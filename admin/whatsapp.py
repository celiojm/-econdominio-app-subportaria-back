# ============================================================================
# ARQUIVO: whatsapp.py
# PASTA: /home/visionlpr/backend/admin/
# DESCRIÇÃO: Integração WhatsApp via Z-API para envio de cobranças
# VERSÃO: 1.0.0 (Python 3.8 Compatible)
# ============================================================================

from __future__ import annotations
import os
import httpx
import logging
from typing import Optional, Tuple
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from .auth import get_current_user
from app.api.condominio import is_admin_master


# 2026-09-28: envio/status de WhatsApp só para admin master (antes qualquer login, até porteiro)
async def _somente_admin_master(current_user: dict = Depends(get_current_user)) -> dict:
    if not is_admin_master(current_user):
        raise HTTPException(status_code=403, detail="Apenas administrador do sistema")
    return current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/painel/whatsapp", tags=["WhatsApp"])

# ============================================
# Configurações Z-API
# ============================================
ZAPI_INSTANCE_ID = os.getenv("ZAPI_INSTANCE_ID", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
ZAPI_API_URL = os.getenv("ZAPI_API_URL", "https://api.z-api.io")
WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "true").lower() == "true"

# ============================================
# Models
# ============================================
class WhatsAppMessage(BaseModel):
    telefone: str
    mensagem: str

class WhatsAppCobranca(BaseModel):
    telefone: str
    valor: float
    descricao: Optional[str] = "Mensalidade"
    link_pagamento: Optional[str] = None
    condominio_nome: Optional[str] = None

# ============================================
# Funções auxiliares
# ============================================
def format_phone(phone: str) -> str:
    """Formata telefone para padrão internacional (55XXXXXXXXXXX)"""
    # Remove tudo que não é número
    numbers = ''.join(filter(str.isdigit, phone))
    
    # Se já começa com 55 e tem 12-13 dígitos, está ok
    if numbers.startswith('55') and len(numbers) in [12, 13]:
        return numbers
    
    # Se tem 10-11 dígitos (DDD + número), adiciona 55
    if len(numbers) in [10, 11]:
        return f"55{numbers}"
    
    # Se tem 8-9 dígitos (só número), assume DDD 48 (SC)
    if len(numbers) in [8, 9]:
        return f"5548{numbers}"
    
    return numbers

def format_currency(value: float) -> str:
    """Formata valor para moeda brasileira"""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

async def send_zapi_message(phone: str, message: str) -> Tuple[bool, str]:
    """Envia mensagem via Z-API"""
    if not WHATSAPP_ENABLED:
        return False, "WhatsApp desabilitado"
    
    if not all([ZAPI_INSTANCE_ID, ZAPI_TOKEN]):
        logger.error("Z-API não configurado: ZAPI_INSTANCE_ID ou ZAPI_TOKEN ausente")
        return False, "Z-API não configurado"
    
    formatted_phone = format_phone(phone)
    
    url = f"{ZAPI_API_URL}/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN
    
    payload = {
        "phone": formatted_phone,
        "message": message
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            logger.info(f"Z-API Response: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get("zapiMessageId") or data.get("messageId"):
                    return True, "Mensagem enviada com sucesso"
                return False, data.get("message", "Erro desconhecido")
            else:
                return False, f"Erro HTTP {response.status_code}: {response.text}"
                
    except httpx.TimeoutException:
        logger.error("Timeout ao conectar com Z-API")
        return False, "Timeout ao conectar com Z-API"
    except Exception as e:
        logger.error(f"Erro ao enviar WhatsApp: {str(e)}")
        return False, str(e)

# ============================================
# Rotas
# ============================================
@router.post("/enviar")
async def enviar_whatsapp(
    data: WhatsAppMessage,
    current_user: dict = Depends(_somente_admin_master)
):
    """Envia mensagem WhatsApp genérica"""
    success, message = await send_zapi_message(data.telefone, data.mensagem)
    
    if success:
        return {"success": True, "message": message}
    else:
        raise HTTPException(status_code=400, detail=message)

@router.post("/enviar-cobranca")
async def enviar_cobranca_whatsapp(
    data: WhatsAppCobranca,
    current_user: dict = Depends(_somente_admin_master)
):
    """Envia mensagem de cobrança formatada via WhatsApp"""
    
    # Monta mensagem de cobrança
    mensagem = f"*🏢 {data.condominio_nome or 'Condomínio'}*\n\n"
    mensagem += f"Olá! Segue os dados para pagamento:\n\n"
    mensagem += f"📋 *Descrição:* {data.descricao}\n"
    mensagem += f"💰 *Valor:* {format_currency(data.valor)}\n"
    
    if data.link_pagamento:
        mensagem += f"\n🔗 *Link para pagamento:*\n{data.link_pagamento}\n"
    
    mensagem += f"\n_Mensagem enviada automaticamente pelo sistema eCondomínio_"
    
    success, message = await send_zapi_message(data.telefone, mensagem)
    
    if success:
        logger.info(f"Cobrança enviada via WhatsApp para {data.telefone}")
        return {"success": True, "message": "Cobrança enviada com sucesso via WhatsApp"}
    else:
        logger.error(f"Falha ao enviar cobrança via WhatsApp: {message}")
        raise HTTPException(status_code=400, detail=message)

@router.get("/status")
async def whatsapp_status(current_user: dict = Depends(_somente_admin_master)):
    """Verifica status da integração WhatsApp/Z-API"""
    
    configured = all([ZAPI_INSTANCE_ID, ZAPI_TOKEN])
    
    return {
        "enabled": WHATSAPP_ENABLED,
        "configured": configured,
        "instance_id": ZAPI_INSTANCE_ID[:8] + "..." if ZAPI_INSTANCE_ID else None,
        "api_url": ZAPI_API_URL
    }
