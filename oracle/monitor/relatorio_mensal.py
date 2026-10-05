# ==============================================================================
# ARQUIVO: relatorio_mensal.py
# PASTA: /home/ubuntu/backend/monitor/
# DESCRIÇÃO: Relatório mensal — soma WhatsApp, moradores, financeiro e
#            despesas do mês anterior completo. Reaproveita as funções já
#            validadas em relatorio_diario.py (mesma pasta). Recebimentos e
#            despesas são AGRUPADOS (por forma de pagamento / categoria /
#            conta de pagamento) e também LISTADOS item a item.
# VERSÃO: 1.3.0 — relatório enxuto: WhatsApp (enviados/respostas),
#          Condomínios (pagantes/trial), Financeiro (recebido+formas+saldo+
#          lista de recebimentos por condomínio) e Pagamentos por categoria
#          + lista item a item (exceto taxas de banco).
# CRIAÇÃO: 2026-07-05   ALTERAÇÃO: 2026-08-01
# CRONTAB: 0 8 1 * *  (dia 1 de cada mês, às 08h)
# ==============================================================================

import os
import sys
import logging
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from relatorio_diario import (  # noqa: E402
    db_query,
    fmt_brl,
    fmt_pct,
    enviar_meta,
    stats_moradores,
    stats_condominios,
    stats_inadimplencia,
    saldo_asaas,
    ASAAS_API_KEY,
    ASAAS_BASE_URL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("relatorio_mensal")

RELATORIO_MENSAL_DESTINOS = os.getenv("RELATORIO_MENSAL_DESTINOS", "5548984046118").split(",")

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}



def periodo_relatorio() -> tuple:
    """Sem argumento: mês anterior fechado (uso do cron).
    Com argumento AAAA-MM: aquele mês; se for o mês corrente, vai só até hoje."""
    import sys
    if len(sys.argv) < 2:
        return mes_anterior()
    ano, mes = map(int, sys.argv[1].split("-"))
    inicio = date(ano, mes, 1)
    fim = (date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)) - timedelta(days=1)
    hoje = date.today()
    if fim > hoje:
        fim = hoje
    label = f"{MESES_PT[inicio.month]}/{inicio.year} (até {fim.strftime('%d/%m')})" if fim == hoje else f"{MESES_PT[inicio.month]}/{inicio.year}"
    return inicio, fim, label
def mes_anterior() -> tuple:
    """Primeiro e último dia do mês anterior ao mês atual."""
    hoje = date.today()
    primeiro_dia_mes_atual = hoje.replace(day=1)
    ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
    inicio = ultimo_dia_mes_anterior.replace(day=1)
    fim = ultimo_dia_mes_anterior
    label = f"{MESES_PT[inicio.month]}/{inicio.year}"
    return inicio, fim, label


def pluralize(qtd, singular: str, plural: str) -> str:
    """1 item / N itens."""
    try:
        return singular if int(qtd) == 1 else plural
    except (TypeError, ValueError):
        return plural


def stats_whatsapp_mes(data_ini: date, data_fim: date) -> dict:
    fila = db_query("""
        SELECT
            SUM(CASE WHEN status = 'sent'   THEN 1 ELSE 0 END) AS enviados,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS falhas,
            SUM(CASE WHEN tipo_evento = 'ENCOMENDA_RECEBIDA' AND status = 'sent'
                     THEN 1 ELSE 0 END) AS encomendas
        FROM whatsapp_message_queue
        WHERE DATE(criado_em) BETWEEN :ini AND :fim
    """, {"ini": data_ini, "fim": data_fim})
    base = fila[0] if fila else {}

    resp = db_query("""
        SELECT COUNT(*) AS respostas
        FROM whatsapp_inbound_messages
        WHERE DATE(created_at) BETWEEN :ini AND :fim
    """, {"ini": data_ini, "fim": data_fim})
    base['respostas'] = int(resp[0].get('respostas', 0)) if resp else 0
    return base


