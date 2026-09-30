from pydantic import BaseModel
from typing import Optional, Dict

class ExtractedData(BaseModel):
    """Dados extraídos da etiqueta"""
    nome: str
    apartamento: str
    bloco: str
    codigo_barra: str

class OCRResponse(BaseModel):
    """Resposta do processamento OCR"""
    extracted_data: Optional[ExtractedData]
    full_text: Optional[str]
    status: str
    source: str
    error: Optional[str] = None

class OCRErrorResponse(BaseModel):
    """Resposta de erro"""
    error: str
    status: str = "error"
