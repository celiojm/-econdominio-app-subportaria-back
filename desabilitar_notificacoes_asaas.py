# ============================================================================
# ARQUIVO: desabilitar_notificacoes_asaas.py
# PASTA: /home/visionlpr/backend/
# DESCRIÇÃO: Script retroativo para desabilitar TODAS as notificações Asaas
#             (notificationDisabled + batch de notificações individuais)
# VERSÃO: 2.0.0
# CRIAÇÃO: 2026-05-22   ALTERAÇÃO: 2026-05-22
# ============================================================================
#
# USO:
#   source /home/visionlpr/backend/venv/bin/activate
#
#   # Testar em DEV (banco AdmGeral_dev):
#   python3 desabilitar_notificacoes_asaas.py --env dev
#
#   # Produção (banco AdmGeral) — somente após validar em dev:
#   python3 desabilitar_notificacoes_asaas.py --env prod
#
# ============================================================================

import asyncio
import argparse
import os
import sys
import httpx
import logging
from datetime import datetime

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Carregar .env ─────────────────────────────────────────────────────────────
def load_env(env_file: str = "/home/visionlpr/backend/.env"):
    if not os.path.exists(env_file):
        logger.warning(f".env não encontrado em {env_file} — usando variáveis do sistema")
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value

load_env()


# ── Configurações ─────────────────────────────────────────────────────────────
def get_config(env: str) -> dict:
    asaas_env = os.getenv("ASAAS_ENV", "sandbox")
    if asaas_env == "sandbox":
        asaas_url = os.getenv("ASAAS_URL_SANDBOX", "https://api-sandbox.asaas.com/v3")
    else:
        asaas_url = os.getenv("ASAAS_URL_PRODUCTION", "https://api.asaas.com/v3")

    db_name = "AdmGeral_dev" if env == "dev" else "AdmGeral"

    return {
        "asaas_url": asaas_url,
        "asaas_key": os.getenv("ASAAS_API_KEY", ""),
        "db_host":   os.getenv("DB_HOST", "10.3.1.3"),
        "db_port":   int(os.getenv("DB_PORT", "6033")),
        "db_user":   os.getenv("DB_USER", "econdo"),
        "db_pass":   os.getenv("DB_PASSWORD", ""),
        "db_name":   db_name,
    }


