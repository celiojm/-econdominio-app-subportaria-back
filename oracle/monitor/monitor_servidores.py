#!/usr/bin/env python3
# ============================================================================
# ARQUIVO: monitor_servidores.py
# PASTA: /home/ubuntu/backend/monitor/ (oracle_backend) e /home/ubuntu/ (oracle_ngnix, modo --vigia-backend)
# DESCRIÇÃO: Monitor dos servidores de produção na Oracle (nginx, backend, mysql). A cada minuto (cron):
#            CPU > 80% por 10 min, RAM > 85% por 10 min, disco > 80%, carga (load 5 min) > nº de núcleos por
#            10 min, servidor sem resposta (2 checagens seguidas), serviço/saúde fora (nginx, cloudflared,
#            backend 5002, MySQL, sites públicos) → WhatsApp. Repete a cada 1 h enquanto durar; avisa ao normalizar.
#            --relatorio (cron 09:00): estado de CPU, RAM, carga, disco, rede (desde o relatório anterior) e saúde.
#            --vigia-backend (no oracle_ngnix): só confere se o oracle_backend responde (o monitor principal
#            roda nele — se ele cair, quem avisa é o vigia).  --teste: manda mensagem de teste.
#            WhatsApp pelo servidor do funil (ZAPI_API_URL/INSTANCE_ID/TOKEN/CLIENT_TOKEN do .env). Só biblioteca padrão.
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-03 data alteração: 2026-10-03
# ============================================================================
import json, os, subprocess, sys, time, urllib.request, urllib.error, ssl
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
ENV_ARQS = ["/home/ubuntu/backend/.env.worker", "/home/ubuntu/backend/.env", "/home/ubuntu/.monitor_zapi.env"]
ESTADO = os.path.join(AQUI, ".monitor_servidores_estado.json")
DESTINO = os.getenv("MONITOR_WHATSAPP", "5548984046118")
CPU_MAX, RAM_MAX, DISCO_MAX, MINUTOS = 80, 85, 80, 10
REPETIR_S = 3600
COLETA = "/home/ubuntu/monitor_coleta.sh"
CHAVE = "/home/ubuntu/.ssh/id_monitor"
SERVIDORES = {  # nome: (ip para ssh ou None = local, serviços esperados)
    "nginx":   ("10.250.0.1",   ["nginx", "cloudflared"]),  # WireGuard (SSH do nginx só aceita 10.250.0.0/24)
    "backend": (None,           ["app_subportaria_back"]),
    "mysql":   ("10.200.2.30",  ["mysql"]),
}
SITES = ["https://portaria.econdominio.com.br/", "https://admin.econdominio.com.br/", "https://financeiro.econdominio.com.br/"]


def env():
    v = {}
    for arq in ENV_ARQS:
        try:
            for l in open(arq):
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, x = l.split("=", 1)
                    v.setdefault(k.strip(), x.strip().strip('"').strip("'"))
        except OSError:
            pass
    return v


