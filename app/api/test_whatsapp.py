from fastapi import APIRouter, HTTPException
from app.services.whatsapp import whatsapp_service
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/test-whatsapp/{phone}")
async def test_whatsapp(phone: str):
    """Testar envio de WhatsApp"""
    try:
        message = "Teste de WhatsApp do Sistema de Encomendas"
        result = await whatsapp_service.send_text_message(phone, message)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro no teste: {str(e)}")
        return {"success": False, "error": str(e)}

@router.get("/test-connection")
async def test_connection():
    """Testar conexão com API WhatsApp"""
    try:
        status = await whatsapp_service.check_status()
        return {"success": True, "status": status}
    except Exception as e:
        return {"success": False, "error": str(e)}
