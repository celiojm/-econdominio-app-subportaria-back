"""
================================================================================
ARQUIVO: auth_service.py
PASTA:   ~/backend/mobile/
CAMINHO: /home/visionlpr/backend/mobile/auth_service.py
================================================================================
Serviço de autenticação para o mobile
- Hash de senhas com Argon2 (fallback bcrypt)
- JWT com expiração curta (30 min)
- Refresh tokens seguros no MySQL
- Revogação de tokens
- Rate limiting por IP
- Multi-condomínio isolado
- Login por email OU whatsapp
================================================================================
"""

import os
import secrets
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

# Tentar usar Argon2, fallback para bcrypt
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHash
    ph = PasswordHasher()
    HASH_ALGO = "argon2"
except ImportError:
    import bcrypt
    ph = None
    HASH_ALGO = "bcrypt"

from .auth_models import (
    MobileOperador,
    MobileRefreshToken,
    MobileLoginAttempt,
    RoleEnum
)

logger = logging.getLogger(__name__)

# ========================== CONFIGURAÇÕES ==========================

# JWT Settings
from dotenv import load_dotenv
load_dotenv()  # scripts/CLIs que importam este módulo sem o .env do systemd
JWT_SECRET_KEY = os.getenv("SECRET_KEY")  # 2026-09-28: sem valor padrão aleatório
if not JWT_SECRET_KEY:
    raise RuntimeError("SECRET_KEY ausente no .env: o serviço não sobe sem chave de assinatura de login")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

# Rate Limiting
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
BLOCK_DURATION_MINUTES = int(os.getenv("LOCKOUT_DURATION_MINUTES", "30"))
ATTEMPT_WINDOW_MINUTES = int(os.getenv("LOGIN_ATTEMPT_WINDOW_MINUTES", "15"))

# Logging
logger.info(f"🔐 Mobile Auth Service inicializado")
logger.info(f"   Hash Algorithm: {HASH_ALGO}")
logger.info(f"   JWT Expire: {JWT_ACCESS_TOKEN_EXPIRE_MINUTES} min")
logger.info(f"   Refresh Expire: {JWT_REFRESH_TOKEN_EXPIRE_DAYS} days")
logger.info(f"   Rate Limit: {MAX_LOGIN_ATTEMPTS} tentativas / {BLOCK_DURATION_MINUTES} min bloqueio")


# ========================== FUNÇÕES DE HASH ==========================

def hash_password(password: str) -> str:
    """Gera hash seguro da senha usando Argon2 ou bcrypt"""
    if HASH_ALGO == "argon2":
        return ph.hash(password)
    else:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verifica se a senha corresponde ao hash (suporta argon2 e bcrypt)"""
    try:
        if hashed.startswith('$argon2'):
            if ph is not None:
                ph.verify(hashed, password)
                return True
            else:
                logger.warning("Hash argon2 encontrado mas biblioteca não instalada")
                return False
        else:
            import bcrypt as _bcrypt
            return _bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

# ========================== FUNÇÕES DE TOKEN ==========================

def create_access_token(operador: MobileOperador) -> Tuple[str, datetime]:
    """
    Cria um JWT access token com expiração curta.
    Retorna (token, expiration_datetime)
    """
    now = datetime.utcnow()
    expire = now + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "sub": str(operador.id),
        "email": operador.email,
        "nome": operador.nome,
        "role": operador.role.value if isinstance(operador.role, RoleEnum) else operador.role,
        "condominio_id": operador.condominio_id,
        "exp": expire,
        "iat": now,
        "jti": secrets.token_urlsafe(16)
    }
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, expire


def create_refresh_token(
    db: Session,
    operador: MobileOperador,
    ip: str = None,
    device: str = None
) -> Tuple[str, MobileRefreshToken]:
    """
    Cria um refresh token seguro e armazena no banco.
    Retorna (token_string, refresh_token_object)
    """
    token_raw = secrets.token_urlsafe(64)
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    expire = datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    
    refresh_token = MobileRefreshToken(
        operador_id=operador.id,
        token_hash=token_hash,
        dispositivo=device[:255] if device else None,
        ip=ip,
        expira_em=expire,
        criado_em=datetime.utcnow()
    )
    
    db.add(refresh_token)
    db.commit()
    db.refresh(refresh_token)
    
    return f"rt_{token_raw}", refresh_token


def verify_access_token(token: str) -> Optional[dict]:
    """Verifica e decodifica um access token JWT"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"JWT inválido: {str(e)}")
        return None


