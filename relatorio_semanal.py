# ==============================================================================
# ARQUIVO: relatorio_semanal.py
# PASTA: /home/visionlpr/desenvolvimento/monitor/
# DESCRIÇÃO: Relatório semanal — soma WhatsApp, moradores, financeiro e
#            despesas da semana (segunda a sábado). Reaproveita as funções
#            já validadas em relatorio_diario.py (mesma pasta) em vez de
#            duplicar lógica — qualquer correção de bug feita lá se propaga
#            automaticamente pra cá.
# CRIAÇÃO: 2026-07-05
# CRONTAB sugerido: 30 8 * * 6  (sábado às 08h30 — dia 6 = sábado no cron)
# ==============================================================================

import os
import sys
import logging
from datetime import date, timedelta

import requests

# Garante que o import funcione independente do diretório de onde o cron chama o script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from relatorio_diario import (  # noqa: E402
    db_query,
    fmt_brl,
    fmt_pct,
    enviar_meta,
    stats_moradores,
    stats_condominios,
    stats_financeiro_periodo,
    stats_despesas_periodo,
    stats_inadimplencia,
    saldo_asaas,
    ASAAS_API_KEY,
    ASAAS_BASE_URL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("relatorio_semanal")

# Destinos separados do relatório diário — só o número de testes por enquanto.
# Adicionar o segundo número aqui quando confirmado, separado por vírgula.
RELATORIO_SEMANAL_DESTINOS = os.getenv("RELATORIO_SEMANAL_DESTINOS", "5548984046118").split(",")


def semana_atual() -> tuple:
    """Segunda-feira desta semana até hoje (pensado pra rodar aos sábados)."""
    hoje = date.today()
    inicio = hoje - timedelta(days=hoje.weekday())  # segunda-feira
    label = f"{inicio.strftime('%d/%m')} a {hoje.strftime('%d/%m')}"
    return inicio, hoje, label


def stats_whatsapp_semana(data_ini: date, data_fim: date) -> dict:
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


def stats_moradores_semana(data_ini: date, data_fim: date) -> dict:
    rows = db_query("""
        SELECT COUNT(*) AS cadastrados_semana
        FROM moradores
        WHERE DATE(data_cadastro) BETWEEN :ini AND :fim
          AND ativo = 1
    """, {"ini": data_ini, "fim": data_fim})
    cadastrados = rows[0].get("cadastrados_semana", 0) if rows else 0

    rows2 = db_query("""
        SELECT COUNT(*) AS confirmados_semana
        FROM moradores
        WHERE DATE(whats_confirmado) BETWEEN :ini AND :fim
    """, {"ini": data_ini, "fim": data_fim})
    confirmados = rows2[0].get("confirmados_semana", 0) if rows2 else 0

    return {"cadastrados_semana": cadastrados, "confirmados_semana": confirmados}


def stats_condominios_novos_semana(data_ini: date, data_fim: date) -> int:
    rows = db_query("""
        SELECT COUNT(*) AS novos
        FROM condominios
        WHERE DATE(data_cadastro) BETWEEN :ini AND :fim
          AND assinatura_status != 'sistema'
    """, {"ini": data_ini, "fim": data_fim})
    return int(rows[0].get("novos", 0)) if rows else 0


def stats_financeiro_asaas_semana(data_ini: date, data_fim: date) -> dict:
    """Confirmação externa via Asaas, filtrando por intervalo de datas
    (paymentDate[ge]/paymentDate[le] — confirmado na doc oficial)."""
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
                # netValue = valor líquido (já descontada taxa Asaas) — mesma
                # base usada por sync_pagamentos_e_nf.py no banco local.
                # 'value' é o valor bruto pago pelo cliente, não comparável
                # direto com cobrancas.valor_pago (confirmado em 2026-07-05).
                total += float(item.get("netValue") or item.get("value") or 0)
                qtd += 1
            if not data.get("hasMore"):
                break
            params["offset"] += 100

    except Exception as exc:
        logger.exception("Erro consultando Asaas (payments, semana)")
        return {"erro": str(exc)}

    return {"total": total, "qtd": qtd}


