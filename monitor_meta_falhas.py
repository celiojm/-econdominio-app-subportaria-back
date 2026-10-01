# ============================================================================
# ARQUIVO: monitor_meta_falhas.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Alerta quando a Meta passa a RECUSAR avisos de WhatsApp em massa (ex.: 01/10/2026,
#            erro 131042 "pagamento pendente" das 06:30 às 10:18 — 226 avisos perdidos sem ninguém
#            perceber, porque a fila marca "sent" quando a Meta aceita o pedido; a recusa só chega
#            depois, no webhook). Lê os retornos ("Statuses recebidos") do log do serviço 5002.
#            Alerta: nos últimos 15 min, um mesmo código de erro com >= 5 recusas e recusas >= 50%
#            dos retornos. Repete a cada 2h enquanto durar; avisa quando normalizar.
#            Canais: e-mail (SMTP do .env) + WhatsApp pelo nosso servidor (vps60688, rota Z-API) —
#            não depende da Meta.
# USO: cron */10 com .env.worker | --teste envia um alerta de teste nos dois canais
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-01 data alteração: 2026-10-01
# ============================================================================
import ast, json, os, re, sys, time
from collections import Counter
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, ".env.worker"))
load_dotenv(os.path.join(BASE, ".env"))  # complementa o que faltar (não sobrescreve)
import httpx

LOG = os.path.join(BASE, "logs", "app_subportaria_back-error.log")
ESTADO = os.path.join(BASE, ".monitor_meta_estado.json")
JANELA_MIN = 15
MIN_RECUSAS = 5
TAXA_MIN = 0.5
REPETIR_H = 2
EMAIL_DESTINO = os.getenv("ALERTA_META_EMAIL", "celiojm@gmail.com")
WHATS_DESTINO = os.getenv("ALERTA_META_WHATSAPP", "5548984046118")


def agora():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def ler_retornos(janela_s):
    """Lê o fim do log (até 8 MB) e devolve [(timestamp, status, codigo, titulo)] da janela."""
    limite = time.time() - janela_s
    with open(LOG, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 8 * 1024 * 1024))
        texto = f.read().decode("utf-8", "replace")
    saida = []
    for linha in texto.splitlines():
        i = linha.find("Statuses recebidos: ")
        if i < 0:
            continue
        try:
            lista = ast.literal_eval(linha[i + len("Statuses recebidos: "):])
        except Exception:
            continue
        for st in lista:
            ts = int(st.get("timestamp") or 0)
            if ts < limite:
                continue
            err = (st.get("errors") or [{}])[0]
            saida.append((ts, st.get("status"), err.get("code"), err.get("title") or err.get("message")))
    return saida


def enviar_email(assunto, texto):
    try:
        from financeiro.financeiro_email import enviar_email as _env
        r = _env(EMAIL_DESTINO, assunto, "<pre style='font-family:sans-serif'>" + texto + "</pre>", texto)
        return bool(r and r.get("success", True))
    except Exception as e:
        print("e-mail falhou:", e)
        return False


def enviar_whatsapp(texto):
    url, inst, tok = os.getenv("ZAPI_API_URL"), os.getenv("ZAPI_INSTANCE_ID"), os.getenv("ZAPI_TOKEN")
    if not (url and inst and tok):
        print("WhatsApp: ZAPI_* ausentes no .env")
        return False
    try:
        r = httpx.post(f"{url.rstrip('/')}/instances/{inst}/token/{tok}/send-text",
                       headers={"Client-Token": os.getenv("ZAPI_CLIENT_TOKEN", ""), "Content-Type": "application/json"},
                       json={"phone": WHATS_DESTINO, "message": texto}, timeout=30)
        ok = r.status_code == 200 and r.json().get("success")
        if not ok:
            print("WhatsApp falhou: HTTP", r.status_code)  # não imprime a URL (tem token)
        return bool(ok)
    except Exception as e:
        print("WhatsApp falhou:", type(e).__name__)
        return False


def alertar(assunto, texto):
    e = enviar_email(assunto, texto)
    w = enviar_whatsapp(f"*{assunto}*\n\n{texto}")
    print(f"{agora()} ALERTA enviado: {assunto} | e-mail={'ok' if e else 'FALHOU'} whatsapp={'ok' if w else 'FALHOU'}")


def main():
    if "--teste" in sys.argv:
        alertar("TESTE - alerta de WhatsApp Meta",
                f"Mensagem de teste do monitor de recusas da Meta ({agora()}). Se chegou, o alerta está funcionando.")
        return

    try:
        estado = json.load(open(ESTADO))
    except Exception:
        estado = {}
    rets = ler_retornos(JANELA_MIN * 60)
    total = len(rets)
    falhas = Counter((c, t) for _, s, c, t in rets if s == "failed")
    n_falhas = sum(falhas.values())
    entregues = sum(1 for _, s, _, _ in rets if s in ("delivered", "read"))
    agora_s = time.time()

    for (codigo, titulo), n in falhas.items():
        chave = str(codigo)
        if n >= MIN_RECUSAS and total and n_falhas / total >= TAXA_MIN:
            if agora_s - estado.get(chave, {}).get("ultimo_alerta", 0) >= REPETIR_H * 3600:
                alertar(f"ALERTA: Meta recusando WhatsApp (erro {codigo})",
                        f"{agora()} - nos últimos {JANELA_MIN} min a Meta recusou {n} aviso(s) com o erro "
                        f"{codigo} \"{titulo}\" ({n_falhas} recusas em {total} retornos; {entregues} entregues).\n\n"
                        "Os moradores NÃO estão recebendo esses avisos de encomenda. Erro 131042 = pagamento "
                        "pendente na conta WhatsApp Business (Meta > Faturamento).\n\n"
                        "Depois de resolvido, os avisos recusados precisam ser recolocados na fila.")
                estado[chave] = {"ultimo_alerta": agora_s, "titulo": titulo, "inicio": estado.get(chave, {}).get("inicio", agora_s)}

    # normalizou: código alertado sem recusas na janela e com entregas acontecendo
    for chave in [k for k in estado if k not in {str(c) for (c, _) in falhas}]:
        if entregues >= 3:
            ini = datetime.fromtimestamp(estado[chave].get("inicio", agora_s)).strftime("%d/%m %H:%M")
            alertar(f"NORMALIZADO: Meta voltou a entregar (erro {chave})",
                    f"{agora()} - nos últimos {JANELA_MIN} min não houve recusa com o erro {chave} e "
                    f"{entregues} aviso(s) foram entregues. Problema começou em {ini}.\n\n"
                    "Lembrete: recolocar na fila os avisos recusados nesse período (encomendas ainda pendentes).")
            del estado[chave]

    json.dump(estado, open(ESTADO, "w"))
    print(f"{agora()} retornos={total} recusas={n_falhas} entregues={entregues} codigos={dict((str(c), n) for (c, _), n in falhas.items())}")


if __name__ == "__main__":
    main()
