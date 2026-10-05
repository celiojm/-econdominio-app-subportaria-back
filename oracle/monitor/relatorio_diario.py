# ==============================================================================
# ARQUIVO: relatorio_diario.py
# PASTA: /home/ubuntu/backend/monitor/
# DESCRIÇÃO: Relatório diário — WhatsApp, moradores, condomínios, financeiro,
#            despesas via Asaas.
# VERSÃO: 2.3.0
#   - WhatsApp (enviados/falhas/encomendas): lido direto de
#     whatsapp_message_queue, não mais de whatsapp_estatisticas (stored
#     procedure sp_atualizar_estatisticas_whatsapp confirmada com bug em
#     2026-07-05 — não atualiza a tabela).
#   - WhatsApp (respostas): lido direto de whatsapp_inbound_messages, mesmo
#     motivo. Sem quebra por tipo de resposta (tabela não tem essa coluna).
#   - Condomínios: "pagantes" via assinaturas.validade_ate >= hoje (validado
#     em 2026-07-05 contra 3 fontes independentes — bateu 7/7). Versões
#     anteriores usavam assinatura_status='ativa', que incluía clientes sem
#     nenhum pagamento real (bug: reportava 28, correto é 7).
#   - Financeiro: recebimentos e despesas agora cobrem um PERÍODO, não só
#     "hoje" — ontem+hoje em dias normais, semana inteira (seg a sáb) quando
#     o relatório roda num sábado (não roda domingo). Evita perder pagamento
#     feito à noite ou atraso de sync do Asaas. Ver periodo_relatorio().
#   - Despesas: seção "Pagamentos pela eCondomínio", lendo de contas_a_pagar
#     (populada por sync_despesas_asaas.py, que sincroniza o endpoint Asaas
#     /v3/bill — rodar esse sync separadamente, não faz parte deste script).
#   - Removida seção "Funil de Contatos" (contato_condominios.status não é
#     atualizado após o cadastro inicial — dado não confiável).
#   - Removidos tokens Z-API hardcoded como fallback no código-fonte.
# CRIAÇÃO: 2026-06-01   ALTERAÇÃO: 2026-07-05
# CRONTAB: 0 8 * * * e 0 16 * * *
# ==============================================================================

import os
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

for _env in [
    "/home/ubuntu/backend/.env.worker",
    "/home/ubuntu/backend/.env",
]:
    if os.path.exists(_env):
        load_dotenv(_env, override=False)
        break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("relatorio_diario")

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "")

ZAPI_API_URL      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080").rstrip("/")
ZAPI_INSTANCE_ID  = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN        = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_ID     = os.getenv("META_PHONE_NUMBER_ID", "")
META_API_VERSION  = os.getenv("META_API_VERSION", "v25.0")

ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY", "")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://api.asaas.com/v3")

RELATORIO_DESTINOS = os.getenv("RELATORIO_WHATSAPP_DESTINOS", "5548984046118").split(",")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurado no .env")

if not ZAPI_TOKEN or not ZAPI_INSTANCE_ID:
    logger.warning("ZAPI_INSTANCE_ID/ZAPI_TOKEN não configurados no .env — envio via WhatsApp vai falhar")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

# ------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------

def db_query(sql: str, params: dict = {}) -> list:
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]


def fmt_brl(valor) -> str:
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def fmt_pct(parte, total) -> str:
    try:
        if not total:
            return "0%"
        return f"{100 * parte / total:.0f}%"
    except Exception:
        return "0%"


def hoje_str() -> str:
    return date.today().strftime("%d/%m/%Y")


def hora_str() -> str:
    return datetime.now().strftime("%H:%M")


def periodo_relatorio() -> tuple:
    """
    Define o intervalo de datas para as listagens de recebimentos/despesas.
    Regra: normalmente ontem + hoje (cobre atraso de sync do Asaas e
    pagamentos feitos após o horário de corte do dia anterior).
    Aos sábados: semana inteira (segunda a sábado), já que o relatório
    não roda domingo.
    """
    hoje = date.today()
    if hoje.weekday() == 5:  # 5 = sábado
        inicio = hoje - timedelta(days=hoje.weekday())  # segunda-feira desta semana
        label = f"semana ({inicio.strftime('%d/%m')} a {hoje.strftime('%d/%m')})"
    else:
        inicio = hoje - timedelta(days=1)
        label = f"{inicio.strftime('%d/%m')} e {hoje.strftime('%d/%m')}"
    return inicio, hoje, label

