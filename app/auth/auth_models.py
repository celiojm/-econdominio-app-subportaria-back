# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/app/auth/auth_models.py
# ==============================================================================
# Modelos Pydantic para Autenticação
# Schemas de request/response
# ==============================================================================

from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, Literal
from datetime import datetime

# ============================================
# REQUEST MODELS
# ============================================

class LoginRequest(BaseModel):
    """Request de login"""
    email: EmailStr = Field(..., description="Email do usuário")
    password: str = Field(..., min_length=1, description="Senha")
    
    class Config:
        schema_extra = {
            "example": {
                "email": "admin@condominio.com.br",
                "password": "senha123"
            }
        }


class RegisterRequest(BaseModel):
    """Request de registro de novo usuário"""
    email: EmailStr = Field(..., description="Email do usuário")
    nome: str = Field(..., min_length=3, max_length=150, description="Nome completo")
    password: str = Field(..., min_length=8, description="Senha")
    condominio_id: int = Field(..., gt=0, description="ID do condomínio")
    role: Literal["admin_condominio", "porteiro", "morador"] = Field(
        default="morador",
        description="Função do usuário"
    )
    nivel_id: int = Field(default=3, description="Nível de permissão")
    
    class Config:
        schema_extra = {
            "example": {
                "email": "porteiro@condominio.com.br",
                "nome": "João da Silva",
                "password": "senha123",
                "condominio_id": 1,
                "role": "porteiro",
                "nivel_id": 2
            }
        }


class RefreshTokenRequest(BaseModel):
    """Request de refresh token"""
    refresh_token: str = Field(..., description="Refresh token")


class LogoutRequest(BaseModel):
    """Request de logout"""
    refresh_token: Optional[str] = Field(None, description="Refresh token para revogar")


class ChangePasswordRequest(BaseModel):
    """Request de mudança de senha"""
    current_password: str = Field(..., description="Senha atual")
    new_password: str = Field(..., min_length=8, description="Nova senha")
    
    @validator('new_password')
    def validate_password(cls, v, values):
        if 'current_password' in values and v == values['current_password']:
            raise ValueError('Nova senha deve ser diferente da atual')
        return v


# ============================================
# RESPONSE MODELS
# ============================================

class TokenResponse(BaseModel):
    """Response com tokens de autenticação"""
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="Refresh token")
    token_type: str = Field(default="bearer", description="Tipo do token")
    expires_in: int = Field(..., description="Tempo de expiração em segundos")


class UserResponse(BaseModel):
    """Response com dados do usuário"""
    id: int
    email: str
    nome: str
    condominio_id: int
    role: str
    nivel_id: int
    ativo: bool
    ultimo_login: Optional[datetime]
    criado_em: Optional[datetime]
    
    class Config:
        orm_mode = True


class LoginResponse(BaseModel):
    """Response completa de login"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class MessageResponse(BaseModel):
    """Response genérica com mensagem"""
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    """Response de erro"""
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


# ============================================
# DATABASE MODELS (para type hints)
# ============================================

class TokenData(BaseModel):
    """Dados extraídos do JWT token"""
    user_id: int
    email: str
    id_condominio: int
    role: str
    nivel_id: int
