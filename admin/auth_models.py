# ========================================
# Auth Models - Schemas Pydantic
# Módulo Admin - Autenticação flexível
# ========================================

from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List
from datetime import datetime
from enum import Enum
import re


class RoleEnum(str, Enum):
    """Roles disponíveis no sistema"""
    admin_sistema = "admin_sistema"
    admin_condominio = "admin_condominio"
    sindico = "sindico"
    operador = "operador"
    porteiro = "porteiro"
    morador = "morador"


class LoginRequest(BaseModel):
    """
    Login flexível: aceita email OU telefone (WhatsApp)
    
    Exemplos válidos:
    - {"identificador": "admin@econdominio.app.br", "senha": "123456"}
    - {"identificador": "48984046118", "senha": "123456"}
    - {"identificador": "+5548984046118", "senha": "123456"}
    
    Também aceita formato antigo com 'nome':
    - {"nome": "admin@econdominio.app.br", "senha": "123456"}
    """
    # Aceita tanto 'identificador' quanto 'nome' para compatibilidade
    identificador: Optional[str] = Field(None, description="Email ou telefone do usuário")
    nome: Optional[str] = Field(None, description="Alias para identificador (compatibilidade)")
    senha: str = Field(..., min_length=1, description="Senha do usuário")
    
    @validator('identificador', pre=True, always=True)
    def set_identificador(cls, v, values):
        """Se identificador não foi passado, usa 'nome'"""
        return v
    
    @validator('nome', pre=True, always=True)
    def validate_nome_or_identificador(cls, v, values):
        """Garante que pelo menos um foi passado"""
        identificador = values.get('identificador')
        if not v and not identificador:
            raise ValueError('Informe email ou telefone para login')
        return v
    
    def get_identificador(self) -> str:
        """Retorna o identificador (email ou telefone)"""
        return self.identificador or self.nome or ""
    
    def is_email(self) -> bool:
        """Verifica se o identificador é um email"""
        ident = self.get_identificador()
        return '@' in ident
    
    def is_telefone(self) -> bool:
        """Verifica se o identificador é um telefone"""
        ident = self.get_identificador()
        # Remove caracteres não numéricos
        numeros = re.sub(r'\D', '', ident)
        # Telefone brasileiro tem 10 ou 11 dígitos (com DDD)
        return len(numeros) >= 10 and len(numeros) <= 13
    
    def get_telefone_normalizado(self) -> str:
        """Retorna telefone apenas com números"""
        ident = self.get_identificador()
        return re.sub(r'\D', '', ident)


class TokenResponse(BaseModel):
    """Resposta de login com token JWT"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600  # 1 hora
    user: Optional['UserResponse'] = None


class UserResponse(BaseModel):
    """Dados do usuário retornados após login"""
    id: int
    email: str
    nome: str
    telefone: Optional[str] = None
    condominio_id: int
    condominio_nome: Optional[str] = None
    role: str
    nivel: int = Field(alias='nivel_id')
    ativo: bool = True
    
    class Config:
        from_attributes = True
        populate_by_name = True


class UserCreate(BaseModel):
    """Schema para criar novo usuário/operador"""
    email: str = Field(..., description="Email do usuário")
    nome: str = Field(..., min_length=2, max_length=150)
    telefone: Optional[str] = Field(None, max_length=20)
    senha: str = Field(..., min_length=6, description="Senha mínimo 6 caracteres")
    condominio_id: int
    role: RoleEnum = RoleEnum.operador
    nivel_id: int = 3
    ativo: bool = True
    
    @validator('email')
    def validate_email(cls, v):
        if '@' not in v:
            raise ValueError('Email inválido')
        return v.lower().strip()
    
    @validator('telefone')
    def validate_telefone(cls, v):
        if v:
            # Remove caracteres não numéricos
            numeros = re.sub(r'\D', '', v)
            if len(numeros) < 10 or len(numeros) > 13:
                raise ValueError('Telefone deve ter entre 10 e 13 dígitos')
            return numeros
        return v


class UserUpdate(BaseModel):
    """Schema para atualizar usuário/operador"""
    email: Optional[str] = None
    nome: Optional[str] = None
    telefone: Optional[str] = None
    senha: Optional[str] = None
    condominio_id: Optional[int] = None
    role: Optional[RoleEnum] = None
    nivel_id: Optional[int] = None
    ativo: Optional[bool] = None
    
    @validator('email')
    def validate_email(cls, v):
        if v and '@' not in v:
            raise ValueError('Email inválido')
        return v.lower().strip() if v else v
    
    @validator('telefone')
    def validate_telefone(cls, v):
        if v:
            numeros = re.sub(r'\D', '', v)
            if len(numeros) < 10 or len(numeros) > 13:
                raise ValueError('Telefone deve ter entre 10 e 13 dígitos')
            return numeros
        return v


class PasswordChange(BaseModel):
    """Schema para alteração de senha"""
    senha_atual: str
    nova_senha: str = Field(..., min_length=6)
    confirmar_senha: str
    
    @validator('confirmar_senha')
    def senhas_coincidem(cls, v, values):
        if 'nova_senha' in values and v != values['nova_senha']:
            raise ValueError('As senhas não coincidem')
        return v


class PasswordReset(BaseModel):
    """Schema para reset de senha (admin)"""
    nova_senha: str = Field(..., min_length=6, alias='senha')
    
    class Config:
        populate_by_name = True


# Atualiza referência forward
TokenResponse.model_rebuild()