def montar_mensagem_semanal() -> str:
    data_ini, data_fim, label = semana_atual()

    wpp  = stats_whatsapp_semana(data_ini, data_fim)
    mor  = stats_moradores_semana(data_ini, data_fim)
    mor_snapshot = stats_moradores()  # total_ativos é foto do momento, não soma
    cond = stats_condominios()        # pagantes/trial/total são foto do momento
    novos_cond = stats_condominios_novos_semana(data_ini, data_fim)

    fin       = stats_financeiro_periodo(data_ini, data_fim)
    despesas  = stats_despesas_periodo(data_ini, data_fim)
    inad      = stats_inadimplencia()
    saldo     = saldo_asaas()
    asaas_sem = stats_financeiro_asaas_semana(data_ini, data_fim)

    enviados   = int(wpp.get("enviados") or 0)
    falhas     = int(wpp.get("falhas") or 0)
    encomendas = int(wpp.get("encomendas") or 0)
    respostas  = int(wpp.get("respostas") or 0)

    lines = []
    lines.append("📅 *Relatório Semanal eCondomínio*")
    lines.append(f"Período: {label}")
    lines.append("")

    lines.append("💬 *WhatsApp — semana*")
    lines.append(f"  Enviados:    {enviados}")
    lines.append(f"  Encomendas:  {encomendas}  ({fmt_pct(encomendas, enviados)} do total)")
    lines.append(f"  Falhas:      {falhas}")
    lines.append(f"  Respostas dos moradores: {respostas}")
    lines.append("")

    lines.append("👤 *Moradores*")
    lines.append(f"  Cadastrados na semana: {mor.get('cadastrados_semana', 0)}")
    lines.append(f"  Confirmaram WhatsApp:  {mor.get('confirmados_semana', 0)}")
    lines.append(f"  Total ativos hoje:     {mor_snapshot.get('total_ativos', 0)}")
    lines.append("")

    lines.append("🏢 *Condomínios* (foto de hoje)")
    lines.append(f"  💰 Pagantes:                 {cond.get('pagantes', 0)}")
    lines.append(f"  🔵 Ativos sem cobrança paga: {cond.get('usando_sem_pagamento', 0)}")
    lines.append(f"  🟡 Trial:                    {cond.get('trial', 0)}")
    lines.append(f"  Total ativos:                {cond.get('total_ativos', 0)}")
    if novos_cond:
        lines.append(f"  🆕 Novos na semana:          {novos_cond}")
    for r in cond.get("renovacoes_proximas", []):
        lines.append(f"  ⏰ Renova em breve: {r['nome']} ({r['validade_ate']})")
    lines.append("")

    lines.append("💰 *Financeiro — semana*")
    if fin.get("qtd"):
        lines.append(f"  Recebido: *{fmt_brl(fin['total'])}*  ({fin['qtd']} cobranças)")
        for item in fin.get("itens", []):
            forma = str(item.get("forma_pagamento") or "outro").upper()
            data_pg = item.get("data_pagamento")
            data_str = data_pg.strftime("%d/%m") if hasattr(data_pg, "strftime") else str(data_pg)
            nome_cond = item.get("condominio") or "—"
            lines.append(f"    {data_str} · {nome_cond} — {fmt_brl(item.get('valor_pago'))} ({forma})")
    else:
        lines.append("  Nenhum pagamento na semana")

    qtd_venc = int(inad.get("qtd") or 0)
    if qtd_venc:
        lines.append(f"  ⚠️ Inadimplência (total acumulado): {qtd_venc} cobranças vencidas — {fmt_brl(inad.get('total'))}")

    if "balance" in saldo:
        lines.append(f"  🏦 Saldo Asaas (hoje): *{fmt_brl(saldo['balance'])}*")

    if "erro" not in asaas_sem:
        lines.append(f"  🔁 Asaas (confirmação externa, semana): *{fmt_brl(asaas_sem['total'])}*  ({asaas_sem['qtd']} pgtos)")
    lines.append("")
    lines.append("💸 *Pagamentos pela eCondomínio — semana*")
    CAT_LABEL = {
        "pessoal":        "👤 Pessoal",
        "infraestrutura": "🖥️ Infraestrutura",
        "software":       "💻 Software",
        "contabilidade":  "📒 Contabilidade",
        "impostos":       "🏛️ Impostos",
        "marketing":      "📣 Marketing",
        "taxas":          "🏦 Taxas",
        "outros":         "📦 Outros",
    }
    if despesas:
        total_despesas = sum(float(d.get("valor_pago") or 0) for d in despesas)
        # Agrupar por categoria
        from collections import defaultdict
        por_cat = defaultdict(list)
        for d in despesas:
            por_cat[d.get("categoria") or "outros"].append(d)
        for cat, itens in sorted(por_cat.items()):
            subtotal = sum(float(i.get("valor_pago") or 0) for i in itens)
            label = CAT_LABEL.get(cat, cat.capitalize())
            lines.append(f"  {label}: *{fmt_brl(subtotal)}*")
            for d in itens:
                forma = str(d.get("forma_pagamento") or "outro").upper()
                data_pg = d.get("data_pagamento")
                data_str = data_pg.strftime("%d/%m") if hasattr(data_pg, "strftime") else str(data_pg)
                desc = (d.get("descricao") or "").strip()
                # Encurtar descrições longas de taxas Asaas
                if "Taxa Asaas" in desc:
                    # Manter tipo da taxa e condomínio, remover nr. da NF
                    import re
                    desc = re.sub(r'\s*-\s*fatura nr\.\s*\S+', '', desc)
                    desc = re.sub(r'\s*nr\.\s*\d+\s*-\s*', ' · ', desc)
                forma_str = forma if forma and forma != "OUTROS" else "—"
                lines.append(f"    {data_str} · {desc} — {fmt_brl(d.get('valor_pago'))} ({forma_str})")

        lines.append(f"  ─────────────────────")
        lines.append(f"  Total pagamentos: *{fmt_brl(total_despesas)}*")
    else:
        lines.append("  Nenhuma despesa paga na semana")

    # Resumo final: recebimentos vs pagamentos
    total_recebido = float(fin.get("total") or 0)
    total_pago     = sum(float(d.get("valor_pago") or 0) for d in despesas) if despesas else 0.0
    saldo_semana   = total_recebido - total_pago
    lines.append("")
    lines.append("📊 *Resumo da semana*")
    lines.append(f"  ✅ Total recebido:   *{fmt_brl(total_recebido)}*")
    lines.append(f"  💸 Total pago:       *{fmt_brl(total_pago)}*")
    lines.append(f"  {'🟢' if saldo_semana >= 0 else '🔴'} Saldo:            *{fmt_brl(saldo_semana)}*")
    if "balance" in saldo:
        lines.append(f"  🏦 Saldo Asaas:     *{fmt_brl(saldo['balance'])}*")
    lines.append("")
    lines.append("_eCondomínio Sistemas — Resumo Semanal_")
    return "\n".join(lines)


def main():
    logger.info("Gerando relatório semanal...")

    mensagem = montar_mensagem_semanal()
    logger.info("Mensagem montada:\n%s", mensagem)

    destinos = [d.strip() for d in RELATORIO_SEMANAL_DESTINOS if d.strip()]
    if not destinos:
        logger.warning("RELATORIO_SEMANAL_DESTINOS não configurado — apenas log")
        print(mensagem)
        return

    for destino in destinos:
        ok = enviar_meta(destino, mensagem)
        if not ok:
            logger.error("Falha ao enviar para %s", destino)

    logger.info("Relatório semanal concluído.")


if __name__ == "__main__":
    main()
