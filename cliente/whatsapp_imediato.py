# =============================================================================
# cliente/whatsapp_imediato.py
# Envio IMEDIATO de WhatsApp para cadastro de novo cliente/condominio.
# NAO usa fila — dispara na hora via Z-API (preferencial) ou Meta (fallback).
#
# Versao: 1.0.0 - 2026-03-21
#
# Uso:
#   from cliente.whatsapp_imediato import send_whatsapp_imediato
#   ok = send_whatsapp_imediato(telefone, mensagem)
#
# Logica:
#   1. Verifica se Z-API esta conectada (GET /status)
#   2. Se conectada: envia via Z-API send-text
#   3. Se Z-API falhar ou offline: envia via Meta Cloud API (template texto livre)
#   4. Retorna True se qualquer provedor teve sucesso
# =============================================================================

import os
import logging
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG — lidos do .env (mesmos do send_grouped_whatsapp.py)
# ---------------------------------------------------------------------------

ZAPI_ENABLED      = os.getenv("WHATSAPP_ZAPI_ENABLED", "true").lower() in ("1", "true")
ZAPI_INSTANCE_ID  = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN        = os.getenv("ZAPI_TOKEN", "")
ZAPI_API_URL      = os.getenv("ZAPI_API_URL", "http://191.252.221.192:8080")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")

META_ENABLED      = os.getenv("META_WHATSAPP_ENABLED", "true").lower() in ("1", "true")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_ID     = os.getenv("META_PHONE_NUMBER_ID", "1002371749631777")
META_API_VERSION  = os.getenv("META_API_VERSION", "v25.0")
META_API_URL      = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_ID}/messages"

# Template de texto livre Meta (sem botoes — para mensagens de cadastro)
# DEVE ser um template aprovado do tipo "utility" com 1 parametro de corpo livre
# ou usar o template de confirmacao configurado no .env
META_TEMPLATE_TEXTO_LIVRE = os.getenv("META_TEMPLATE_CONFIRMACAO", "novo_condominio")
META_TEMPLATE_LANG        = os.getenv("META_TEMPLATE_LANG", "pt_BR")

WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "true").lower() in ("1", "true")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_phone(phone: str) -> str:
    digits = "".join(filter(str.isdigit, str(phone or "")))
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _zapi_connected() -> bool:
    """Verifica se Z-API esta online e WhatsApp conectado."""
    if not ZAPI_ENABLED or not ZAPI_INSTANCE_ID or not ZAPI_TOKEN:
        return False
    try:
        r = requests.get(f"{ZAPI_API_URL}/status", timeout=5)
        if r.status_code == 200:
            return r.json().get("whatsappReady", False)
    except Exception as exc:
        logger.debug("Z-API status check falhou: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Envio via Z-API
# ---------------------------------------------------------------------------

def _send_zapi(phone: str, message: str) -> bool:
    url     = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Content-Type": "application/json"}
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN
    try:
        r    = requests.post(url, json={"phone": phone, "message": message}, headers=headers, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code < 300:
            if body.get("error") or body.get("erro"):
                logger.warning("Z-API erro resposta | tel=%s | %s", phone, body)
                return False
            mid = body.get("messageId") or body.get("zaapId")
            if mid or body.get("success") is True:
                logger.info("Z-API imediato OK | tel=%s | msgId=%s", phone, mid)
                return True
        logger.warning("Z-API imediato HTTP %s | tel=%s | %s", r.status_code, phone, body)
        return False
    except Exception as exc:
        logger.warning("Z-API imediato excecao | tel=%s | %s", phone, exc)
        return False


# ---------------------------------------------------------------------------
# Envio via Meta (template texto livre — sem botoes)
# ---------------------------------------------------------------------------

def _send_meta(phone: str, message: str) -> bool:
    if not META_ENABLED or not META_ACCESS_TOKEN:
        logger.warning("Meta nao configurado (META_ACCESS_TOKEN ausente)")
        return False

    # Trunca para limite Meta (1024 chars por parametro)
    msg_truncado = message[:1024].strip()

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": META_TEMPLATE_TEXTO_LIVRE,
            "language": {"code": META_TEMPLATE_LANG},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": msg_truncado}
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
        r    = requests.post(META_API_URL, json=payload, headers=headers, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code < 300:
            messages = body.get("messages", [])
            if messages:
                mid = messages[0].get("id")
                logger.info("Meta imediato OK | tel=%s | msgId=%s", phone, mid)
                return True
        err = body.get("error", {}).get("message", str(body))
        logger.warning("Meta imediato HTTP %s | tel=%s | erro=%s", r.status_code, phone, err)
        return False
    except Exception as exc:
        logger.warning("Meta imediato excecao | tel=%s | %s", phone, exc)
        return False


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def send_whatsapp_imediato(telefone: str, mensagem: str) -> bool:
    """
    Envia WhatsApp IMEDIATAMENTE (sem fila).
    Tenta Z-API primeiro; se falhar ou offline, usa Meta.

    Args:
        telefone: numero com ou sem DDI (sera normalizado para 55...)
        mensagem: texto livre da mensagem

    Returns:
        True se enviado com sucesso por qualquer provedor
    """
    if not WHATSAPP_ENABLED:
        logger.info("SIMULACAO send_whatsapp_imediato | tel=%s", telefone)
        return True

    phone = _normalize_phone(telefone)
    if not phone or len(phone) < 12:
        logger.error("send_whatsapp_imediato: telefone invalido | raw=%s", telefone)
        return False

    logger.info("send_whatsapp_imediato | tel=%s | len_msg=%s", phone, len(mensagem))

    # --- tenta Z-API ---
    if ZAPI_ENABLED and _zapi_connected():
        logger.info("Provedor: Z-API imediato | tel=%s", phone)
        if _send_zapi(phone, mensagem):
            return True
        logger.warning("Z-API falhou, acionando Meta | tel=%s", phone)
    else:
        logger.info("Z-API offline/desabilitada, usando Meta imediato | tel=%s", phone)

    # --- fallback Meta ---
    if _send_meta(phone, mensagem):
        return True

    logger.error("send_whatsapp_imediato: todos provedores falharam | tel=%s", phone)
    return False
