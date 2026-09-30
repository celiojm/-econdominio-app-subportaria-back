# ================================================================================
# ARQUIVO: auth_models.py - Modelos de Autenticação de Afiliados
# ================================================================================

from pydantic import BaseModel, Field, validator
from typing import Optional
import re


class LoginRequest(BaseModel):
    """
    Login flexível: aceita email OU WhatsApp
    
    Exemplos:
    - {"identificador": "teste@econdominio.com.br", "senha": "teste123"}
    - {"identificador": "4830351252", "senha": "teste123"}
    - {"identificador": "(48) 3035-1252", "senha": "teste123"}
    """
    identificador: str = Field(..., description="Email ou WhatsApp")
    senha: str = Field(..., min_length=6)

    def get_identificador(self) -> str:
        """Retorna identificador normalizado"""
        return self.identificador.strip()

    def is_email(self) -> bool:
        """Verifica se é email"""
        return '@' in self.identificador

    def is_whatsapp(self) -> bool:
        """Verifica se é WhatsApp"""
        numeros = re.sub(r'\D', '', self.identificador)
        return len(numeros) >= 10 and not '@' in self.identificador

    def get_whatsapp_normalizado(self) -> str:
        """Retorna apenas números"""
        return re.sub(r'\D', '', self.identificador)

    class Config:
        json_schema_extra = {
            "example": {
                "identificador": "teste@econdominio.com.br ou 4830351252",
                "senha": "teste123"
            }
        }


class TokenResponse(BaseModel):
    """Resposta com token JWT"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400  # 24 horas
    user: dict


class AfiliadoResponse(BaseModel):
    """Dados do afiliado autenticado"""
    id: int
    usuario_id: int
    nome_completo: str
    email: str
    whatsapp: str
    codigo_afiliado: str
    tipo_comissao: str
    percentual: float
    is_admin: int
    ativo: bool
