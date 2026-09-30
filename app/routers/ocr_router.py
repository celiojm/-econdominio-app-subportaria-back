import os  # 2026-09-30: chave do OCR vem do ambiente
from fastapi import APIRouter, File, UploadFile, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import logging

from ..services.ocr_service import OCRService
from ..schemas.ocr_schema import OCRResponse, OCRErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/ocr",
    tags=["OCR"],
    responses={404: {"description": "Not found"}},
)

# Função de dependência para validar API Key
async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verifica se a API Key é válida"""
    if not x_api_key or x_api_key != os.getenv("ETIQUETA_API_KEY", ""):  # 2026-09-30: chave fora do código
        raise HTTPException(
            status_code=401,
            detail="API Key inválida ou não fornecida"
        )
    return x_api_key

@router.get("/")
async def ocr_status():
    """Endpoint para verificar status do serviço OCR"""
    return {"status": "online", "service": "OCR Local Service"}

@router.post("/processar_etiqueta", response_model=OCRResponse)
async def processar_etiqueta(
    image: UploadFile = File(...),
    api_key: str = Depends(verify_api_key)
):
    """
    Processa uma imagem de etiqueta e extrai informações usando OCR
    
    - **image**: Arquivo de imagem (JPG, PNG, etc.)
    - **X-API-Key**: Chave de API necessária no header
    
    Retorna:
    - **extracted_data**: Dados extraídos (nome, apartamento, bloco, código)
    - **full_text**: Texto completo extraído
    - **status**: Status do processamento
    """
    try:
        # Validar tipo de arquivo
        if not image.content_type or not image.content_type.startswith('image/'):
            raise HTTPException(
                status_code=400,
                detail="O arquivo enviado não é uma imagem válida"
            )
        
        # Processar a etiqueta
        logger.info(f"Processando etiqueta: {image.filename}")
        result = OCRService.process_label(image)
        
        # Verificar se houve erro no processamento
        if result["status"] == "error":
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Erro no processamento OCR")
            )
        
        return OCRResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao processar etiqueta: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao processar imagem: {str(e)}"
        )

@router.options("/processar_etiqueta")
async def options_processar_etiqueta():
    """
    Endpoint OPTIONS para CORS preflight
    """
    return JSONResponse(
        content="",
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "X-API-Key, Content-Type",
        }
    )