def verify_refresh_token(db: Session, token: str) -> Optional[MobileRefreshToken]:
    """Verifica um refresh token no banco de dados"""
    if not token.startswith("rt_"):
        return None
    
    token_raw = token[3:]
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    refresh_token = db.query(MobileRefreshToken).filter(
        and_(
            MobileRefreshToken.token_hash == token_hash,
            MobileRefreshToken.revogado == False,
            MobileRefreshToken.expira_em > datetime.utcnow()
        )
    ).first()
    
    if refresh_token:
        refresh_token.ultimo_uso = datetime.utcnow()
        db.commit()
    
    return refresh_token


def revoke_refresh_token(db: Session, token: str) -> bool:
    """Revoga um refresh token específico"""
    if not token.startswith("rt_"):
        return False
    
    token_raw = token[3:]
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    refresh_token = db.query(MobileRefreshToken).filter(
        MobileRefreshToken.token_hash == token_hash
    ).first()
    
    if refresh_token:
        refresh_token.revogado = True
        refresh_token.revogado_em = datetime.utcnow()
        db.commit()
        return True
    
    return False


def revoke_all_user_tokens(db: Session, operador_id: int) -> int:
    """Revoga todos os refresh tokens de um operador"""
    result = db.query(MobileRefreshToken).filter(
        and_(
            MobileRefreshToken.operador_id == operador_id,
            MobileRefreshToken.revogado == False
        )
    ).update({
        "revogado": True,
        "revogado_em": datetime.utcnow()
    }, synchronize_session=False)
    
    db.commit()
    return result


# ========================== RATE LIMITING ==========================

def check_rate_limit(db: Session, ip: str) -> Tuple[bool, Optional[int]]:
    """Verifica se o IP está dentro do limite de tentativas"""
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=ATTEMPT_WINDOW_MINUTES)
    
    attempts = db.query(MobileLoginAttempt).filter(
        and_(
            MobileLoginAttempt.ip == ip,
            MobileLoginAttempt.sucesso == False,
            MobileLoginAttempt.tentativa_em > window_start
        )
    ).count()
    
    if attempts >= MAX_LOGIN_ATTEMPTS:
        oldest_attempt = db.query(MobileLoginAttempt).filter(
            and_(
                MobileLoginAttempt.ip == ip,
                MobileLoginAttempt.sucesso == False,
                MobileLoginAttempt.tentativa_em > window_start
            )
        ).order_by(MobileLoginAttempt.tentativa_em.asc()).first()
        
        if oldest_attempt:
            block_until = oldest_attempt.tentativa_em + timedelta(minutes=BLOCK_DURATION_MINUTES)
            if now < block_until:
                remaining = int((block_until - now).total_seconds())
                return False, remaining
    
    return True, None


def record_login_attempt(db: Session, ip: str, identifier: str = None, success: bool = False):
    """Registra uma tentativa de login"""
    attempt = MobileLoginAttempt(
        ip=ip,
        identifier=identifier,
        sucesso=success,
        tentativa_em=datetime.utcnow()
    )
    db.add(attempt)
    db.commit()
    
    if success:
        # Limpar tentativas antigas deste IP após sucesso
        db.query(MobileLoginAttempt).filter(
            and_(
                MobileLoginAttempt.ip == ip,
                MobileLoginAttempt.sucesso == False
            )
        ).delete(synchronize_session=False)
        db.commit()


# ========================== FUNÇÕES DE USUÁRIO ==========================

def normalize_phone(phone: str) -> str:
    """Normaliza número de telefone removendo caracteres especiais"""
    return re.sub(r'[^\d]', '', phone)


def get_operador_by_identifier(db: Session, identifier: str) -> Optional[MobileOperador]:
    """
    Busca operador por email OU telefone.
    identifier pode ser email ou número de telefone.
    """
    # Verificar se é email (contém @)
    if '@' in identifier:
        return db.query(MobileOperador).filter(
            MobileOperador.email == identifier.lower()
        ).first()
    
    # Se não é email, tenta como telefone
    phone_normalized = normalize_phone(identifier)
    return db.query(MobileOperador).filter(
        MobileOperador.telefone.like(f"%{phone_normalized}%")
    ).first()


def get_operador_by_id(db: Session, operador_id: int) -> Optional[MobileOperador]:
    """Busca operador por ID"""
    return db.query(MobileOperador).filter(
        MobileOperador.id == operador_id
    ).first()


def create_operador(
    db: Session,
    email: str,
    password: str,
    nome: str,
    condominio_id: int,
    role: str = "operador",
    telefone: str = None,
    created_by: int = None
) -> MobileOperador:
    """Cria um novo operador"""
    # Validar role
    if role not in [r.value for r in RoleEnum]:
        raise ValueError(f"Role inválida: {role}")
    
    # Validar condomínio para não-admins
    if role != "admin_sistema" and condominio_id == 1:
        raise ValueError("Apenas admin_sistema pode ter condominio_id=1")
    
    operador = MobileOperador(
        email=email.lower(),
        senha_hash=hash_password(password),
        nome=nome,
        telefone=normalize_phone(telefone) if telefone else None,
        condominio_id=condominio_id,
        role=role,
        criado_por=created_by,
        criado_em=datetime.utcnow()
    )
    
    db.add(operador)
    db.commit()
    db.refresh(operador)
    
    logger.info(f"✅ Operador criado: {email} (role: {role}, condomínio: {condominio_id})")
    return operador


