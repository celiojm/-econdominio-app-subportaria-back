# ==============================================================================
# ARQUIVO: sync_despesas_asaas.py
# PASTA: /home/ubuntu/backend/
# DESCRIÇÃO: Sincroniza pagamentos de contas feitos pela eCondomínio através
#            da própria conta Asaas (endpoint /v3/bill) para a tabela local
#            contas_a_pagar. Idempotente — usa asaas_bill_id como chave única,
#            nunca duplica nem sobrescreve lançamentos manuais (origem='manual').
# CRIAÇÃO: 2026-07-05
# CRONTAB sugerido: 0 7 * * * (junto dos outros sync_* já existentes)
#
# PRÉ-REQUISITO: rodar antes desta migração de schema (uma única vez):
#   ALTER TABLE contas_a_pagar
#     ADD COLUMN origem VARCHAR(20) NOT NULL DEFAULT 'manual' AFTER criado_por,
#     ADD COLUMN asaas_bill_id VARCHAR(60) NULL AFTER origem,
#     ADD UNIQUE KEY uq_asaas_bill_id (asaas_bill_id);
# ==============================================================================

import os
import logging
from datetime import date

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
logger = logging.getLogger("sync_despesas_asaas")

DATABASE_URL   = os.getenv("DATABASE_URL", "")
ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY", "")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://api.asaas.com/v3")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurado no .env")
if not ASAAS_API_KEY:
    raise RuntimeError("ASAAS_API_KEY não configurado no .env")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

# ------------------------------------------------------------------------------
# Mapeamento status Asaas -> status local (enum de contas_a_pagar é fixo:
# pendente | pago | vencido | cancelado)
# ------------------------------------------------------------------------------

STATUS_MAP = {
    "PAID":                                 "pago",
    "PENDING":                              "pendente",
    "BANK_PROCESSING":                      "pendente",
    "AWAITING_CHECKOUT_RISK_ANALYSIS_REQUEST": "pendente",
    "FAILED":                               "cancelado",
    "CANCELLED":                            "cancelado",
    "REFUNDED":                             "cancelado",
}


def fetch_bills(limit: int = 100) -> list:
    """Busca todos os pagamentos de conta no Asaas, paginando."""
    headers = {"access_token": ASAAS_API_KEY}
    url = f"{ASAAS_BASE_URL}/bill"
    offset = 0
    all_items = []

    while True:
        r = requests.get(url, headers=headers, params={"limit": limit, "offset": offset}, timeout=15)
        if r.status_code != 200:
            logger.error("Asaas /bill HTTP %s: %s", r.status_code, r.text[:300])
            break

        data = r.json()
        items = data.get("data", [])
        all_items.extend(items)

        if not data.get("hasMore"):
            break
        offset += limit

    return all_items


def already_imported_ids(conn) -> set:
    rows = conn.execute(text("""
        SELECT asaas_bill_id FROM contas_a_pagar WHERE asaas_bill_id IS NOT NULL
    """)).fetchall()
    return {r[0] for r in rows}


def already_imported_ftn_ids(conn) -> set:
    rows = conn.execute(text("""
        SELECT asaas_ftn_id FROM contas_a_pagar WHERE asaas_ftn_id IS NOT NULL
    """)).fetchall()
    return {r[0] for r in rows}


# Tipos de transação do Asaas que representam taxa/tarifa cobrada da conta.
# Confirmado em 2026-07-05 via /v3/financialTransactions: PAYMENT_FEE cobre
# tanto boleto quanto Pix (mesmo tipo genérico), PAYMENT_MESSAGING_NOTIFICATION_FEE
# é taxa de mensageria, INVOICE_FEE é emissão de nota fiscal. Pode haver outros
# tipos de taxa não vistos ainda (ex: antecipação) — o filtro abaixo usa
# "contém FEE" para pegar qualquer tipo de taxa futuro sem precisar listar todos.
def fetch_fee_transactions(limit: int = 100) -> list:
    """Busca transações do tipo taxa (FEE) no extrato financeiro do Asaas."""
    headers = {"access_token": ASAAS_API_KEY}
    url = f"{ASAAS_BASE_URL}/financialTransactions"
    offset = 0
    fees = []

    while True:
        r = requests.get(url, headers=headers, params={"limit": limit, "offset": offset}, timeout=15)
        if r.status_code != 200:
            logger.error("Asaas /financialTransactions HTTP %s: %s", r.status_code, r.text[:300])
            break

        data = r.json()
        items = data.get("data", [])
        fees.extend(i for i in items if "FEE" in (i.get("type") or ""))

        if not data.get("hasMore"):
            break
        offset += limit

    return fees


def importar_taxa(item: dict) -> dict:
    valor = abs(float(item.get("value") or 0))
    data_transacao = item.get("date")
    return {
        "descricao":        f"Taxa Asaas ({item.get('type')}) - {item.get('description') or ''}"[:200],
        "fornecedor":       "Asaas",
        "categoria":        "taxas",
        "valor":            valor,
        "valor_pago":       valor,
        "data_vencimento":  data_transacao,
        "data_pagamento":   data_transacao,
        "competencia":      (data_transacao or "")[:7] if data_transacao else None,
        "status":           "pago",
        "recorrente":       0,
        "periodicidade":    "mensal",
        "dia_vencimento":   None,
        "forma_pagamento":  "outros",
        "conta_pagamento":  "Asaas",
        "observacoes":      None,
        "numero_documento": None,
        "criado_por":       "Sync Asaas",
        "origem":           "asaas_fee",
        "asaas_ftn_id":     item["id"],
    }

