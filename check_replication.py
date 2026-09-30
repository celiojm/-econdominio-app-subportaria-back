#!/usr/bin/env python3
# ==========================================================================================
# check_replication.py
# Monitor de Replicação MySQL — Tijucas
# Versão: 1.0.0 - 2026-03-23
#
# Verifica se a replicação MySQL está ativa.
# Se parada, tenta reiniciar e avisa via WhatsApp:
#   - Z-API primeiro (se conectada)
#   - Meta API como fallback
#
# Uso:
#   python3 check_replication.py
#
# Crontab (a cada 5 minutos):
#   */5 * * * * cd /home/visionlpr/backend && /home/visionlpr/backend/venv/bin/python3 check_replication.py >> /var/log/replication_monitor.log 2>&1
# ==========================================================================================

import os
import subprocess
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv

# Carrega .env do backend
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ------------------------------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("replication_monitor")

# ------------------------------------------------------------------------------------------
# CONFIG WHATSAPP
# ------------------------------------------------------------------------------------------
NOTIFY_PHONE      = "554984046118"  # Número fixo para alertas

# Z-API
ZAPI_ENABLED      = os.getenv("WHATSAPP_ZAPI_ENABLED", "true").lower() in ("1", "true")
ZAPI_INSTANCE_ID  = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN        = os.getenv("ZAPI_TOKEN", "")
ZAPI_API_URL      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")

# Meta API
META_ENABLED       = os.getenv("META_WHATSAPP_ENABLED", "true").lower() in ("1", "true")
META_ACCESS_TOKEN  = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_ID      = os.getenv("META_PHONE_NUMBER_ID", "1002371749631777")
META_TEMPLATE_NAME = os.getenv("META_TEMPLATE_NAME", "encomenda_na_portaria_v2")
META_TEMPLATE_LANG = os.getenv("META_TEMPLATE_LANG", "pt_BR")
META_API_VERSION   = os.getenv("META_API_VERSION", "v25.0")
META_API_URL       = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_ID}/messages"

# ------------------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------------------

def normalize_phone(phone: str) -> str:
    digits = "".join(filter(str.isdigit, str(phone)))
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

# ------------------------------------------------------------------------------------------
# VERIFICAR REPLICAÇÃO
# ------------------------------------------------------------------------------------------

def check_replication() -> dict:
    """Verifica status da replicação MySQL via mysql cli."""
    try:
        result = subprocess.run(
            ["sudo", "mysql", "-u", "root", "-e", "SHOW SLAVE STATUS\\G"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout

        if not output.strip():
            return {"ok": False, "error": "Sem saída do SHOW SLAVE STATUS — replicação não configurada?"}

        io_running  = ""
        sql_running = ""
        seconds_behind = ""
        last_error  = ""

        for line in output.splitlines():
            line = line.strip()
            if "Slave_IO_Running:" in line:
                io_running = line.split(":")[-1].strip()
            elif "Slave_SQL_Running:" in line and "State" not in line:
                sql_running = line.split(":")[-1].strip()
            elif "Seconds_Behind_Master:" in line:
                seconds_behind = line.split(":")[-1].strip()
            elif "Last_SQL_Error:" in line:
                last_error = line.split(":", 1)[-1].strip()

        ok = (io_running == "Yes" and sql_running == "Yes")

        return {
            "ok": ok,
            "io_running": io_running,
            "sql_running": sql_running,
            "seconds_behind": seconds_behind,
            "last_error": last_error,
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout ao verificar replicação"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def restart_replication() -> bool:
    """Tenta reiniciar a replicação."""
    try:
        result = subprocess.run(
            ["sudo", "mysql", "-u", "root", "-e", "STOP SLAVE; START SLAVE;"],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception as e:
        logger.error("Erro ao reiniciar replicação: %s", e)
        return False

# ------------------------------------------------------------------------------------------
# WHATSAPP — Z-API
# ------------------------------------------------------------------------------------------

def check_zapi_connected() -> bool:
    if not ZAPI_ENABLED or not ZAPI_INSTANCE_ID or not ZAPI_TOKEN:
        return False
    try:
        r = requests.get(f"{ZAPI_API_URL}/status", timeout=5)
        if r.status_code == 200:
            return r.json().get("whatsappReady", False)
    except Exception:
        pass
    return False


def send_zapi(phone: str, message: str) -> bool:
    phone = normalize_phone(phone)
    url   = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Content-Type": "application/json"}
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN
    try:
        r    = requests.post(url, json={"phone": phone, "message": message}, headers=headers, timeout=15)
        body = r.json() if "application/json" in r.headers.get("content-type", "") else {}
        if r.status_code < 300:
            mid = body.get("messageId") or body.get("zaapId")
            if mid or body.get("success") is True:
                logger.info("Z-API OK | tel=%s | msgId=%s", phone, mid)
                return True
        logger.warning("Z-API falhou | status=%s | body=%s", r.status_code, body)
    except Exception as e:
        logger.warning("Z-API erro | %s", e)
    return False

# ------------------------------------------------------------------------------------------
# WHATSAPP — META
# ------------------------------------------------------------------------------------------

def send_meta(phone: str, message: str) -> bool:
    if not META_ENABLED or not META_ACCESS_TOKEN:
        return False
    phone = normalize_phone(phone)

    # Usa template com mensagem genérica
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": META_TEMPLATE_NAME,
            "language": {"code": META_TEMPLATE_LANG},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "Admin"},
                        {"type": "text", "text": "Tijucas"},
                        {"type": "text", "text": message[:1024]},
                    ]
                }
            ]
        }
    }
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        r    = requests.post(META_API_URL, json=payload, headers=headers, timeout=15)
        body = r.json() if "application/json" in r.headers.get("content-type", "") else {}
        if r.status_code < 300 and body.get("messages"):
            mid = body["messages"][0].get("id")
            logger.info("Meta OK | tel=%s | msgId=%s", phone, mid)
            return True
        logger.warning("Meta falhou | status=%s | body=%s", r.status_code, body)
    except Exception as e:
        logger.warning("Meta erro | %s", e)
    return False

