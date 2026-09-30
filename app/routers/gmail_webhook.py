#!/usr/bin/env python3
"""
gmail_webhook.py — Processa comandos recebidos via WhatsApp
Comandos: RESUMO, APAGAR, AJUDA
"""

import os
import logging
import requests
import subprocess
from fastapi import APIRouter
from pydantic import BaseModel
from pathlib import Path

router = APIRouter(prefix="/gmail", tags=["gmail"])

log = logging.getLogger("gmail_webhook")

NOTIFY_PHONE  = '554884046118'
PRIMARY_URL   = os.getenv('ZAPI_API_URL', 'http://191.252.221.192:8080')
ZAPI_INSTANCE = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN_WA = os.getenv("ZAPI_TOKEN", "")
RESUMO_SCRIPT = '/home/visionlpr/gmail_resumo/resumo_gmail.py'
PYTHON_BIN    = '/home/visionlpr/backend/venv/bin/python3'
ULTIMO_RESUMO = Path('/home/visionlpr/gmail_resumo/logs/ultimo_resumo.txt')
IDS_APAGAR    = Path('/home/visionlpr/gmail_resumo/logs/ids_apagar.txt')
APAGAR_SCRIPT = '/home/visionlpr/gmail_resumo/apagar_emails.py'


class WebhookPayload(BaseModel):
    phone: str
    comando: str


def enviar_whatsapp(mensagem: str):
    send_url = f"{PRIMARY_URL}/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN_WA}/send-text"
    max_len = 1500
    partes = [mensagem[i:i+max_len] for i in range(0, len(mensagem), max_len)]
    for parte in partes:
        try:
            r = requests.post(send_url, json={'phone': NOTIFY_PHONE, 'message': parte}, timeout=10)
            r.raise_for_status()
        except Exception as e:
            log.error(f"Erro WhatsApp: {e}")


@router.post("/comando")
async def processar_comando(payload: WebhookPayload):
    comando = payload.comando.strip().upper()
    log.info(f"Comando recebido: {comando} de {payload.phone}")

    if comando == 'AJUDA':
        msg = (
            "Comandos disponíveis:\n\n"
            "RESUMO — Gera novo resumo agora\n"
            "APAGAR — Apaga e-mails marcados como pode apagar\n"
            "AJUDA  — Mostra esta mensagem"
        )
        enviar_whatsapp(msg)
        return {"ok": True, "acao": "ajuda"}

    if comando == 'RESUMO':
        enviar_whatsapp("Gerando resumo, aguarde...")
        try:
            subprocess.Popen([PYTHON_BIN, RESUMO_SCRIPT])
            return {"ok": True, "acao": "resumo_iniciado"}
        except Exception as e:
            enviar_whatsapp(f"Erro ao gerar resumo: {e}")
            return {"ok": False, "erro": str(e)}


    if comando == 'APAGAR':
        try:
            if not IDS_APAGAR.exists():
                enviar_whatsapp("Nenhum e-mail marcado para apagar. Envie RESUMO primeiro.")
                return {"ok": True}
            ids = [l.strip() for l in IDS_APAGAR.read_text().splitlines() if l.strip()]
            if not ids:
                enviar_whatsapp("Nenhum e-mail para apagar.")
                return {"ok": True}
            subprocess.Popen([PYTHON_BIN, APAGAR_SCRIPT])
            enviar_whatsapp(f"Apagando {len(ids)} e-mails, aguarde...")
            return {"ok": True, "acao": "apagar_iniciado", "total": len(ids)}
        except Exception as e:
            enviar_whatsapp(f"Erro ao apagar: {e}")
            return {"ok": False, "erro": str(e)}
   # ── IRESUMO ────────────────────────────────────────────────────────────────
    if comando == 'IRESUMO':
        enviar_whatsapp("⏳ Gerando resumo InforSeg, aguarde...")
        try:
            subprocess.Popen([PYTHON_BIN, '/home/visionlpr/gmail_resumo/resumo_inforseg.py'])
            return {"ok": True, "acao": "iresumo_iniciado"}
        except Exception as e:
            enviar_whatsapp(f"❌ Erro ao gerar resumo InforSeg: {e}")
            return {"ok": False, "erro": str(e)}

    # ── IAPAGAR ────────────────────────────────────────────────────────────────
    if comando == 'IAPAGAR':
        try:
            ids_file = Path('/home/visionlpr/gmail_resumo/logs/ids_apagar_inforseg.txt')
            if not ids_file.exists():
                enviar_whatsapp("ℹ️ Nenhum e-mail marcado para apagar. Envie IRESUMO primeiro.")
                return {"ok": True}
            ids = [l.strip() for l in ids_file.read_text().splitlines() if l.strip()]
            if not ids:
                enviar_whatsapp("ℹ️ Nenhum e-mail para apagar.")
                return {"ok": True}
            subprocess.Popen([PYTHON_BIN, '/home/visionlpr/gmail_resumo/apagar_inforseg.py'])
            enviar_whatsapp(f"🗑️ Apagando {len(ids)} e-mails InforSeg, aguarde...")
            return {"ok": True, "acao": "iapagar_iniciado", "total": len(ids)}
        except Exception as e:
            enviar_whatsapp(f"❌ Erro ao apagar: {e}")
            return {"ok": False, "erro": str(e)}

    # ── IAJUDA ─────────────────────────────────────────────────────────────────
    if comando == 'IAJUDA':
        msg = (
            "🏢 *Comandos InforSeg:*\n\n"
            "IRESUMO — Gera resumo InforSeg agora\n"
            "IAPAGAR — Apaga e-mails marcados\n"
            "IAJUDA  — Mostra esta mensagem\n\n"
            "📧 *Comandos Gmail pessoal:*\n\n"
            "RESUMO — Gera resumo pessoal\n"
            "APAGAR — Apaga e-mails marcados\n"
            "AJUDA  — Lista comandos pessoais"
        )
        enviar_whatsapp(msg)
        return {"ok": True, "acao": "iajuda"}      


    return {"ok": False, "erro": "Comando desconhecido"}