def importar(item: dict) -> dict:
    """Monta o dict de insert a partir do JSON do Asaas."""
    status_local = STATUS_MAP.get(item.get("status"), "pendente")

    descricao = item.get("description") or item.get("beneficiaryName") or item.get("companyName") or "Pagamento de conta (Asaas)"
    fornecedor = item.get("companyName") or item.get("beneficiaryName")

    data_vencimento = item.get("dueDate")
    data_pagamento  = item.get("paymentDate")
    competencia     = (data_vencimento or "")[:7] if data_vencimento else None

    valor      = item.get("value") or 0
    valor_pago = item.get("value") if status_local == "pago" else None

    observacoes = None
    if item.get("transactionReceiptUrl"):
        observacoes = f"Comprovante Asaas: {item['transactionReceiptUrl']}"

    return {
        "descricao":        descricao[:200],
        "fornecedor":       (fornecedor or "")[:150] or None,
        "categoria":        "outros",  # revisar manualmente depois — Asaas não categoriza
        "valor":            valor,
        "valor_pago":       valor_pago,
        "data_vencimento":  data_vencimento,
        "data_pagamento":   data_pagamento,
        "competencia":      competencia,
        "status":           status_local,
        "recorrente":       0,
        "periodicidade":    "mensal",
        "dia_vencimento":   None,
        "forma_pagamento":  "boleto",
        "conta_pagamento":  "Asaas",
        "observacoes":      observacoes,
        "numero_documento": item.get("identificationField"),
        "criado_por":       "Sync Asaas",
        "origem":           "asaas_bill",
        "asaas_bill_id":    item["id"],
    }
def sync_bills(conn):
    logger.info("Buscando pagamentos de conta no Asaas...")
    items = fetch_bills()
    logger.info("Total retornado pelo Asaas (bills): %s", len(items))

    ja_importados = already_imported_ids(conn)
    logger.info("Bills já importados anteriormente: %s", len(ja_importados))

    novos = [importar(i) for i in items if i["id"] not in ja_importados]
    logger.info("Bills novos a importar: %s", len(novos))

    for row in novos:
        conn.execute(text("""
            INSERT INTO contas_a_pagar
                (descricao, fornecedor, categoria, valor, valor_pago,
                 data_vencimento, data_pagamento, competencia, status,
                 recorrente, periodicidade, dia_vencimento, forma_pagamento,
                 conta_pagamento, observacoes, numero_documento, criado_por,
                 origem, asaas_bill_id)
            VALUES
                (:descricao, :fornecedor, :categoria, :valor, :valor_pago,
                 :data_vencimento, :data_pagamento, :competencia, :status,
                 :recorrente, :periodicidade, :dia_vencimento, :forma_pagamento,
                 :conta_pagamento, :observacoes, :numero_documento, :criado_por,
                 :origem, :asaas_bill_id)
        """), row)
        logger.info("Importado (bill): %s | %s | R$ %.2f", row["asaas_bill_id"], row["descricao"], row["valor"])

    return len(novos)


def sync_taxas(conn):
    logger.info("Buscando taxas no extrato financeiro do Asaas...")
    items = fetch_fee_transactions()
    logger.info("Total de taxas retornadas pelo Asaas: %s", len(items))

    ja_importados = already_imported_ftn_ids(conn)
    logger.info("Taxas já importadas anteriormente: %s", len(ja_importados))

    novos = [importar_taxa(i) for i in items if i["id"] not in ja_importados]
    logger.info("Taxas novas a importar: %s", len(novos))

    for row in novos:
        conn.execute(text("""
            INSERT INTO contas_a_pagar
                (descricao, fornecedor, categoria, valor, valor_pago,
                 data_vencimento, data_pagamento, competencia, status,
                 recorrente, periodicidade, dia_vencimento, forma_pagamento,
                 conta_pagamento, observacoes, numero_documento, criado_por,
                 origem, asaas_ftn_id)
            VALUES
                (:descricao, :fornecedor, :categoria, :valor, :valor_pago,
                 :data_vencimento, :data_pagamento, :competencia, :status,
                 :recorrente, :periodicidade, :dia_vencimento, :forma_pagamento,
                 :conta_pagamento, :observacoes, :numero_documento, :criado_por,
                 :origem, :asaas_ftn_id)
        """), row)
        logger.info("Importada (taxa): %s | %s | R$ %.2f", row["asaas_ftn_id"], row["descricao"], row["valor"])

    return len(novos)


def main():
    with engine.connect() as conn:
        qtd_bills = sync_bills(conn)
        qtd_taxas = sync_taxas(conn)
        conn.commit()
        logger.info("Sync concluído. %s bills novos, %s taxas novas.", qtd_bills, qtd_taxas)
if __name__ == "__main__":
    main()