# ------------------------------------------------------------------------------
# 1. WHATSAPP — estatísticas do dia (direto da fila real, não de estatísticas
#    pré-agregadas — ver nota de versão no topo do arquivo)
# ------------------------------------------------------------------------------

def stats_whatsapp() -> dict:
    fila = db_query("""
        SELECT
            SUM(CASE WHEN status = 'sent'   THEN 1 ELSE 0 END) AS enviados,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS falhas,
            SUM(CASE WHEN tipo_evento = 'ENCOMENDA_RECEBIDA' AND status = 'sent'
                     THEN 1 ELSE 0 END) AS encomendas
        FROM whatsapp_message_queue
        WHERE DATE(criado_em) = CURDATE()
    """)
    base = fila[0] if fila else {}

    resp = db_query("""
        SELECT COUNT(*) AS respostas
        FROM whatsapp_inbound_messages
        WHERE DATE(created_at) = CURDATE()
    """)
    base['respostas'] = int(resp[0].get('respostas', 0)) if resp else 0
    # Sem quebra por tipo — whatsapp_inbound_messages não tem coluna de classificação
    base['resp_ver'] = base['resp_confirmar'] = base['resp_outras'] = 0
    return base


def stats_fila_pendente() -> dict:
    rows = db_query("""
        SELECT
            COUNT(*) AS pendentes,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS falhas
        FROM whatsapp_message_queue
        WHERE DATE(criado_em) = CURDATE()
          AND status IN ('pending', 'failed')
    """)
    return rows[0] if rows else {}

# ------------------------------------------------------------------------------
# 2. MORADORES — cadastrados hoje
# ------------------------------------------------------------------------------

def stats_moradores() -> dict:
    rows = db_query("""
        SELECT COUNT(*) AS cadastrados_hoje
        FROM moradores
        WHERE DATE(data_cadastro) = CURDATE()
          AND ativo = 1
    """)
    cadastrados = rows[0].get("cadastrados_hoje", 0) if rows else 0

    rows2 = db_query("""
        SELECT COUNT(*) AS total_ativos
        FROM moradores
        WHERE ativo = 1
    """)
    total = rows2[0].get("total_ativos", 0) if rows2 else 0

    rows3 = db_query("""
        SELECT COUNT(*) AS confirmados_hoje
        FROM moradores
        WHERE DATE(whats_confirmado) = CURDATE()
    """)
    confirmados = rows3[0].get("confirmados_hoje", 0) if rows3 else 0

    return {
        "cadastrados_hoje": cadastrados,
        "total_ativos":     total,
        "confirmados_hoje": confirmados,
    }

# ------------------------------------------------------------------------------
# 3. CONDOMÍNIOS — segmentação
#
# Regras validadas em 2026-07-05, confirmadas por 3 fontes independentes
# (cobrancas.pago, assinaturas.validade_ate, cruzamento manual com Asaas):
#  - "pagantes" = presença em `assinaturas` com validade_ate ainda vigente.
#  - assinatura_status='sistema' são contas internas, sempre excluir.
# ------------------------------------------------------------------------------

def stats_condominios() -> dict:
    rows = db_query("""
        SELECT
            COUNT(*) AS total_ativos,
            SUM(CASE WHEN assinatura_status = 'trial' THEN 1 ELSE 0 END) AS trial,
            SUM(CASE WHEN DATE(data_cadastro) = CURDATE() THEN 1 ELSE 0 END) AS novos_hoje
        FROM condominios
        WHERE ativo = 1
          AND assinatura_status != 'sistema'
    """)
    base = rows[0] if rows else {}

    pag = db_query("""
        SELECT COUNT(*) AS pagantes
        FROM assinaturas a
        JOIN condominios c ON c.id = a.id_condominio
        WHERE c.ativo = 1
          AND a.validade_ate >= CURDATE()
    """)
    base['pagantes'] = int(pag[0].get('pagantes', 0)) if pag else 0

    renov = db_query("""
        SELECT c.nome, a.validade_ate
        FROM assinaturas a
        JOIN condominios c ON c.id = a.id_condominio
        WHERE c.ativo = 1
          AND a.validade_ate BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)
        ORDER BY a.validade_ate ASC
    """)
    base['renovacoes_proximas'] = renov

    using = db_query("""
        SELECT COUNT(*) AS ativa_sem_cobranca_paga
        FROM condominios
        WHERE ativo = 1
          AND assinatura_status = 'ativa'
          AND id NOT IN (SELECT id_condominio FROM assinaturas WHERE validade_ate >= CURDATE())
    """)
    base['usando_sem_pagamento'] = int(using[0].get('ativa_sem_cobranca_paga') or 0)
    return base