def stats_moradores_mes(data_ini: date, data_fim: date) -> dict:
    rows = db_query("""
        SELECT COUNT(*) AS cadastrados_mes
        FROM moradores
        WHERE DATE(data_cadastro) BETWEEN :ini AND :fim
          AND ativo = 1
    """, {"ini": data_ini, "fim": data_fim})
    cadastrados = rows[0].get("cadastrados_mes", 0) if rows else 0

    rows2 = db_query("""
        SELECT COUNT(*) AS confirmados_mes
        FROM moradores
        WHERE DATE(whats_confirmado) BETWEEN :ini AND :fim
    """, {"ini": data_ini, "fim": data_fim})
    confirmados = rows2[0].get("confirmados_mes", 0) if rows2 else 0

    return {"cadastrados_mes": cadastrados, "confirmados_mes": confirmados}


def stats_condominios_novos_mes(data_ini: date, data_fim: date) -> int:
    rows = db_query("""
        SELECT COUNT(*) AS novos
        FROM condominios
        WHERE DATE(data_cadastro) BETWEEN :ini AND :fim
          AND assinatura_status != 'sistema'
    """, {"ini": data_ini, "fim": data_fim})
    return int(rows[0].get("novos", 0)) if rows else 0


def stats_condominios_churn_mes(data_ini: date, data_fim: date) -> list:
    """Condomínios cuja assinatura venceu (validade_ate) dentro do mês e
    ainda não foi renovada (validade_ate < hoje) — sinal de possível churn."""
    return db_query("""
        SELECT c.nome, a.validade_ate
        FROM assinaturas a
        JOIN condominios c ON c.id = a.id_condominio
        WHERE a.validade_ate BETWEEN :ini AND :fim
          AND a.validade_ate < CURDATE()
        ORDER BY a.validade_ate ASC
    """, {"ini": data_ini, "fim": data_fim})


def stats_financeiro_mes_agrupado(data_ini: date, data_fim: date) -> dict:
    rows = db_query("""
        SELECT
            forma_pagamento,
            COUNT(*)        AS qtd,
            SUM(valor) AS total
        FROM cobrancas
        WHERE status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
          AND DATE(data_pagamento) BETWEEN :ini AND :fim
        GROUP BY forma_pagamento
        ORDER BY total DESC
    """, {"ini": data_ini, "fim": data_fim})

    total_geral = sum(float(r.get("total") or 0) for r in rows)
    qtd_geral   = sum(int(r.get("qtd") or 0) for r in rows)

    condominios_pagantes = db_query("""
        SELECT COUNT(DISTINCT id_condominio) AS qtd
        FROM cobrancas
        WHERE status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
          AND DATE(data_pagamento) BETWEEN :ini AND :fim
    """, {"ini": data_ini, "fim": data_fim})

    return {
        "total":    total_geral,
        "qtd":      qtd_geral,
        "detalhes": rows,
        "condominios_pagantes": int(condominios_pagantes[0].get("qtd", 0)) if condominios_pagantes else 0,
    }


def stats_recebimentos_mes_itemizado(data_ini: date, data_fim: date) -> list:
    """Lista item a item dos recebimentos do mês (uma linha por cobrança paga)."""
    return db_query("""
        SELECT c.nome AS condominio, cob.valor AS valor_pago, cob.forma_pagamento
        FROM cobrancas cob
        JOIN condominios c ON c.id = cob.id_condominio
        WHERE cob.status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
          AND DATE(cob.data_pagamento) BETWEEN :ini AND :fim
        ORDER BY cob.valor DESC
    """, {"ini": data_ini, "fim": data_fim})


