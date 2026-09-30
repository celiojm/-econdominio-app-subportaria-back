# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/app/auth/auth_utils.py
# ==============================================================================
# ATUALIZADO: Usa SECRET_KEY ao invés de JWT_SECRET_KEY
# ==============================================================================

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import re

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, status

# ============================================
# CONFIGURAÇÃO
# ============================================

# Usar Argon2id (mais seguro) ou bcrypt como fallback
try:
    pwd_context = CryptContext(
        schemes=["argon2"],
        deprecated="auto",
        argon2__memory_cost=65536,
        argon2__time_cost=3,
        argon2__parallelism=4
    )
    HASH_ALGORITHM = "argon2"
except Exception:
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    HASH_ALGORITHM = "bcrypt"

# Carregar de variáveis de ambiente
import os
from dotenv import load_dotenv

load_dotenv()

# ATUALIZADO: Usa SECRET_KEY ao invés de JWT_SECRET_KEY
JWT_SECRET = os.getenv("SECRET_KEY")  # 2026-09-28: sem valor padrão
if not JWT_SECRET:
    raise RuntimeError("SECRET_KEY ausente no .env: o serviço não sobe sem chave de assinatura de login")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))

# ============================================
# PASSWORD HASHING
# ============================================

def hash_password(password: str) -> str:
    """Hash de senha usando Argon2id ou bcrypt"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha corresponde ao hash"""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def validate_password_strength(password: str) -> tuple:
    """Valida força da senha"""
    if len(password) < PASSWORD_MIN_LENGTH:
        return False, f"Senha deve ter no mínimo {PASSWORD_MIN_LENGTH} caracteres"
    
    if not re.search(r'[a-zA-Z]', password):
        return False, "Senha deve conter pelo menos uma letra"
    
    if not re.search(r'\d', password):
        return False, "Senha deve conter pelo menos um número"
    
    return True, None


# ============================================
# REFRESH TOKEN
# ============================================

def generate_refresh_token() -> str:
    """Gera refresh token seguro"""
    return secrets.token_urlsafe(64)


def hash_token(token: str) -> str:
    """Hash SHA256 de um token"""
    return hashlib.sha256(token.encode()).hexdigest()


# ============================================
# JWT ACCESS TOKEN
# ============================================

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Cria JWT access token"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE)
    
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decodifica e valida JWT access token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não foi possível validar as credenciais",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise credentials_exception


# ============================================
# VALIDAÇÕES
# ============================================

def validate_email(email: str) -> bool:
    """Validação simples de formato de email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


# ============================================
# INFORMAÇÕES DO SISTEMA
# ============================================

def get_auth_info() -> Dict[str, str]:
    """Retorna informações sobre configuração de autenticação"""
    return {
        "hash_algorithm": HASH_ALGORITHM,
        "jwt_algorithm": JWT_ALGORITHM,
        "access_token_expire_minutes": str(ACCESS_TOKEN_EXPIRE),
        "refresh_token_expire_days": str(REFRESH_TOKEN_EXPIRE),
        "password_min_length": str(PASSWORD_MIN_LENGTH)
    }