def update_operador_password(db: Session, operador: MobileOperador, new_password: str) -> bool:
    """Atualiza a senha do operador"""
    operador.senha_hash = hash_password(new_password)
    operador.atualizado_em = datetime.utcnow()
    db.commit()
    
    # Revogar todos os tokens existentes
    revoke_all_user_tokens(db, operador.id)
    
    logger.info(f"🔑 Senha alterada para: {operador.email}")
    return True


def update_last_login(db: Session, operador: MobileOperador):
    """Atualiza timestamp do último login"""
    operador.ultimo_login = datetime.utcnow()
    operador.login_falhos = 0
    db.commit()


def increment_failed_login(db: Session, operador: MobileOperador):
    """Incrementa contador de logins falhos"""
    operador.login_falhos = (operador.login_falhos or 0) + 1
    
    if operador.login_falhos >= MAX_LOGIN_ATTEMPTS:
        operador.bloqueado_ate = datetime.utcnow() + timedelta(minutes=BLOCK_DURATION_MINUTES)
        logger.warning(f"🚫 Operador bloqueado: {operador.email}")
    
    db.commit()


def is_user_blocked(operador: MobileOperador) -> Tuple[bool, Optional[int]]:
    """Verifica se o operador está bloqueado"""
    if not operador.bloqueado_ate:
        return False, None
    
    now = datetime.utcnow()
    if now < operador.bloqueado_ate:
        remaining = int((operador.bloqueado_ate - now).total_seconds())
        return True, remaining
    
    return False, None


def get_user_sessions(db: Session, operador_id: int) -> list:
    """Lista sessões ativas (refresh tokens) do operador"""
    tokens = db.query(MobileRefreshToken).filter(
        and_(
            MobileRefreshToken.operador_id == operador_id,
            MobileRefreshToken.revogado == False,
            MobileRefreshToken.expira_em > datetime.utcnow()
        )
    ).all()
    
    return [t.to_dict() for t in tokens]


# ========================== PERMISSÕES ==========================

def check_permission_create_user(operador: MobileOperador, target_condominio_id: int, target_role: str) -> bool:
    """
    Verifica se operador pode criar usuário.
    
    Regras:
    - admin_sistema: pode criar qualquer usuário em qualquer condomínio
    - sindico: pode criar apenas operadores no seu próprio condomínio
    - operador: NÃO pode criar usuários
    """
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    
    # Admin sistema pode tudo
    if role == "admin_sistema":
        return True
    
    # Síndico pode criar apenas operadores no seu condomínio
    if role == "sindico":
        return (
            operador.condominio_id == target_condominio_id and
            target_role == "operador"
        )
    
    # Operador não pode criar ninguém
    return False


def check_permission_delete_encomenda(operador: MobileOperador, encomenda_condominio_id: int) -> bool:
    """
    Verifica se operador pode excluir encomenda.
    
    Regras:
    - admin_sistema: pode excluir de qualquer condomínio
    - sindico: pode excluir do seu condomínio
    - operador: NÃO pode excluir
    """
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    
    if role == "admin_sistema":
        return True
    
    if role == "sindico":
        return operador.condominio_id == encomenda_condominio_id
    
    return False


def check_permission_access_condominio(operador: MobileOperador, condominio_id: int) -> bool:
    """
    Verifica se operador pode acessar condomínio.
    
    Regras:
    - admin_sistema: acessa todos
    - sindico/operador: apenas o seu
    """
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    
    if role == "admin_sistema":
        return True
    
    return operador.condominio_id == condominio_id


# ========================== EXPORTAÇÕES ==========================

__all__ = [
    # Hash
    'hash_password',
    'verify_password',
    
    # Tokens
    'create_access_token',
    'create_refresh_token',
    'verify_access_token',
    'verify_refresh_token',
    'revoke_refresh_token',
    'revoke_all_user_tokens',
    
    # Rate Limiting
    'check_rate_limit',
    'record_login_attempt',
    
    # User
    'get_operador_by_identifier',
    'get_operador_by_id',
    'create_operador',
    'update_operador_password',
    'update_last_login',
    'increment_failed_login',
    'is_user_blocked',
    'get_user_sessions',
    
    # Permissions
    'check_permission_create_user',
    'check_permission_delete_encomenda',
    'check_permission_access_condominio',
    
    # Config
    'JWT_ACCESS_TOKEN_EXPIRE_MINUTES',
]
