"""
================================================================================
ARQUIVO: auth_models.py
PASTA:   ~/backend/mobile/
CAMINHO: /home/visionlpr/backend/mobile/auth_models.py
================================================================================
Modelos SQLAlchemy e Schemas Pydantic para autenticação do mobile
Sistema próprio: Argon2/bcrypt, JWT, Refresh Tokens, Rate Limiting
Com suporte a login por WhatsApp ou Email
================================================================================
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Index, Enum
from sqlalchemy.orm import relationship
from app.database import Base
from pydantic import BaseModel, Field, EmailStr, validator
from typing import Optional, List
from datetime import datetime
import enum as py_enum


# ========================== ENUMS ==========================

class RoleEnum(str, py_enum.Enum):
    """Tipos de usuário do sistema mobile"""
    admin_sistema    = 'admin_sistema'    # Acesso total ao sistema (condominio_id=1)
    admin_condominio = 'admin_condominio' # Gestão completa do próprio condomínio
    sindico          = 'sindico'          # Acesso total ao seu condomínio + criar usuários
    operador         = 'operador'         # Acesso ao seu condomínio (não cria usuários nem exclui)
    porteiro         = 'porteiro'         # Recebe e entrega encomendas
    morador          = 'morador'          # Visualiza apenas as próprias encomendas




# ========================== MODELOS SQLALCHEMY ==========================

class MobileOperador(Base):
    """
    Modelo para operadores do mobile.
    Tabela: mobile_operadores
    """
    __tablename__ = "mobile_operadores"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    nome = Column(String(150), nullable=False)
    telefone = Column(String(20), nullable=True, index=True)  # Para login por WhatsApp
    senha_hash = Column(String(255), nullable=False)
    condominio_id = Column(Integer, nullable=False, index=True)     
    # Role e permissões
    role = Column(
        Enum(RoleEnum, name='role_enum', native_enum=False),
        default=RoleEnum.operador,
        nullable=False,
        index=True
    )
    
    # Controle de acesso
    ativo = Column(Boolean, default=True, index=True)
    email_verificado = Column(Boolean, default=False)
    login_falhos = Column(Integer, default=0)
    bloqueado_ate = Column(DateTime, nullable=True)
    ultimo_login = Column(DateTime, nullable=True)
    
    # Auditoria
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    criado_por = Column(Integer, nullable=True)  # ID do admin/sindico que criou
    
    # Relacionamentos
    refresh_tokens = relationship("MobileRefreshToken", back_populates="operador", cascade="all, delete-orphan")
    
    # Índices compostos
    __table_args__ = (
        Index('idx_condominio_role', 'condominio_id', 'role'),
        Index('idx_telefone_condominio', 'telefone', 'condominio_id'),
    )
    
    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "nome": self.nome,
            "telefone": self.telefone,
            "condominio_id": self.condominio_id,
            "role": self.role.value if isinstance(self.role, RoleEnum) else self.role,
            "ativo": self.ativo,
            "email_verificado": self.email_verificado,
            "ultimo_login": self.ultimo_login.isoformat() if self.ultimo_login else None,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
        }


class MobileRefreshToken(Base):
    """
    Modelo para refresh tokens do mobile.
    Permite revogação e controle de sessões.
    Tabela: mobile_refresh_tokens
    """
    __tablename__ = "mobile_refresh_tokens"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    operador_id = Column(Integer, ForeignKey('mobile_operadores.id', ondelete='CASCADE'), nullable=False)
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
    operador = relationship("MobileOperador", back_populates="refresh_tokens")
    
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


class MobileLoginAttempt(Base):
    """
    Modelo para registro de tentativas de login (rate limiting).
    Tabela: mobile_login_attempts
    """
    __tablename__ = "mobile_login_attempts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(45), nullable=False, index=True)
    identifier = Column(String(255), nullable=True)  # Email ou telefone tentado
    sucesso = Column(Boolean, default=False)
    tentativa_em = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Índice composto para consultas de rate limit
    __table_args__ = (
        Index('idx_ip_tentativa', 'ip', 'tentativa_em'),
    )


class PasswordResetToken(Base):
    """
    Modelo para tokens de recuperação de senha.
    Tabela: mobile_password_reset_tokens
    """
    __tablename__ = "mobile_password_reset_tokens"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    operador_id = Column(Integer, ForeignKey('mobile_operadores.id', ondelete='CASCADE'), nullable=False)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    metodo_envio = Column(String(20), nullable=False)  # 'email' ou 'whatsapp'
    
    # Validade
    expira_em = Column(DateTime, nullable=False)
    usado = Column(Boolean, default=False)
    usado_em = Column(DateTime, nullable=True)
    
    # Auditoria
    criado_em = Column(DateTime, default=datetime.utcnow)
    ip_solicitacao = Column(String(45), nullable=True)


# ========================== SCHEMAS PYDANTIC ==========================

class LoginRequest(BaseModel):
    """Schema para requisição de login"""
    identifier: str = Field(..., description="Email ou telefone (WhatsApp)")
    password: str = Field(..., min_length=6)
    
    class Config:
        json_schema_extra = {
            "example": {
                "identifier": "admin@econdominio.app.br",
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
                "user": {
                    "id": 1,
                    "email": "admin@econdominio.app.br",
                    "nome": "Admin",
                    "role": "admin_sistema"
                }
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


class OperadorCreate(BaseModel):
    """Schema para criação de operador (síndico ou admin)"""
    email: EmailStr
    password: str = Field(..., min_length=8)
    nome: str = Field(..., min_length=2, max_length=150)
    telefone: Optional[str] = Field(None, max_length=20)
    role: str = "operador"  # admin_sistema, sindico, operador
    condominio_id: Optional[int] = None  # Obrigatório se não for admin_sistema


class OperadorUpdate(BaseModel):
    """Schema para atualização de operador"""
    nome: Optional[str] = None
    telefone: Optional[str] = None
    role: Optional[str] = None
    ativo: Optional[bool] = None


class AlterarSenhaRequest(BaseModel):
    """Schema para alteração de senha"""
    senha_atual: str
    nova_senha: str = Field(..., min_length=8)


class OperadorResponse(BaseModel):
    """Schema para resposta de operador"""
    id: int
    email: str
    nome: str
    telefone: Optional[str]
    condominio_id: int
    role: str
    ativo: bool
    email_verificado: bool
    ultimo_login: Optional[str] = None
    criado_em: Optional[str] = None


class EsqueciSenhaRequest(BaseModel):
    """Schema para solicitação de recuperação de senha"""
    identifier: str = Field(..., description="Email ou telefone")
    metodo_envio: str = Field(..., description="'email' ou 'whatsapp'")
    
    @validator('metodo_envio')
    def validate_metodo(cls, v):
        if v not in ['email', 'whatsapp']:
            raise ValueError("metodo_envio deve ser 'email' ou 'whatsapp'")
        return v


class RedefinirSenhaRequest(BaseModel):
    """Schema para redefinição de senha com token"""
    token: str
    nova_senha: str = Field(..., min_length=8)


class TokenPayload(BaseModel):
    """Schema interno para payload do JWT"""
    sub: int  # operador_id
    email: str
    nome: str
    role: str
    condominio_id: int
    exp: datetime
    iat: datetime
    jti: str  # JWT ID único


class SessoesResponse(BaseModel):
    """Schema para listar sessões ativas"""
    sessoes: List[dict]
    total: int
