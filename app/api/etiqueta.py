from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
import base64
from PIL import Image
from io import BytesIO
import re

router = APIRouter()

class ProcessarEtiquetaRequest(BaseModel):
    image: str  # Base64 da imagem

class ProcessarEtiquetaResponse(BaseModel):
    success: bool
    nome: Optional[str] = None
    apartamento: Optional[str] = None
    bloco: Optional[str] = None
    codigo: Optional[str] = None
    message: Optional[str] = None

@router.post("/processar", response_model=ProcessarEtiquetaResponse)
async def processar_etiqueta(request: ProcessarEtiquetaRequest):
    """Processa imagem de etiqueta e extrai dados"""
    print(f"Recebido request para processar etiqueta")
    print(f"Tamanho da imagem base64: {len(request.image)} caracteres")
    
    try:
        # Por enquanto, vamos simular uma resposta de sucesso com dados mockados
        # Isso permite testar o fluxo completo
        return ProcessarEtiquetaResponse(
            success=True,
            nome="João da Silva",
            apartamento="101",
            bloco="A",
            codigo="1234567890",
            message="Dados extraídos com sucesso (mock)"
        )
            
    except Exception as e:
        print(f"Erro ao processar etiqueta: {str(e)}")
        return ProcessarEtiquetaResponse(
            success=False,
            message="Erro ao processar imagem. Preencha os dados manualmente."
        )

@router.get("/test")
async def test_etiqueta():
    """Endpoint de teste"""
    return {"status": "ok", "message": "API de etiqueta funcionando"}
