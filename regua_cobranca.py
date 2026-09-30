# ============================================================================
# ALTERAÇÃO 2026-09-27: padronização 7 status — 7 status + contagem de moradores
# ALTERAÇÃO 2026-09-27: re_teste na régua; pagante -> assinatura_status 'ativa' diariamente (com REGUA_GRAVAR_SITUACAO)
# ARQUIVO: regua_cobranca.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Régua de cobrança diária. Classifica os condomínios pela fonte única
#            (app/services/assinatura_situacao.py), lista boletos a gerar
#            (6 dias antes da validade, vencimento em 10 dias), lembretes,
#            débitos/desativações e monta o relatório interno.
#            Nesta versão SÓ roda em dry-run: não gera boleto, não envia nada,
#            não grava no banco (sessão MySQL READ ONLY).
# USO:  venv/bin/python3 regua_cobranca.py                 (banco do .env)
#       venv/bin/python3 regua_cobranca.py --banco AdmGeral  (leitura de produção)
# CRON (sugerido, NÃO instalado):
#   15 8 * * * flock -n /tmp/regua_cobranca.lock <venv>/bin/python3 <pasta>/regua_cobranca.py >> <pasta>/regua_cobranca.log 2>&1
# VERSÃO: 2.0.0 - modo real (REGUA_DRY_RUN=false): 1º boleto de quem está em teste (venc +10d, NF
#                 antes se marcado, e-mail+WhatsApp), lembretes D-3/D0/D+1, relatório por e-mail
#                 (REGUA_ENVIAR_RELATORIO). Pagantes seguem no gerar_cobrancas_renovacao.py.
#         1.2.0 - grava condominios.situacao_assinatura (flag REGUA_GRAVAR_SITUACAO, só mudanças)
#         1.1.0 - pagante = tem cobrança paga
#         1.0.0 - criação (dry-run apenas)
# data criação: 2026-09-26 data alteração: 2026-09-26
# ============================================================================

import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import pymysql
from dotenv import load_dotenv

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)
load_dotenv(os.path.join(PASTA, ".env"))

from app.services.assinatura_situacao import (  # noqa: E402
    AVISO_ANTES_DIAS,
    BOLETO_VALIDADE_DIAS,
    CAMPOS_CADASTRO_OBRIGATORIOS,
    calcular_situacao,
)

DIAS_LEMBRETE = (3, 0, -1)          # dias relativos à validade_ate
DIAS_SEM_MORADOR_ALERTA = 2         # cadastrados há >= X dias com 0 moradores
PAGO_RECENTE_DIAS = 10              # pagamento recente: webhook já estende a validade

DRY_RUN = os.getenv("REGUA_DRY_RUN", "true").strip().lower() not in ("0", "false", "no")
GRAVAR_SITUACAO = os.getenv("REGUA_GRAVAR_SITUACAO", "false").strip().lower() in ("1", "true", "yes")


def conectar(banco: str, somente_leitura: bool = True):
    conn = pymysql.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        database=banco, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    if somente_leitura:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
    return conn