# ------------------------------------------------------------------------------
# 4. FINANCEIRO — recebimentos (banco local) no período
# ------------------------------------------------------------------------------

def stats_financeiro_periodo(data_ini: date, data_fim: date) -> dict:
    itens = db_query("""
        SELECT c.nome AS condominio, cb.valor AS valor_pago, cb.forma_pagamento, cb.data_pagamento
        FROM cobrancas cb
        LEFT JOIN condominios c ON c.id = cb.id_condominio
        WHERE cb.status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
          AND DATE(cb.data_pagamento) BETWEEN :ini AND :fim
        ORDER BY cb.data_pagamento DESC
    """, {"ini": data_ini, "fim": data_fim})

    total = sum(float(i.get("valor_pago") or 0) for i in itens)
    return {"total": total, "qtd": len(itens), "itens": itens}


def stats_financeiro_semana() -> dict:
    rows = db_query("""
        SELECT COALESCE(SUM(valor), 0) AS total, COUNT(*) AS qtd
        FROM cobrancas
        WHERE status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
          AND YEARWEEK(data_pagamento, 1) = YEARWEEK(CURDATE(), 1)
    """)
    return rows[0] if rows else {"total": 0, "qtd": 0}


def stats_financeiro_mes() -> dict:
    rows = db_query("""
        SELECT COALESCE(SUM(valor), 0) AS total, COUNT(*) AS qtd
        FROM cobrancas
        WHERE status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
          AND YEAR(data_pagamento) = YEAR(CURDATE())
          AND MONTH(data_pagamento) = MONTH(CURDATE())
    """)
    return rows[0] if rows else {"total": 0, "qtd": 0}


def stats_inadimplencia() -> dict:
    rows = db_query("""
        SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS total
        FROM cobrancas
        WHERE status IN ('vencido','OVERDUE')
    """)
    return rows[0] if rows else {"qtd": 0, "total": 0}

# ------------------------------------------------------------------------------
# 5. FINANCEIRO — recebimentos via Asaas API (confirmação externa) + saldo
# ------------------------------------------------------------------------------

def stats_financeiro_asaas() -> dict:
    if not ASAAS_API_KEY:
        return {"erro": "ASAAS_API_KEY não configurada"}

    hoje = date.today().strftime("%Y-%m-%d")
    headers = {"access_token": ASAAS_API_KEY}
    url = f"{ASAAS_BASE_URL}/payments"
    params = {
        "paymentDate": hoje,
        "status":      "RECEIVED,CONFIRMED,RECEIVED_IN_CASH",
        "limit":       100,
        "offset":      0,
    }

    total = 0.0
    qtd = 0
    por_tipo = {}

    try:
        while True:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                logger.warning("Asaas API HTTP %s", r.status_code)
                return {"erro": f"HTTP {r.status_code}"}

            data = r.json()
            items = data.get("data", [])
            if not items:
                break

            for item in items:
                valor = float(item.get("value") or 0)
                tipo  = item.get("billingType", "OUTRO")
                total += valor
                qtd   += 1
                por_tipo[tipo] = por_tipo.get(tipo, 0) + valor

            if not data.get("hasMore"):
                break
            params["offset"] += 100

    except Exception as exc:
        logger.exception("Erro consultando Asaas (payments)")
        return {"erro": str(exc)}

    return {"total": total, "qtd": qtd, "por_tipo": por_tipo}