def stats_despesas_mes_agrupado(data_ini: date, data_fim: date) -> dict:
    rows = db_query("""
        SELECT
            categoria,
            COUNT(*)            AS qtd,
            SUM(valor_pago)      AS total
        FROM contas_a_pagar
        WHERE status = 'pago'
          AND DATE(data_pagamento) BETWEEN :ini AND :fim
        GROUP BY categoria
        ORDER BY total DESC
    """, {"ini": data_ini, "fim": data_fim})

    total_geral = sum(float(r.get("total") or 0) for r in rows)
    qtd_geral   = sum(int(r.get("qtd") or 0) for r in rows)

    return {"total": total_geral, "qtd": qtd_geral, "detalhes": rows}


def stats_despesas_mes_itemizado(data_ini: date, data_fim: date) -> list:
    """Lista item a item dos pagamentos do mês, EXCETO taxas de banco."""
    return db_query("""
        SELECT descricao, valor_pago, categoria
        FROM contas_a_pagar
        WHERE status = 'pago'
          AND categoria <> 'taxas'
          AND DATE(data_pagamento) BETWEEN :ini AND :fim
        ORDER BY valor_pago DESC
    """, {"ini": data_ini, "fim": data_fim})


def stats_despesas_mes_por_conta(data_ini: date, data_fim: date) -> dict:
    """Pagamentos do mês, agrupado por conta_pagamento."""
    rows = db_query("""
        SELECT
            conta_pagamento,
            COUNT(*)        AS qtd,
            SUM(valor_pago) AS total
        FROM contas_a_pagar
        WHERE status = 'pago'
          AND DATE(data_pagamento) BETWEEN :ini AND :fim
        GROUP BY conta_pagamento
        ORDER BY total DESC
    """, {"ini": data_ini, "fim": data_fim})
    total_geral = sum(float(r.get("total") or 0) for r in rows)
    qtd_geral   = sum(int(r.get("qtd") or 0) for r in rows)
    return {"total": total_geral, "qtd": qtd_geral, "detalhes": rows}


def stats_despesas_total_historico() -> dict:
    """Soma de todos os pagamentos já feitos, desde o início, agrupado por conta_pagamento."""
    rows = db_query("""
        SELECT
            conta_pagamento,
            COUNT(*)       AS qtd,
            SUM(valor_pago) AS total
        FROM contas_a_pagar
        WHERE status = 'pago'
        GROUP BY conta_pagamento
        ORDER BY total DESC
    """)
    total_geral = sum(float(r.get("total") or 0) for r in rows)
    qtd_geral   = sum(int(r.get("qtd") or 0) for r in rows)
    return {"total": total_geral, "qtd": qtd_geral, "detalhes": rows}


def stats_financeiro_asaas_mes(data_ini: date, data_fim: date) -> dict:
    """Confirmação externa via Asaas, usando netValue (valor líquido — mesma
    base do banco local). Confirmado em 2026-07-05: value=bruto, netValue=líquido."""
    if not ASAAS_API_KEY:
        return {"erro": "ASAAS_API_KEY não configurada"}

    headers = {"access_token": ASAAS_API_KEY}
    url = f"{ASAAS_BASE_URL}/payments"
    params = {
        "paymentDate[ge]": data_ini.strftime("%Y-%m-%d"),
        "paymentDate[le]": data_fim.strftime("%Y-%m-%d"),
        "status":          "RECEIVED,CONFIRMED,RECEIVED_IN_CASH",
        "limit":           100,
        "offset":          0,
    }

    total = 0.0
    qtd = 0

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
                total += float(item.get("netValue") or item.get("value") or 0)
                qtd += 1

            if not data.get("hasMore"):
                break
            params["offset"] += 100

    except Exception as exc:
        logger.exception("Erro consultando Asaas (payments, mês)")
        return {"erro": str(exc)}

    return {"total": total, "qtd": qtd}