def carregar(conn):
    campos = ", ".join(CAMPOS_CADASTRO_OBRIGATORIOS)
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM condominios LIKE 'situacao_assinatura'")
        col_sit = "situacao_assinatura" if cur.fetchone() else "NULL AS situacao_assinatura"
        cur.execute(f"""
            SELECT id, nome, validade_ate, assinatura_status, bonificado, data_fim_bonificado,
                   data_cadastro, nfe_antes_pagamento, cnpj, email_financeiro, asaas_customer_id,
                   valor_plano_final, {col_sit}, {campos}
            FROM condominios WHERE ativo = 1 ORDER BY id""")
        conds = cur.fetchall()
        cur.execute("""SELECT id_condominio, id_cobranca, data_vencimento, valor, asaas_payment_id, descricao
                       FROM cobrancas WHERE status = 'pendente'""")
        pend = defaultdict(list)
        for r in cur.fetchall():
            pend[r["id_condominio"]].append(r)
        cur.execute("""SELECT id_condominio, MAX(data_pagamento) ult FROM cobrancas
                       WHERE status = 'pago' GROUP BY id_condominio""")
        pagos = {r["id_condominio"]: r["ult"] for r in cur.fetchall()}
        cur.execute("SELECT condominio_id, COUNT(*) n FROM moradores WHERE ativo = 1 GROUP BY condominio_id")
        moradores = {r["condominio_id"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT condominio_id, COUNT(*) n FROM encomendas GROUP BY condominio_id")
        encomendas = {r["condominio_id"]: r["n"] for r in cur.fetchall()}
    return conds, pend, pagos, moradores, encomendas


def _d(v):
    return v.date() if hasattr(v, "date") and callable(v.date) else v


def _cond_dict(c: dict) -> dict:
    campos = ("id", "nome", "email", "email_financeiro", "cobranca_email", "telefone",
              "cobranca_whats", "cnpj", "asaas_customer_id")
    d = {k: c.get(k) for k in campos}
    d["cnpj"] = d["cnpj"] or ""
    return d


async def _emitir_nf_antes(id_cobranca: int) -> None:
    import aiomysql
    from financeiro.financeiro_nfe import emitir_nf, DB_CONFIG
    nf_conn = await aiomysql.connect(**DB_CONFIG)
    try:
        print(f"  NF antes do pagamento cobranca={id_cobranca}: {await emitir_nf(nf_conn, id_cobranca)}")
    finally:
        nf_conn.close()


async def gerar_boleto_teste(c: dict, vencimento: date) -> str:
    """Primeiro boleto de quem está em teste: plano mensal, reaproveitando o fluxo do /gerar-cobranca."""
    from sqlalchemy import text
    from app.database import SessionLocal
    from mobile.assinatura_routes import (buscar_ou_criar_cliente_asaas, calcular_valor_plano,
                                          criar_cobranca_asaas, enviar_email_cobranca,
                                          enviar_whatsapp_cobranca, fmt_data)
    db = SessionLocal()
    try:
        cond = _cond_dict(c)
        valor = float(c["valor_plano_final"] or 0) or calcular_valor_plano(c["total_apartamentos"] or 0, 30)[1]
        customer_id = await buscar_ou_criar_cliente_asaas(cond, db)
        if not customer_id:
            raise RuntimeError("não foi possível criar/buscar o cliente no Asaas")
        cond["asaas_customer_id"] = customer_id
        venc = vencimento.isoformat()
        res = await criar_cobranca_asaas(cond, valor, 30, customer_id, db, data_vencimento=venc)
        if not res.get("success"):
            raise RuntimeError(res.get("error", "erro ao criar cobrança"))
        if c["nfe_antes_pagamento"]:
            row = db.execute(text("SELECT id_cobranca FROM cobrancas WHERE asaas_payment_id = :p"),
                             {"p": res["asaas_payment_id"]}).fetchone()
            if row:
                await _emitir_nf_antes(row.id_cobranca)
        base = max(c["validade_ate"] or date.today(), date.today())
        nova = fmt_data(base + timedelta(days=30))
        enviar_email_cobranca(cond=cond, valor=valor, descricao=res["descricao"], link_pagamento=res["link_pagamento"],
                              data_vencimento_boleto=fmt_data(venc), validade_atual=fmt_data(c["validade_ate"]),
                              nova_validade=nova)
        enviar_whatsapp_cobranca(cond=cond, valor=valor, descricao=res["descricao"], link_pagamento=res["link_pagamento"],
                                 data_vencimento_boleto=fmt_data(venc), nova_validade=nova)
        return res["link_pagamento"]
    finally:
        db.close()


def enviar_lembrete(c: dict, p: dict) -> None:
    from mobile.assinatura_routes import (enviar_email_cobranca, enviar_whatsapp_cobranca,
                                          fmt_data, gerar_link_pagamento)
    cond = _cond_dict(c)
    link = gerar_link_pagamento(p["asaas_payment_id"])
    kw = dict(cond=cond, valor=float(p["valor"]), descricao=p["descricao"] or "Assinatura eCondomínio",
              link_pagamento=link, data_vencimento_boleto=fmt_data(p["data_vencimento"]),
              nova_validade="conforme o plano contratado")
    enviar_email_cobranca(validade_atual=fmt_data(c["validade_ate"]), **kw)
    enviar_whatsapp_cobranca(**kw)


def enviar_relatorio(texto: str) -> None:
    import smtplib
    from email.mime.text import MIMEText
    from mobile.assinatura_routes import SMTP_FROM, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USER
    destino = os.getenv("REGUA_EMAIL_RELATORIO", "financeiro@econdominio.com.br")
    msg = MIMEText(texto, "plain", "utf-8")
    msg["Subject"] = f"Régua de cobrança — {date.today():%d/%m/%Y}"
    msg["From"], msg["To"] = SMTP_FROM, destino
    import ssl
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
        srv.starttls(context=ssl.create_default_context())
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(SMTP_FROM, [destino], msg.as_string())


def sincronizar_pagantes(banco: str) -> list:
    """Quem já pagou é Ativo: assinatura_status trial/re_teste -> ativa (rede de segurança do webhook)."""
    conn = conectar(banco, somente_leitura=False)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, nome FROM condominios c
                           WHERE c.assinatura_status IN ('trial', 're_teste')
                             AND EXISTS (SELECT 1 FROM cobrancas p WHERE p.id_condominio = c.id AND p.status = 'pago')""")
            rows = cur.fetchall()
            for r in rows:
                cur.execute("UPDATE condominios SET assinatura_status = 'ativa' WHERE id = %s", (r["id"],))
        conn.commit()
        return [f"#{r['id']} {r['nome']}" for r in rows]
    finally:
        conn.close()


def gravar_mudancas(banco: str, mudancas: list) -> None:
    conn = conectar(banco, somente_leitura=False)
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE condominios SET situacao_assinatura = %s, situacao_atualizada_em = NOW() WHERE id = %s",
                [(depois, cid) for cid, _nome, _antes, depois in mudancas])
        conn.commit()
    finally:
        conn.close()


def executar(banco: str, hoje: date, gravar: bool = False):
    conn = conectar(banco)
    try:
        conds, pend, pagos, moradores, encomendas = carregar(conn)
    finally:
        conn.close()

    r = defaultdict(list)
    dist = Counter()
    mudancas = []
    for c in conds:
        sit = calcular_situacao(c["validade_ate"], c["assinatura_status"], bool(c["bonificado"]),
                                c["data_fim_bonificado"], {k: c[k] for k in CAMPOS_CADASTRO_OBRIGATORIOS}, hoje,
                                ja_pagou=c["id"] in pagos, n_moradores=moradores.get(c["id"], 0))
        st, dias = sit["status"], sit["dias_restantes"]
        dist[st] += 1
        if c["situacao_assinatura"] != st:
            mudancas.append((c["id"], c["nome"], c["situacao_assinatura"], st))
        if st == "sistema":
            continue
        cid, nome = c["id"], c["nome"]
        n_mor, n_enc = moradores.get(cid, 0), encomendas.get(cid, 0)
        pendentes = pend.get(cid, [])
        ult_pago = _d(pagos.get(cid))
        tag = f"#{cid} {nome} | {st} | validade {c['validade_ate']} ({dias:+d}d) | mor={n_mor} enc={n_enc}"

        cad = _d(c["data_cadastro"])
        if cad and (hoje - cad).days >= DIAS_SEM_MORADOR_ALERTA and n_mor == 0 and st in ("em_teste", "teste_estendido", "cadastro_incompleto", "ativo"):
            r["sem_morador"].append(tag)
        if sit["cadastro_incompleto"]:
            r["incompleto"].append(f"{tag} | falta: {', '.join(sit['cadastro_incompleto'])}")

        # A) boleto: 6 dias antes (ou já vencido sem boleto), nunca com pendente, nunca após pagamento recente
        if st in ("em_teste", "teste_estendido", "cadastro_incompleto", "ativo") and dias <= AVISO_ANTES_DIAS:
            if pendentes:
                for p in pendentes:
                    if p["data_vencimento"] and p["data_vencimento"] < hoje:
                        r["pend_vencido"].append(f"{tag} | cobrança {p['id_cobranca']} venceu {p['data_vencimento']} -> prorrogar +2d ao abrir a tela")
            elif ult_pago and (hoje - ult_pago).days <= PAGO_RECENTE_DIAS:
                r["pago_recente"].append(f"{tag} | pago em {ult_pago}")
            elif sit["cadastro_incompleto"]:
                r["sem_boleto_incompleto"].append(tag)
            elif st not in ("em_teste", "teste_estendido", "cadastro_incompleto"):
                # pagantes: boleto sai pelo gerar_cobrancas_renovacao.py (valor por plano + trava)
                r["renovacao"].append(tag)
            else:
                venc = hoje + timedelta(days=BOLETO_VALIDADE_DIAS)
                nf = " + NF antes do pagamento" if c["nfe_antes_pagamento"] else ""
                if DRY_RUN:
                    r["boletos"].append(f"{tag} | vencimento {venc}{nf}")
                else:
                    try:
                        link = asyncio.run(gerar_boleto_teste(c, venc))
                        r["boletos"].append(f"{tag} | vencimento {venc}{nf} | {link}")
                    except Exception as e:
                        r["erros"].append(f"{tag} | boleto: {e}")

        # B) lembretes só para quem tem cobrança pendente
        if dias in DIAS_LEMBRETE and pendentes:
            for p in pendentes:
                if not DRY_RUN:
                    try:
                        enviar_lembrete(c, p)
                    except Exception as e:
                        r["erros"].append(f"{tag} | lembrete cobrança {p['id_cobranca']}: {e}")
                        continue
                r["lembretes"].append(f"{tag} | lembrete D{dias:+d} | cobrança {p['id_cobranca']}")

        if st == "ativo" and dias < 0:
            r["em_debito"].append(f"{tag} | em atraso" + (f", cancela em {sit['dias_para_desativar']}d" if sit['dias_para_desativar'] else ""))
        if st != "sistema" and not sit["pode_receber"]:
            r["sem_receber"].append(f"{tag} | {'cadastro incompleto' if sit['cadastro_incompleto'] else st}")
        if st in ("nao_convertido", "cancelado") and n_enc == 0:
            r["vencido_sem_uso"].append(tag)

    if gravar and mudancas:
        gravar_mudancas(banco, mudancas)
    promovidos = sincronizar_pagantes(banco) if gravar else []

    secoes = [
        ("Distribuição por situação", [f"{k}: {v}" for k, v in sorted(dist.items())]),
        ("0b. Pagantes promovidos a Ativo (assinatura_status -> ativa)", promovidos),
        ("0. Mudanças de situação (" + ("GRAVADAS" if gravar else "não gravadas") + ")",
         [f"#{cid} {nome}: {antes or '(vazio)'} -> {depois}" for cid, nome, antes, depois in mudancas]),
        (f"1. Cadastrados há >= {DIAS_SEM_MORADOR_ALERTA} dias com 0 moradores (ligar para ativar)", r["sem_morador"]),
        ("2. Boletos que SERIAM gerados hoje" if DRY_RUN else "2. Boletos gerados hoje", r["boletos"]),
        ("2b. Não geraria: cadastro incompleto (sem e-mail/WhatsApp de cobrança)", r["sem_boleto_incompleto"]),
        ("2c. Não geraria: pagamento recente", r["pago_recente"]),
        ("2e. Pagantes na janela: boleto pelo script de renovação (09h)", r["renovacao"]),
        ("ERROS", r["erros"]),
        ("2d. Cobrança pendente já vencida", r["pend_vencido"]),
        ("3. Lembretes que SERIAM enviados" if DRY_RUN else "3. Lembretes enviados", r["lembretes"]),
        ("4. Pagantes em atraso", r["em_debito"]),
        ("5. Sem permissão de RECEBER encomendas (se a flag de bloqueio estivesse ligada)", r["sem_receber"]),
        ("6. Não convertidos/cancelados sem nenhuma encomenda (nunca usaram)", r["vencido_sem_uso"]),
        ("Cadastro incompleto (todos)", r["incompleto"]),
    ]
    linhas = [f"RÉGUA DE COBRANÇA — {hoje} — banco {banco} — {'DRY-RUN' if DRY_RUN else 'REAL'}"]
    for titulo, itens in secoes:
        linhas.append(f"\n== {titulo} ({len(itens)})")
        linhas.extend(f"  {i}" for i in itens) if itens else linhas.append("  —")
    return "\n".join(linhas)


def main():
    banco = os.getenv("DB_NAME")
    if "--banco" in sys.argv:
        banco = sys.argv[sys.argv.index("--banco") + 1]
    if "--banco" in sys.argv and not DRY_RUN:
        sys.exit("--banco só é permitido em dry-run (leitura).")
    # --banco (ex.: leitura da produção) nunca grava, mesmo com a flag ligada
    gravar = GRAVAR_SITUACAO and "--banco" not in sys.argv
    relatorio = executar(banco, date.today(), gravar)
    print(relatorio)
    if os.getenv("REGUA_ENVIAR_RELATORIO", "false").strip().lower() in ("1", "true", "yes"):
        try:
            enviar_relatorio(relatorio)
            print("\nRelatório enviado por e-mail.")
        except Exception as e:
            print(f"\nFalha ao enviar relatório por e-mail: {e}")


if __name__ == "__main__":
    main()