def saldo_asaas() -> dict:
    if not ASAAS_API_KEY:
        return {"erro": "ASAAS_API_KEY não configurada"}
    try:
        r = requests.get(
            f"{ASAAS_BASE_URL}/finance/balance",
            headers={"access_token": ASAAS_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("Asaas balance HTTP %s", r.status_code)
            return {"erro": f"HTTP {r.status_code}"}
        return {"balance": float(r.json().get("balance") or 0)}
    except Exception as exc:
        logger.exception("Erro consultando Asaas (balance)")
        return {"erro": str(exc)}

# ------------------------------------------------------------------------------
# 6. DESPESAS — pagamentos feitos pela eCondomínio (contas_a_pagar) no período
#    Populada por sync_despesas_asaas.py (roda separado, via cron próprio)
# ------------------------------------------------------------------------------

def stats_despesas_periodo(data_ini: date, data_fim: date) -> list:
    return db_query("""
        SELECT descricao, fornecedor, valor_pago, forma_pagamento, categoria, data_pagamento
        FROM contas_a_pagar
        WHERE status = 'pago'
          AND DATE(data_pagamento) BETWEEN :ini AND :fim
        ORDER BY data_pagamento DESC, valor_pago DESC
    """, {"ini": data_ini, "fim": data_fim})

# ------------------------------------------------------------------------------
# MONTAR MENSAGEM
# ------------------------------------------------------------------------------

def montar_mensagem() -> str:
    wpp   = stats_whatsapp()
    fila  = stats_fila_pendente()
    mor   = stats_moradores()
    cond  = stats_condominios()

    periodo_ini, periodo_fim, periodo_label = periodo_relatorio()
    fin     = stats_financeiro_periodo(periodo_ini, periodo_fim)
    fin_sem = stats_financeiro_semana()
    fin_mes = stats_financeiro_mes()
    inad    = stats_inadimplencia()
    asaas   = stats_financeiro_asaas()
    saldo   = saldo_asaas()
    despesas_periodo = stats_despesas_periodo(periodo_ini, periodo_fim)

    enviados    = int(wpp.get("enviados") or 0)
    falhas_wpp  = int(wpp.get("falhas") or 0)
    encomendas  = int(wpp.get("encomendas") or 0)
    respostas   = int(wpp.get("respostas") or 0)
    pendentes   = int(fila.get("pendentes") or 0)
    falhas_fila = int(fila.get("falhas") or 0)

    lines = []
    lines.append("📊 *Relatório eCondomínio*")
    lines.append(f"📅 {hoje_str()} às {hora_str()}")
    lines.append("")

    # --- WhatsApp ---
    lines.append("💬 *WhatsApp — hoje*")
    lines.append(f"  Enviados:    {enviados}")
    lines.append(f"  Encomendas:  {encomendas}  ({fmt_pct(encomendas, enviados)} do total)")
    lines.append(f"  Falhas:      {falhas_wpp}")
    if pendentes:
        lines.append(f"  ⚠️ Pendentes na fila: {pendentes}")
    if falhas_fila:
        lines.append(f"  ❌ Com erro na fila:  {falhas_fila}")
    lines.append("")
    lines.append(f"  📲 Respostas dos moradores: {respostas}")
    lines.append("")

    # --- Moradores ---
    lines.append("👤 *Moradores*")
    lines.append(f"  Cadastrados hoje:        {mor.get('cadastrados_hoje', 0)}")
    lines.append(f"  Confirmaram WhatsApp:    {mor.get('confirmados_hoje', 0)}")
    lines.append(f"  Total ativos no sistema: {mor.get('total_ativos', 0)}")
    lines.append("")

    # --- Condomínios ---
    lines.append("🏢 *Condomínios*")
    lines.append(f"  💰 Pagantes:                 {cond.get('pagantes', 0)}")
    lines.append(f"  🔵 Ativos sem cobrança paga: {cond.get('usando_sem_pagamento', 0)}")
    lines.append(f"  🟡 Trial:                    {cond.get('trial', 0)}")
    lines.append(f"  Total ativos:                {cond.get('total_ativos', 0)}")
    if int(cond.get("novos_hoje") or 0):
        lines.append(f"  🆕 Novos hoje:               {cond.get('novos_hoje', 0)}")
    for r in cond.get("renovacoes_proximas", []):
        lines.append(f"  ⏰ Renova em breve: {r['nome']} ({r['validade_ate']})")
    lines.append("")

    # --- Financeiro banco (recebimentos no período) ---
    lines.append("💰 *Financeiro*")
    lines.append(f"  📋 Recebimentos — {periodo_label}")
    if fin.get("qtd"):
        lines.append(f"  Total: *{fmt_brl(fin['total'])}*  ({fin['qtd']} cobranças)")
        for item in fin.get("itens", []):
            forma = str(item.get("forma_pagamento") or "outro").upper()
            data_pg = item.get("data_pagamento")
            data_str = data_pg.strftime("%d/%m") if hasattr(data_pg, "strftime") else str(data_pg)
            nome_cond = item.get("condominio") or "—"
            lines.append(f"    {data_str} · {nome_cond} — {fmt_brl(item.get('valor_pago'))} ({forma})")
    else:
        lines.append("  Nenhum pagamento no período")

    lines.append(f"  Recebido semana: *{fmt_brl(fin_sem.get('total'))}*  ({fin_sem.get('qtd', 0)} cobranças)")
    lines.append(f"  Recebido mês:    *{fmt_brl(fin_mes.get('total'))}*  ({fin_mes.get('qtd', 0)} cobranças)")

    qtd_venc = int(inad.get("qtd") or 0)
    if qtd_venc:
        lines.append(f"  ⚠️ Inadimplência: {qtd_venc} cobranças vencidas — {fmt_brl(inad.get('total'))}")

    if "balance" in saldo:
        lines.append(f"  🏦 Saldo Asaas:  *{fmt_brl(saldo['balance'])}*")
    elif saldo.get("erro") != "ASAAS_API_KEY não configurada":
        lines.append(f"  ⚠️ Saldo Asaas indisponível: {saldo.get('erro')}")

    if "erro" not in asaas:
        lines.append("")
        lines.append(f"  🔁 Asaas (confirmação externa, hoje): *{fmt_brl(asaas['total'])}*  ({asaas['qtd']} pgtos)")
        for tipo, val in asaas.get("por_tipo", {}).items():
            lines.append(f"    {tipo}: {fmt_brl(val)}")
    elif asaas.get("erro") != "ASAAS_API_KEY não configurada":
        lines.append(f"  ⚠️ Asaas indisponível: {asaas['erro']}")

    # --- Pagamentos pela eCondomínio (despesas) no período ---
    lines.append("")
    lines.append(f"💸 *Pagamentos pela eCondomínio* — {periodo_label}")
    if despesas_periodo:
        total_despesas = sum(float(d.get("valor_pago") or 0) for d in despesas_periodo)
        lines.append(f"  Total: *{fmt_brl(total_despesas)}*  ({len(despesas_periodo)} pagamentos)")
        for d in despesas_periodo:
            forma = str(d.get("forma_pagamento") or "outro").upper()
            data_pg = d.get("data_pagamento")
            data_str = data_pg.strftime("%d/%m") if hasattr(data_pg, "strftime") else str(data_pg)
            lines.append(f"    {data_str} · {d.get('descricao')} — {fmt_brl(d.get('valor_pago'))} ({forma})")
    else:
        lines.append("  Nenhuma despesa paga no período")

    lines.append("")
    lines.append("_eCondomínio Sistemas_")

    return "\n".join(lines)

# ------------------------------------------------------------------------------
# ENVIO — Z-API
# ------------------------------------------------------------------------------

def enviar_meta(destino: str, mensagem: str) -> bool:
    digits = "".join(filter(str.isdigit, destino))
    if not digits.startswith("55"):
        digits = "55" + digits

    url     = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN
    payload = {"phone": digits, "message": mensagem}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        if r.status_code < 300:
            logger.info("Z-API OK | destino=%s", digits)
            return True
        logger.warning("Z-API HTTP %s | destino=%s | resp=%s", r.status_code, digits, r.text[:200])
        return False
    except Exception:
        logger.exception("Erro Z-API | destino=%s", digits)
        return False

# ------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------

def main():
    logger.info("Gerando relatório diário...")

    mensagem = montar_mensagem()
    logger.info("Mensagem montada:\n%s", mensagem)

    destinos = [d.strip() for d in RELATORIO_DESTINOS if d.strip()]
    if not destinos:
        logger.warning("RELATORIO_WHATSAPP_DESTINOS não configurado — apenas log")
        print(mensagem)
        return

    for destino in destinos:
        ok = enviar_meta(destino, mensagem)
        if not ok:
            logger.error("Falha ao enviar para %s", destino)

    logger.info("Relatório concluído.")


if __name__ == "__main__":
    main()
