# ~/encomenda_v2/backend/app/routers/routes_storage.py
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
import logging
from app.services.image_storage_service import image_storage_service

logger = logging.getLogger(__name__)
router = APIRouter()

# ✅ NOVO: Schema para upload via JSON
class UploadEtiquetaJSON(BaseModel):
    image: str
    encomenda_id: Optional[int] = None
    codigo_rastreio: Optional[str] = None

@router.post("/upload/etiqueta")
async def upload_etiqueta_json(payload: UploadEtiquetaJSON):
    """
    Upload de imagem de etiqueta via JSON (usado pelo frontend mobile)
    Nome do arquivo: etiqueta_{codigo_rastreio}_{timestamp}.jpg
    """
    try:
        logger.info(f"=== UPLOAD ETIQUETA VIA JSON ===")
        logger.info(f"Encomenda ID: {payload.encomenda_id or 'não fornecido'}")
        logger.info(f"Código de rastreio: {payload.codigo_rastreio or 'não fornecido'}")
        
        # Processar e fazer upload com código de rastreio
        resultado = await image_storage_service.process_and_upload_etiqueta(
            payload.image,
            identificador=None,
            codigo_rastreio=payload.codigo_rastreio  # ✅ PASSANDO O CÓDIGO!
        )
        
        logger.info(f"✅ Upload concluído: {resultado['nome_personalizado']}")
        
        return {
            "success": True,
            "filename": resultado["nome_servidor"],
            "url": resultado["url"],
            "nome_personalizado": resultado["nome_personalizado"]
        }
    except Exception as e:
        logger.error(f"❌ Erro no upload de etiqueta: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload/etiqueta/form")
async def upload_etiqueta_form(
    file: UploadFile = File(...),
    nome_destinatario: Optional[str] = Form(None),
    codigo_rastreio: Optional[str] = Form(None)
):
    """
    Upload de imagem de etiqueta via form-data (mantido para compatibilidade)
    """
    try:
        content = await file.read()
        
        # Converter bytes para base64
        import base64
        base64_image = f"data:image/jpeg;base64,{base64.b64encode(content).decode()}"
        
        resultado = await image_storage_service.process_and_upload_etiqueta(
            base64_image,
            identificador=nome_destinatario,
            codigo_rastreio=codigo_rastreio
        )
        
        return {
            "success": True,
            "filename": resultado["nome_servidor"],
            "url": resultado["url"],
            "nome_personalizado": resultado["nome_personalizado"]
        }
    except Exception as e:
        logger.error(f"Erro no upload de etiqueta (form): {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload/assinatura")
async def upload_assinatura(
    file: UploadFile = File(...),
    encomenda_id: int = Form(...)
):
    """Upload de assinatura"""
    try:
        content = await file.read()
        
        # Converter bytes para base64
        import base64
        base64_image = f"data:image/jpeg;base64,{base64.b64encode(content).decode()}"
        
        filename = await image_storage_service.process_and_upload_assinatura(
            base64_image,
            encomenda_id
        )
        
        return {
            "success": True,
            "filename": filename,
            "url": await image_storage_service.get_image_url(filename)
        }
    except Exception as e:
        logger.error(f"Erro no upload de assinatura: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/image/{filename}")
async def get_image_url(filename: str):
    """Obter URL de uma imagem"""
    try:
        url = await image_storage_service.get_image_url(filename)
        return {"url": url}
    except Exception as e:
        logger.error(f"Erro ao obter URL da imagem: {str(e)}")
        raise HTTPException(status_code=404, detail="Imagem não encontrada")
