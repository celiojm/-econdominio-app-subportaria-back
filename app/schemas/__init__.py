from pydantic import BaseModel, EmailStr, Field, field_validator
#from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

# Enum para status de encomenda
class EncomendaStatus(str, Enum):
    pendente = "pendente"
    entregue = "entregue"

# Auth Schemas
class LoginRequest(BaseModel):
    nome: str
    senha: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserInfo(BaseModel):
    id: int
    nome: str
    nivel: int
    condominio_id: int
    condominio_nome: Optional[str] = None

# Morador Schemas
class MoradorBase(BaseModel):
    nome: str
    apartamento: str
    bloco: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[EmailStr] = None

    @field_validator("telefone")
    @classmethod
    def validar_telefone(cls, v):
        if v is None or v.strip() == "":
            return v  # telefone continua opcional — não force preenchimento aqui
        digitos = "".join(filter(str.isdigit, v))
        # Celular BR: DDD (2) + 9 dígitos = 11, ou DDD+8 dígitos fixo = 10.
        # Menos que isso é impossível de ser um número real (ex: "19" sozinho).
        if len(digitos) < 10:
            raise ValueError(
                f"Telefone inválido: '{v}' tem {len(digitos)} dígito(s), "
                f"mínimo esperado é 10 (DDD + número)."
            )
        return v
class MoradorCreate(MoradorBase):
    pass

class MoradorUpdate(BaseModel):
    nome: Optional[str] = None
    apartamento: Optional[str] = None
    bloco: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[EmailStr] = None
    ativo: Optional[bool] = None

class MoradorCheck(BaseModel):
    nome: str
    apartamento: str
    bloco: Optional[str] = None

class MoradorResponse(MoradorBase):
    id: int
    condominio_id: int
    data_cadastro: Optional[datetime] = None
    ativo: Optional[bool] = True
    
    class Config:
        from_attributes = True

# Encomenda Schemas
class EncomendaBase(BaseModel):
    nome_destinatario: str
    apartamento: str
    bloco: Optional[str] = None
    codigo_rastreio: Optional[str] = None
    remetente: Optional[str] = None
    observacoes: Optional[str] = None
    telefone_morador: Optional[str] = None
    imagem_etiqueta: Optional[str] = None

class EncomendaCreate(EncomendaBase):
    morador_id: Optional[int] = None
    criar_morador: Optional[bool] = False

class EncomendaUpdate(BaseModel):
    nome_destinatario: Optional[str] = None
    apartamento: Optional[str] = None
    bloco: Optional[str] = None
    codigo_rastreio: Optional[str] = None
    remetente: Optional[str] = None
    observacoes: Optional[str] = None
    telefone_morador: Optional[str] = None

class EncomendaEntrega(BaseModel):
    nome_retirou: str
    documento_retirou: Optional[str] = None
    observacoes_entrega: Optional[str] = None

class EncomendaResponse(EncomendaBase):
    id: int
    condominio_id: int
    status: EncomendaStatus
    data_recebimento: datetime
    data_entrega: Optional[datetime] = None
    morador_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class EncomendaListResponse(BaseModel):
    total: int
    items: List[EncomendaResponse]

# Dashboard Schemas
class DashboardStats(BaseModel):
    total_encomendas: int
    pendentes: int
    entregues: int
    recebidas_hoje: int
    entregues_hoje: int

# WhatsApp Schemas
class WhatsAppMessage(BaseModel):
    to: str
    message: str
    imageBase64: Optional[str] = None

class WhatsAppStatus(BaseModel):
    connected: bool
    qr_code: Optional[str] = None
    message: str

# Error Schemas
class ErrorResponse(BaseModel):
    detail: str

# OCR Schemas
class OCRRequest(BaseModel):
    image_base64: str

class OCRResponse(BaseModel):
    success: bool
    text: Optional[str] = None
    nome: Optional[str] = None
    apartamento: Optional[str] = None
    bloco: Optional[str] = None
    codigo_rastreio: Optional[str] = None
    error: Optional[str] = None

# Exportar todas as classes
__all__ = [
    "LoginRequest", "Token", "UserInfo",
    "MoradorBase", "MoradorCreate", "MoradorUpdate", "MoradorCheck", "MoradorResponse",
    "EncomendaBase", "EncomendaCreate", "EncomendaUpdate", "EncomendaEntrega",
    "EncomendaResponse", "EncomendaListResponse", "EncomendaStatus",
    "DashboardStats", "WhatsAppMessage", "WhatsAppStatus",
    "ErrorResponse", "OCRRequest", "OCRResponse"
]