def montar_mensagem_mensal() -> str:
    data_ini, data_fim, label = periodo_relatorio()

    wpp      = stats_whatsapp_mes(data_ini, data_fim)
    cond     = stats_condominios()
    fin      = stats_financeiro_mes_agrupado(data_ini, data_fim)
    fin_lista = stats_recebimentos_mes_itemizado(data_ini, data_fim)
    despesas = stats_despesas_mes_agrupado(data_ini, data_fim)
    despesas_lista = stats_despesas_mes_itemizado(data_ini, data_fim)
    saldo    = saldo_asaas()

    enviados  = int(wpp.get("enviados") or 0)
    respostas = int(wpp.get("respostas") or 0)

    lines = []
    lines.append("🗓️ *Relatório Mensal eCondomínio*")
    lines.append(f"Referente a: {label}")
    lines.append("")

    lines.append("💬 *WhatsApp — mês*")
    lines.append(f"  Enviados:                {enviados}")
    lines.append(f"  Respostas dos moradores: {respostas}")
    lines.append("")

    lines.append("🏢 *Condomínios*")
    lines.append(f"  💰 Pagantes hoje: {cond.get('pagantes', 0)}")
    lines.append(f"  🟡 Trial hoje:    {cond.get('trial', 0)}")
    lines.append("")

    lines.append(f"💰 *Financeiro — {label}*")
    if fin.get("qtd"):
        lines.append(f"  Recebido no mês: *{fmt_brl(fin['total'])}*")
        for det in fin.get("detalhes", []):
            forma = str(det.get("forma_pagamento") or "outro").upper()
            lines.append(f"    {forma}: {fmt_brl(det.get('total'))}  ({det.get('qtd')} pgtos)")
    else:
        lines.append("  Nenhum pagamento no mês")
    if "balance" in saldo:
        lines.append(f"  🏦 Saldo Asaas (hoje): *{fmt_brl(saldo['balance'])}*")
    if fin_lista:
        lines.append("  📋 *Recebimentos do mês:*")
        for det in fin_lista:
            nome = (det.get("condominio") or "—").strip()
            if len(nome) > 60:
                nome = nome[:59] + "…"
            forma = str(det.get("forma_pagamento") or "").upper()
            sufixo = f"  ({forma})" if forma else ""
            lines.append(f"    {nome} — {fmt_brl(det.get('valor_pago'))}{sufixo}")
    lines.append("")

    lines.append(f"💸 *Pagamentos pela eCondomínio — {label}*")
    if despesas.get("qtd"):
        lines.append(f"  Total: *{fmt_brl(despesas['total'])}*  ({despesas['qtd']} pagamentos)")
        for det in despesas.get("detalhes", []):
            categoria = str(det.get("categoria") or "outros").upper()
            qtd_cat   = int(det.get("qtd") or 0)
            unidade   = pluralize(qtd_cat, "item", "itens")
            lines.append(f"    {categoria}: {fmt_brl(det.get('total'))}  ({qtd_cat} {unidade})")
    else:
        lines.append("  Nenhuma despesa paga no mês")

    if despesas_lista:
        lines.append("")
        lines.append("📋 *Lista de pagamentos (exceto taxas)*")
        for det in despesas_lista:
            desc = (det.get("descricao") or "—").strip()
            if len(desc) > 45:
                desc = desc[:44] + "…"
            lines.append(f"    {desc} — {fmt_brl(det.get('valor_pago'))}")

    lines.append("")
    lines.append("_eCondomínio Sistemas — Resumo Mensal_")

    return "\n".join(lines)


def main():
    logger.info("Gerando relatório mensal...")

    mensagem = montar_mensagem_mensal()
    logger.info("Mensagem montada:\n%s", mensagem)

    destinos = [d.strip() for d in RELATORIO_MENSAL_DESTINOS if d.strip()]
    if not destinos:
        logger.warning("RELATORIO_MENSAL_DESTINOS não configurado — apenas log")
        print(mensagem)
        return

    for destino in destinos:
        ok = enviar_meta(destino, mensagem)
        if not ok:
            logger.error("Falha ao enviar para %s", destino)

    logger.info("Relatório mensal concluído.")


if __name__ == "__main__":
    main()