# ------------------------------------------------------------------------------------------
# ENVIAR ALERTA
# ------------------------------------------------------------------------------------------

def send_alert(message: str) -> None:
    """Envia alerta via Z-API ou Meta."""
    logger.info("Enviando alerta | tel=%s", NOTIFY_PHONE)

    # Tenta Z-API primeiro
    if check_zapi_connected():
        logger.info("Usando Z-API para alerta")
        if send_zapi(NOTIFY_PHONE, message):
            return
        logger.warning("Z-API falhou, tentando Meta...")

    # Fallback Meta
    logger.info("Usando Meta para alerta")
    if send_meta(NOTIFY_PHONE, message):
        return

    logger.error("FALHA ao enviar alerta por todos os canais!")

# ------------------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------------------

def main():
    logger.info("=== Verificando replicação MySQL ===")

    status = check_replication()

    if status.get("ok"):
        behind = status.get("seconds_behind", "0")
        logger.info(
            "Replicação OK | IO=%s | SQL=%s | Atraso=%ss",
            status.get("io_running"), status.get("sql_running"), behind
        )
        return

    # Replicação com problema
    error_info = status.get("error") or status.get("last_error") or "erro desconhecido"
    logger.warning(
        "Replicação PARADA | IO=%s | SQL=%s | Erro=%s",
        status.get("io_running", "?"), status.get("sql_running", "?"), error_info
    )

    # Tenta reiniciar
    logger.info("Tentando reiniciar replicação...")
    restarted = restart_replication()

    if restarted:
        # Verifica novamente após restart
        import time
        time.sleep(3)
        status2 = check_replication()
        if status2.get("ok"):
            msg = (
                f"[ALERTA - Tijucas] {now_str()}\n"
                f"Replicação MySQL estava parada mas foi REINICIADA com sucesso.\n"
                f"Erro anterior: {error_info}"
            )
            logger.info("Replicação reiniciada com sucesso!")
            send_alert(msg)
            return
        else:
            error_info = status2.get("last_error") or error_info

    # Replicação ainda parada
    msg = (
        f"[ALERTA CRITICO - Tijucas] {now_str()}\n"
        f"Replicacao MySQL PARADA e nao foi possivel reiniciar!\n"
        f"IO: {status.get('io_running','?')} | SQL: {status.get('sql_running','?')}\n"
        f"Erro: {error_info}\n"
        f"Acesse: ssh visionlpr@10.3.2.5"
    )
    logger.error("Replicação parada e não reiniciada! Enviando alerta crítico.")
    send_alert(msg)


if __name__ == "__main__":
    main()