def whatsapp(texto):
    e = env()
    url, inst, tok = e.get("ZAPI_API_URL"), e.get("ZAPI_INSTANCE_ID"), e.get("ZAPI_TOKEN")
    if not (url and inst and tok):
        print("WhatsApp: ZAPI_* ausentes"); return False
    req = urllib.request.Request(f"{url.rstrip('/')}/instances/{inst}/token/{tok}/send-text",
                                 data=json.dumps({"phone": DESTINO, "message": texto}).encode(),
                                 headers={"Content-Type": "application/json", "Client-Token": e.get("ZAPI_CLIENT_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = json.loads(r.read() or b"{}").get("success")
    except Exception as ex:
        print("WhatsApp falhou:", type(ex).__name__); return False   # não imprime a URL (tem token)
    print("WhatsApp:", "ok" if ok else "falhou"); return bool(ok)


def coletar(nome):
    ip, _ = SERVIDORES[nome]
    cmd = [COLETA] if ip is None else ["ssh", "-i", CHAVE, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                       "-o", "StrictHostKeyChecking=accept-new", f"ubuntu@{ip}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return None
    d = dict(l.split("=", 1) for l in out.split() if "=" in l)
    return d if "cpu" in d else None


def http_ok(url, timeout=15):
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "monitor-econdominio"}), timeout=timeout, context=ctx) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def mysql_ok():
    try:
        r = subprocess.run(["mysql", "--defaults-file=/home/ubuntu/.my_econdo.cnf", "-N", "-e", "SELECT 1"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() == "1"
    except Exception:
        return False


def carregar():
    try:
        return json.load(open(ESTADO))
    except Exception:
        return {"inicio": {}, "alertado": {}, "falhas_acesso": {}, "rede_ref": {}}


def salvar(st):
    json.dump(st, open(ESTADO, "w"))


def avaliar(st, chave, condicao, minutos, texto_alerta, agora):
    """Retorna mensagem de alerta/normalizado ou None. minutos=0 → imediato."""
    ini = st["inicio"]; al = st["alertado"]
    if condicao:
        ini.setdefault(chave, agora)
        if agora - ini[chave] >= minutos * 60 and agora - al.get(chave, 0) >= REPETIR_S:
            al[chave] = agora
            dur = int((agora - ini[chave]) / 60)
            return f"🔴 {texto_alerta}" + (f" (há {dur} min)" if dur else "")
    else:
        ini.pop(chave, None)
        if al.pop(chave, None):
            return f"✅ Normalizado: {chave.replace('|', ' — ')}"
    return None


def checar():
    st = carregar(); agora = time.time(); msgs = []
    dados = {n: coletar(n) for n in SERVIDORES}
    for n, d in dados.items():
        fa = st["falhas_acesso"]
        if d is None:
            fa[n] = fa.get(n, 0) + 1
        else:
            fa[n] = 0
        m = avaliar(st, f"{n}|sem resposta", fa[n] >= 2, 0, f"Servidor {n.upper()} sem resposta", agora)
        if m: msgs.append(m)
        if d is None:
            continue
        nuc = int(d.get("nucleos", 1))
        for chave, cond, mins, txt in [
            ("CPU", int(d["cpu"]) > CPU_MAX, MINUTOS, f"{n.upper()}: CPU {d['cpu']}% (> {CPU_MAX}%)"),
            ("RAM", int(d["ram"]) > RAM_MAX, MINUTOS, f"{n.upper()}: RAM {d['ram']}% (> {RAM_MAX}%)"),
            ("disco", int(d["disco"]) > DISCO_MAX, 0, f"{n.upper()}: disco {d['disco']}% (> {DISCO_MAX}%, livre {d.get('disco_livre_gb')} GB)"),
            ("carga", float(d["load5"]) > nuc, MINUTOS, f"{n.upper()}: carga {d['load5']} (> {nuc} núcleos)"),
        ]:
            m = avaliar(st, f"{n}|{chave}", cond, mins, txt, agora)
            if m: msgs.append(m)
        for s in SERVIDORES[n][1]:
            v = d.get(f"svc_{s}", "ausente")
            m = avaliar(st, f"{n}|serviço {s}", v != "active", 2, f"{n.upper()}: serviço {s} = {v}", agora)
            if m: msgs.append(m)
    saude = {"backend 5002": http_ok("http://localhost:5002/docs", 10), "MySQL (consulta)": mysql_ok()}
    saude.update({u.split("//")[1].split(".")[0]: http_ok(u) for u in SITES})
    for k, ok in saude.items():
        m = avaliar(st, f"saúde|{k}", not ok, 2, f"Saúde: {k} FORA", agora)
        if m: msgs.append(m)
    salvar(st)
    if msgs:
        whatsapp("*Monitor Oracle — eCondomínio*\n" + datetime.now().strftime("%d/%m %H:%M") + "\n\n" + "\n".join(msgs))
    print(datetime.now().strftime("%d/%m %H:%M"), "ok" if not msgs else f"{len(msgs)} aviso(s)")


def gb(b):
    return f"{b / 1073741824:.2f} GB"


def relatorio():
    st = carregar(); agora = time.time(); linhas = []
    for n in SERVIDORES:
        d = coletar(n)
        if d is None:
            linhas.append(f"🔴 *{n.upper()}*: sem resposta"); continue
        rx, tx = int(d["rede_rx"]), int(d["rede_tx"])
        ref = st["rede_ref"].get(n)
        if ref and rx >= ref[0] and tx >= ref[1]:
            rede = f"↓{gb(rx - ref[0])} ↑{gb(tx - ref[1])} (desde {datetime.fromtimestamp(ref[2]).strftime('%d/%m %H:%M')})"
        else:
            rede = "sem referência (primeiro relatório ou reinício)"
        st["rede_ref"][n] = [rx, tx, agora]
        svcs = ", ".join(f"{s} {'✅' if d.get('svc_' + s) == 'active' else '🔴 ' + d.get('svc_' + s, 'ausente')}" for s in SERVIDORES[n][1])
        alerta = any([int(d["cpu"]) > CPU_MAX, int(d["ram"]) > RAM_MAX, int(d["disco"]) > DISCO_MAX, float(d["load5"]) > int(d["nucleos"])])
        dias = int(d["uptime_s"]) // 86400
        linhas.append(f"{'🟡' if alerta else '🟢'} *{n.upper()}* (ligado há {dias}d)\n"
                      f"  CPU {d['cpu']}% · RAM {d['ram']}% de {int(d['ram_total_mb']) // 1024 or 1} GB · carga {d['load1']}/{d['load5']}/{d['load15']} ({d['nucleos']} núcleos)\n"
                      f"  Disco {d['disco']}% (livre {d['disco_livre_gb']} GB) · Rede {rede}\n  Serviços: {svcs}")
    saude = {"backend 5002": http_ok("http://localhost:5002/docs", 10), "MySQL": mysql_ok()}
    saude.update({u.split("//")[1].split(".")[0]: http_ok(u) for u in SITES})
    linhas.append("*Saúde:* " + " · ".join(f"{k} {'✅' if ok else '🔴'}" for k, ok in saude.items()))
    salvar(st)
    whatsapp("*Estado dos servidores Oracle — eCondomínio*\n" + datetime.now().strftime("%d/%m/%Y %H:%M") + "\n\n" + "\n\n".join(linhas))


def vigia_backend():
    """Roda no oracle_ngnix: avisa se o oracle_backend (onde fica o monitor principal) não responde."""
    st = carregar(); agora = time.time()
    ok = http_ok("http://10.250.0.3:5002/docs", 10)
    fa = st["falhas_acesso"]; fa["backend"] = 0 if ok else fa.get("backend", 0) + 1
    m = avaliar(st, "backend|sem resposta (vigia no nginx)", fa["backend"] >= 2, 0,
                "Servidor BACKEND sem resposta (visto pelo NGINX) — o monitor principal roda nele", agora)
    salvar(st)
    if m:
        whatsapp("*Monitor Oracle — eCondomínio*\n" + datetime.now().strftime("%d/%m %H:%M") + "\n\n" + m)
    print(datetime.now().strftime("%d/%m %H:%M"), "backend ok" if ok else "backend SEM RESPOSTA")


if __name__ == "__main__":
    if "--teste" in sys.argv:
        whatsapp("✅ Teste do monitor dos servidores Oracle (eCondomínio) — " + datetime.now().strftime("%d/%m %H:%M"))
    elif "--relatorio" in sys.argv:
        relatorio()
    elif "--vigia-backend" in sys.argv:
        vigia_backend()
    else:
        checar()
