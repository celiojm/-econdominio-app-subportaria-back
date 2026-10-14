# ============================================================================
# ARQUIVO: validade_pagamento.py
# PASTA: backend/financeiro/
# DESCRIÇÃO: Regra única de validade da licença a partir de um pagamento
#            (usada pelo webhook do Asaas e pela sincronização diária).
# VERSÃO: 1.0.0 - criação: dias pelo período da cobrança paga; pagamento novo
#                 soma a partir da validade atual; revisão só garante o mínimo
# data criação: 2026-10-14 data alteração: 2026-10-14
# ============================================================================

from datetime import date, timedelta
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Status de cobrança que contam como pagamento recebido
STATUS_PAGO = ("pago", "RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH")

DIAS_POR_PERIODO = {
    "anual": 365,
    "semestral": 180,
    "trimestral": 90,
    "mensal": 30,
}


def dias_pela_descricao(descricao):
    """Dias do período escrito na cobrança ("Trimestral - ...", "plano mensal"); None se não achar."""
    d = (descricao or "").lower()
    for periodo in ("anual", "semestral", "trimestral", "mensal"):
        if periodo in d:
            return DIAS_POR_PERIODO[periodo]
    return None


def dias_do_plano(plano_selecionado):
    """Dias pelo plano do cadastro do condomínio; None se vazio/desconhecido."""
    return DIAS_POR_PERIODO.get((plano_selecionado or "").lower().strip())


def aplicar_pagamento(db, id_condominio, dias, data_pagamento, pagamento_novo):
    """
    Atualiza condominios.validade_ate (e assinaturas) por causa de um pagamento.

    pagamento_novo=True  -> a cobrança acabou de virar paga: soma `dias` a partir da
                            validade atual (ou da data do pagamento, se já venceu).
    pagamento_novo=False -> pagamento já registrado antes (revisão): só garante
                            data_pagamento + dias. Nunca soma de novo.
    Nunca diminui a validade. Não faz commit.
    Retorna (validade_anterior, nova_validade, alterou).
    """
    data_pagamento = data_pagamento or date.today()
    row = db.execute(
        text("SELECT validade_ate FROM condominios WHERE id = :id"),
        {"id": id_condominio},
    ).fetchone()
    if not row:
        return None, None, False
    anterior = row[0]

    if pagamento_novo:
        base = max(anterior, data_pagamento) if anterior else data_pagamento
    else:
        base = data_pagamento
    nova = base + timedelta(days=dias)

    if anterior and nova <= anterior:
        return anterior, anterior, False

    db.execute(
        text("""
            UPDATE condominios
               SET validade_ate = :v,
                   assinatura_status = IF(assinatura_status = 'sistema', 'sistema', 'ativa')
             WHERE id = :id
        """),
        {"v": nova, "id": id_condominio},
    )
    db.execute(
        text("""
            UPDATE assinaturas
               SET validade_ate = :v, status = 'ativa', data_atualizacao = NOW()
             WHERE id_condominio = :id AND (validade_ate IS NULL OR validade_ate < :v)
        """),
        {"v": nova, "id": id_condominio},
    )
    logger.info(
        f"📅 Validade condomínio {id_condominio}: {anterior} → {nova} "
        f"(+{dias} dias, {'pagamento novo' if pagamento_novo else 'revisão'}, pgto {data_pagamento})"
    )
    return anterior, nova, True
