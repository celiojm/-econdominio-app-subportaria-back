"""
================================================================================
ARQUIVO: auth_modelos.py
PASTA:   /home/visionlpr/encomenda_v2/backend/financeiro/
CAMINHO: /home/visionlpr/encomenda_v2/backend/financeiro/auth_modelos.py
================================================================================
Modelos SQLAlchemy e Schemas Pydantic para autenticação do módulo financeiro
Sistema próprio: Argon2/bcrypt, JWT, Refresh Tokens, Rate Limiting
================================================================================
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.database import Base
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ========================== ENUMS ==========================

class UsuarioTipoEnum(str, Enum):
    """Tipos de usuário do sistema financeiro"""
    admin = 'admin'           # Acesso total
    operador = 'operador'     # Acesso limitado
    visualizador = 'visualizador'  # Apenas leitura


# ========================== MODELOS SQLALCHEMY ==========================

class UsuarioFinanceiro(Base):
    """
    Modelo para usuários do módulo financeiro.
    Tabela: financeiro_usuarios
    """
    __tablename__ = "financeiro_usuarios"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    senha_hash = Column(String(255), nullable=False)  # Argon2 hash
    nome = Column(String(100), nullable=False)
    tipo = Column(String(20), default='operador')  # admin, operador, visualizador
    ativo = Column(Boolean, default=True)
    
    # Controle de acesso
    ultimo_login = Column(DateTime, nullable=True)
    login_falhos = Column(Integer, default=0)
    bloqueado_ate = Column(DateTime, nullable=True)
    
    # Auditoria
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    criado_por = Column(Integer, nullable=True)  # ID do admin que criou
    
    # Relacionamentos
    refresh_tokens = relationship("RefreshToken", back_populates="usuario", cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "nome": self.nome,
            "tipo": self.tipo,
            "ativo": self.ativo,
            "ultimo_login": self.ultimo_login.isoformat() if self.ultimo_login else None,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
        }


class RefreshToken(Base):
    """
    Modelo para refresh tokens.
    Permite revogação e controle de sessões.
    Tabela: financeiro_refresh_tokens
    """
    __tablename__ = "financeiro_refresh_tokens"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey('financeiro_usuarios.id', ondelete='CASCADE'), nullable=False)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)  # SHA256 do token
    dispositivo = Column(String(255), nullable=True)  # User-Agent ou identificador
    ip = Column(String(45), nullable=True)  # IPv4 ou IPv6
    
    # Validade
    expira_em = Column(DateTime, nullable=False)
    revogado = Column(Boolean, default=False)
    revogado_em = Column(DateTime, nullable=True)
    
    # Auditoria
    criado_em = Column(DateTime, default=datetime.utcnow)
    ultimo_uso = Column(DateTime, nullable=True)
    
    # Relacionamento
    usuario = relationship("UsuarioFinanceiro", back_populates="refresh_tokens")
    
    def to_dict(self):
        return {
            "id": self.id,
            "dispositivo": self.dispositivo,
            "ip": self.ip,
            "expira_em": self.expira_em.isoformat() if self.expira_em else None,
            "revogado": self.revogado,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
            "ultimo_uso": self.ultimo_uso.isoformat() if self.ultimo_uso else None,
        }


class LoginAttempt(Base):
    """
    Modelo para registro de tentativas de login (rate limiting).
    Tabela: financeiro_login_attempts
    """
    __tablename__ = "financeiro_login_attempts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(45), nullable=False, index=True)
    email = Column(String(255), nullable=True)  # Email tentado (se fornecido)
    sucesso = Column(Boolean, default=False)
    tentativa_em = Column(DateTime, default=datetime.utcnow)
    
    # Índice composto para consultas de rate limit
    __table_args__ = (
        Index('idx_ip_tentativa', 'ip', 'tentativa_em'),
    )


# ========================== SCHEMAS PYDANTIC ==========================

class LoginRequest(BaseModel):
    """Schema para requisição de login"""
    email: EmailStr
    password: str = Field(..., min_length=6)
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "admin@econdominio.com",
                "password": "senha123"
            }
        }


class LoginResponse(BaseModel):
    """Schema para resposta de login"""
    success: bool
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # segundos
    user: dict
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "access_token": "eyJ...",
                "refresh_token": "rt_...",
                "token_type": "Bearer",
                "expires_in": 900,
                "user": {"id": 1, "email": "admin@econdominio.com", "nome": "Admin"}
            }
        }


class RefreshRequest(BaseModel):
    """Schema para renovação de token"""
    refresh_token: str


class RefreshResponse(BaseModel):
    """Schema para resposta de refresh"""
    success: bool
    access_token: str
    expires_in: int


class UsuarioCreate(BaseModel):
    """Schema para criação de usuário (apenas admins)"""
    email: EmailStr
    password: str = Field(..., min_length=8)
    nome: str = Field(..., min_length=2, max_length=100)
    tipo: str = "operador"  # admin, operador, visualizador


class UsuarioUpdate(BaseModel):
    """Schema para atualização de usuário"""
    nome: Optional[str] = None
    tipo: Optional[str] = None
    ativo: Optional[bool] = None


class AlterarSenhaRequest(BaseModel):
    """Schema para alteração de senha"""
    senha_atual: str
    nova_senha: str = Field(..., min_length=8)


class UsuarioResponse(BaseModel):
    """Schema para resposta de usuário"""
    id: int
    email: str
    nome: str
    tipo: str
    ativo: bool
    ultimo_login: Optional[str] = None
    criado_em: Optional[str] = None


class TokenPayload(BaseModel):
    """Schema interno para payload do JWT"""
    sub: int  # user_id
    email: str
    nome: str
    tipo: str
    exp: datetime
    iat: datetime
    jti: str  # JWT ID único


class SessoesResponse(BaseModel):
    """Schema para listar sessões ativas"""
    sessoes: List[dict]
    total: int