# ── Buscar clientes no banco ──────────────────────────────────────────────────
def buscar_clientes(cfg: dict) -> list:
    try:
        import pymysql
    except ImportError:
        logger.error("pymysql não instalado. Execute: pip install pymysql --break-system-packages")
        sys.exit(1)

    conn = pymysql.connect(
        host=cfg["db_host"], port=cfg["db_port"],
        user=cfg["db_user"], password=cfg["db_pass"],
        database=cfg["db_name"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, nome, asaas_customer_id
                FROM condominios
                WHERE asaas_customer_id IS NOT NULL
                  AND asaas_customer_id != ''
                ORDER BY id
            """)
            rows = cur.fetchall()
        logger.info(f"Banco [{cfg['db_name']}] — {len(rows)} condomínio(s) com asaas_customer_id")
        return rows
    finally:
        conn.close()


# ── Passo 1: notificationDisabled = true ─────────────────────────────────────
async def desabilitar_flag_principal(client: httpx.AsyncClient, cfg: dict, row: dict) -> bool:
    """PUT /customers/{id}  →  notificationDisabled: true"""
    url  = f"{cfg['asaas_url']}/customers/{row['asaas_customer_id']}"
    resp = await client.put(url, headers=_headers(cfg), json={"notificationDisabled": True})

    if resp.status_code == 200:
        return True
    err = resp.json().get("errors", [{}])[0].get("description", resp.text[:120])
    if "excluído" in err or "excluido" in err:
        logger.warning(f"  ⚠️  [{row['id']}] {row['nome']}  →  cliente excluído no Asaas, pulando")
        return None   # None = excluído, não conta como falha
    logger.warning(f"  ⚠️  [{row['id']}] {row['nome']}  →  flag HTTP {resp.status_code}: {err}")
    return False


# ── Passo 2: buscar IDs das notificações individuais ─────────────────────────
async def buscar_ids_notificacoes(client: httpx.AsyncClient, cfg: dict, customer_id: str) -> list:
    """GET /customers/{id}/notifications"""
    url  = f"{cfg['asaas_url']}/customers/{customer_id}/notifications"
    resp = await client.get(url, headers=_headers(cfg))
    if resp.status_code != 200:
        return []
    data = resp.json().get("data", [])
    return [n["id"] for n in data if not n.get("deleted", False)]


# ── Passo 3: desabilitar todas via batch ─────────────────────────────────────
async def desabilitar_batch(client: httpx.AsyncClient, cfg: dict, customer_id: str, notif_ids: list) -> bool:
    """POST /notifications/batch  →  zera todos os canais de cada notificação"""
    if not notif_ids:
        return True

    url = f"{cfg['asaas_url']}/notifications/batch"
    payload = {
        "customer": customer_id,
        "notifications": [
            {
                "id": nid,
                "enabled": False,
                "emailEnabledForCustomer":     False,
                "smsEnabledForCustomer":       False,
                "whatsappEnabledForCustomer":  False,
                "phoneCallEnabledForCustomer": False,
                "emailEnabledForProvider":     False,
                "smsEnabledForProvider":       False,
            }
            for nid in notif_ids
        ]
    }

    resp = await client.post(url, headers=_headers(cfg), json=payload)
    return resp.status_code == 200


# ── Loop principal ────────────────────────────────────────────────────────────
async def processar_clientes(cfg: dict, customers: list):
    ok = 0
    excluidos = 0
    falhas = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for row in customers:
            cid  = row["id"]
            nome = row["nome"]
            acid = row["asaas_customer_id"]

            # Passo 1 — flag principal
            resultado = await desabilitar_flag_principal(client, cfg, row)
            await asyncio.sleep(0.3)

            if resultado is None:      # cliente excluído no Asaas
                excluidos += 1
                continue

            if resultado is False:
                falhas.append({"id": cid, "nome": nome, "acid": acid, "etapa": "flag principal"})
                continue

            # Passo 2 — buscar IDs
            notif_ids = await buscar_ids_notificacoes(client, cfg, acid)
            await asyncio.sleep(0.3)

            # Passo 3 — batch
            batch_ok = await desabilitar_batch(client, cfg, acid, notif_ids) if notif_ids else True
            await asyncio.sleep(0.3)

            if batch_ok:
                logger.info(f"  ✅  [{cid}] {nome}  →  {acid}  ({len(notif_ids)} notif. zeradas)")
                ok += 1
            else:
                logger.warning(f"  ⚠️  [{cid}] {nome}  →  batch falhou")
                falhas.append({"id": cid, "nome": nome, "acid": acid, "etapa": "batch"})

    logger.info("=" * 60)
    logger.info(f"CONCLUÍDO — ✅ {ok} OK  |  ⏭️  {excluidos} excluídos no Asaas  |  ❌ {len(falhas)} falha(s)")
    if falhas:
        logger.info("Falhas:")
        for f in falhas:
            logger.info(f"  id={f['id']}  nome={f['nome']}  etapa={f['etapa']}")
    logger.info("=" * 60)


# ── Helper ────────────────────────────────────────────────────────────────────
def _headers(cfg: dict) -> dict:
    return {
        "accept":       "application/json",
        "content-type": "application/json",
        "access_token": cfg["asaas_key"],
    }


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(env: str):
    logger.info("=" * 60)
    logger.info(f"Script: desabilitar notificações Asaas v2.0 — {env.upper()}")
    logger.info(f"Início: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    cfg = get_config(env)

    if not cfg["asaas_key"]:
        logger.error("ASAAS_API_KEY não encontrada no .env")
        sys.exit(1)

    logger.info(f"Asaas URL : {cfg['asaas_url']}")
    logger.info(f"Banco     : {cfg['db_name']} @ {cfg['db_host']}:{cfg['db_port']}")
    logger.info("")

    customers = buscar_clientes(cfg)
    if not customers:
        logger.info("Nenhum cliente encontrado. Nada a fazer.")
        return

    logger.info("Iniciando...")
    logger.info("")
    await processar_clientes(cfg, customers)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], required=True,
                        help="dev = AdmGeral_dev | prod = AdmGeral")
    args = parser.parse_args()
    asyncio.run(main(args.env))
