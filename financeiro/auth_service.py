"""
================================================================================
ARQUIVO: auth_service.py
PASTA:   /home/visionlpr/encomenda_v2/backend/financeiro/
CAMINHO: /home/visionlpr/encomenda_v2/backend/financeiro/auth_service.py
================================================================================
Serviço de autenticação para o módulo financeiro
- Hash de senhas com Argon2 (fallback bcrypt)
- JWT com expiração curta (15 min)
- Refresh tokens seguros no MySQL
- Revogação de tokens
- Rate limiting por IP
================================================================================
"""

import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

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

from .auth_modelos import (
    UsuarioFinanceiro, 
    RefreshToken, 
    LoginAttempt,
    TokenPayload
)

logger = logging.getLogger(__name__)

# ========================== CONFIGURAÇÕES ==========================

# JWT Settings
from dotenv import load_dotenv
load_dotenv()  # scripts/CLIs que importam este módulo sem o .env do systemd
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")  # 2026-09-28: sem valor padrão aleatório
if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY ausente no .env: o serviço não sobe sem chave de assinatura de login")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "15"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))

# Rate Limiting
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
BLOCK_DURATION_MINUTES = int(os.getenv("BLOCK_DURATION_MINUTES", "15"))
ATTEMPT_WINDOW_MINUTES = int(os.getenv("ATTEMPT_WINDOW_MINUTES", "5"))

# Logging
logger.info(f"🔐 Auth Service inicializado")
logger.info(f"   Hash Algorithm: {HASH_ALGO}")
logger.info(f"   JWT Expire: {JWT_ACCESS_TOKEN_EXPIRE_MINUTES} min")
logger.info(f"   Refresh Expire: {JWT_REFRESH_TOKEN_EXPIRE_DAYS} days")
logger.info(f"   Rate Limit: {MAX_LOGIN_ATTEMPTS} tentativas / {BLOCK_DURATION_MINUTES} min bloqueio")


# ========================== FUNÇÕES DE HASH ==========================

def hash_password(password: str) -> str:
    """
    Gera hash seguro da senha usando Argon2 ou bcrypt.
    """
    if HASH_ALGO == "argon2":
        return ph.hash(password)
    else:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """
    Verifica se a senha corresponde ao hash.
    """
    try:
        if HASH_ALGO == "argon2":
            ph.verify(hashed, password)
            return True
        else:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except (VerifyMismatchError, InvalidHash, ValueError, Exception):
        return False


def needs_rehash(hashed: str) -> bool:
    """
    Verifica se o hash precisa ser atualizado (para migração de algoritmos).
    """
    if HASH_ALGO == "argon2":
        try:
            return ph.check_needs_rehash(hashed)
        except:
            return True
    return False


# ========================== FUNÇÕES DE TOKEN ==========================

def create_access_token(user: UsuarioFinanceiro) -> Tuple[str, datetime]:
    """
    Cria um JWT access token com expiração curta.
    Retorna (token, expiration_datetime)
    """
    now = datetime.utcnow()
    expire = now + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "nome": user.nome,
        "tipo": user.tipo,
        "exp": expire,
        "iat": now,
        "jti": secrets.token_urlsafe(16)  # JWT ID único
    }
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, expire


def create_refresh_token(
    db: Session, 
    user: UsuarioFinanceiro, 
    ip: str = None, 
    device: str = None
) -> Tuple[str, RefreshToken]:
    """
    Cria um refresh token seguro e armazena no banco.
    Retorna (token_string, refresh_token_object)
    """
    # Gerar token aleatório
    token_raw = secrets.token_urlsafe(64)
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    expire = datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    
    # Criar registro no banco
    refresh_token = RefreshToken(
        usuario_id=user.id,
        token_hash=token_hash,
        dispositivo=device[:255] if device else None,
        ip=ip,
        expira_em=expire,
        criado_em=datetime.utcnow()
    )
    
    db.add(refresh_token)
    db.commit()
    db.refresh(refresh_token)
    
    # Retornar token prefixado para identificação
    return f"rt_{token_raw}", refresh_token


def verify_access_token(token: str) -> Optional[dict]:
    """
    Verifica e decodifica um access token JWT.
    Retorna o payload ou None se inválido.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"JWT inválido: {str(e)}")
        return None


def verify_refresh_token(db: Session, token: str) -> Optional[RefreshToken]:
    """
    Verifica um refresh token no banco de dados.
    Retorna o objeto RefreshToken ou None se inválido/expirado/revogado.
    """
    if not token.startswith("rt_"):
        return None
    
    token_raw = token[3:]  # Remove prefixo "rt_"
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    refresh_token = db.query(RefreshToken).filter(
        and_(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revogado == False,
            RefreshToken.expira_em > datetime.utcnow()
        )
    ).first()
    
    if refresh_token:
        # Atualizar último uso
        refresh_token.ultimo_uso = datetime.utcnow()
        db.commit()
    
    return refresh_token


def revoke_refresh_token(db: Session, token: str) -> bool:
    """
    Revoga um refresh token específico.
    """
    if not token.startswith("rt_"):
        return False
    
    token_raw = token[3:]
    token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
    
    refresh_token = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash
    ).first()
    
    if refresh_token:
        refresh_token.revogado = True
        refresh_token.revogado_em = datetime.utcnow()
        db.commit()
        return True
    
    return False


def revoke_all_user_tokens(db: Session, user_id: int) -> int:
    """
    Revoga todos os refresh tokens de um usuário.
    Útil para logout de todas as sessões.
    Retorna quantidade de tokens revogados.
    """
    result = db.query(RefreshToken).filter(
        and_(
            RefreshToken.usuario_id == user_id,
            RefreshToken.revogado == False
        )
    ).update({
        RefreshToken.revogado: True,
        RefreshToken.revogado_em: datetime.utcnow()
    })
    
    db.commit()
    return result


def cleanup_expired_tokens(db: Session) -> int:
    """
    Remove tokens expirados ou revogados há mais de 30 dias.
    Chamar periodicamente (ex: via cron ou startup).
    """
    cutoff = datetime.utcnow() - timedelta(days=30)
    
    deleted = db.query(RefreshToken).filter(
        (RefreshToken.expira_em < cutoff) | 
        (RefreshToken.revogado_em < cutoff)
    ).delete()
    
    db.commit()
    logger.info(f"🧹 Limpeza: {deleted} tokens removidos")
    return deleted


# ========================== RATE LIMITING ==========================

def check_rate_limit(db: Session, ip: str) -> Tuple[bool, Optional[int]]:
    """
    Verifica se o IP está dentro do limite de tentativas.
    Retorna (permitido, segundos_restantes_bloqueio)
    """
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=ATTEMPT_WINDOW_MINUTES)
    
    # Contar tentativas recentes
    attempts = db.query(LoginAttempt).filter(
        and_(
            LoginAttempt.ip == ip,
            LoginAttempt.sucesso == False,
            LoginAttempt.tentativa_em > window_start
        )
    ).count()
    
    if attempts >= MAX_LOGIN_ATTEMPTS:
        # Calcular tempo restante de bloqueio
        oldest_attempt = db.query(LoginAttempt).filter(
            and_(
                LoginAttempt.ip == ip,
                LoginAttempt.sucesso == False,
                LoginAttempt.tentativa_em > window_start
            )
        ).order_by(LoginAttempt.tentativa_em.asc()).first()
        
        if oldest_attempt:
            block_until = oldest_attempt.tentativa_em + timedelta(minutes=BLOCK_DURATION_MINUTES)
            if now < block_until:
                remaining = int((block_until - now).total_seconds())
                return False, remaining
    
    return True, None


def record_login_attempt(
    db: Session, 
    ip: str, 
    email: str = None, 
    success: bool = False
):
    """
    Registra uma tentativa de login.
    """
    attempt = LoginAttempt(
        ip=ip,
        email=email,
        sucesso=success,
        tentativa_em=datetime.utcnow()
    )
    db.add(attempt)
    db.commit()
    
    if success:
        # Limpar tentativas antigas deste IP após sucesso
        db.query(LoginAttempt).filter(
            and_(
                LoginAttempt.ip == ip,
                LoginAttempt.sucesso == False
            )
        ).delete()
        db.commit()


def cleanup_old_attempts(db: Session) -> int:
    """
    Remove tentativas de login antigas (mais de 24h).
    """
    cutoff = datetime.utcnow() - timedelta(hours=24)
    deleted = db.query(LoginAttempt).filter(
        LoginAttempt.tentativa_em < cutoff
    ).delete()
    db.commit()
    return deleted


# ========================== FUNÇÕES DE USUÁRIO ==========================

def get_user_by_email(db: Session, email: str) -> Optional[UsuarioFinanceiro]:
    """
    Busca usuário por email.
    """
    return db.query(UsuarioFinanceiro).filter(
        UsuarioFinanceiro.email == email.lower()
    ).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[UsuarioFinanceiro]:
    """
    Busca usuário por ID.
    """
    return db.query(UsuarioFinanceiro).filter(
        UsuarioFinanceiro.id == user_id
    ).first()


def create_user(
    db: Session,
    email: str,
    password: str,
    nome: str,
    tipo: str = "operador",
    created_by: int = None
) -> UsuarioFinanceiro:
    """
    Cria um novo usuário.
    """
    user = UsuarioFinanceiro(
        email=email.lower(),
        senha_hash=hash_password(password),
        nome=nome,
        tipo=tipo,
        criado_por=created_by,
        criado_em=datetime.utcnow()
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    logger.info(f"✅ Usuário criado: {email} (tipo: {tipo})")
    return user


def update_user_password(
    db: Session,
    user: UsuarioFinanceiro,
    new_password: str
) -> bool:
    """
    Atualiza a senha do usuário.
    """
    user.senha_hash = hash_password(new_password)
    user.atualizado_em = datetime.utcnow()
    db.commit()
    
    # Revogar todos os tokens existentes (forçar re-login)
    revoke_all_user_tokens(db, user.id)
    
    logger.info(f"🔑 Senha alterada para: {user.email}")
    return True


def update_last_login(db: Session, user: UsuarioFinanceiro):
    """
    Atualiza timestamp do último login.
    """
    user.ultimo_login = datetime.utcnow()
    user.login_falhos = 0  # Reset contador de falhas
    db.commit()


def increment_failed_login(db: Session, user: UsuarioFinanceiro):
    """
    Incrementa contador de logins falhos.
    """
    user.login_falhos = (user.login_falhos or 0) + 1
    
    # Bloquear após muitas falhas
    if user.login_falhos >= MAX_LOGIN_ATTEMPTS:
        user.bloqueado_ate = datetime.utcnow() + timedelta(minutes=BLOCK_DURATION_MINUTES)
        logger.warning(f"🚫 Usuário bloqueado: {user.email}")
    
    db.commit()


def is_user_blocked(user: UsuarioFinanceiro) -> Tuple[bool, Optional[int]]:
    """
    Verifica se o usuário está bloqueado.
    Retorna (bloqueado, segundos_restantes)
    """
    if not user.bloqueado_ate:
        return False, None
    
    now = datetime.utcnow()
    if now < user.bloqueado_ate:
        remaining = int((user.bloqueado_ate - now).total_seconds())
        return True, remaining
    
    return False, None


def get_user_sessions(db: Session, user_id: int) -> list:
    """
    Lista sessões ativas (refresh tokens) do usuário.
    """
    tokens = db.query(RefreshToken).filter(
        and_(
            RefreshToken.usuario_id == user_id,
            RefreshToken.revogado == False,
            RefreshToken.expira_em > datetime.utcnow()
        )
    ).all()
    
    return [t.to_dict() for t in tokens]


# ========================== EXPORTAÇÕES ==========================

__all__ = [
    # Hash
    'hash_password',
    'verify_password',
    'needs_rehash',
    
    # Tokens
    'create_access_token',
    'create_refresh_token',
    'verify_access_token',
    'verify_refresh_token',
    'revoke_refresh_token',
    'revoke_all_user_tokens',
    'cleanup_expired_tokens',
    
    # Rate Limiting
    'check_rate_limit',
    'record_login_attempt',
    'cleanup_old_attempts',
    
    # User
    'get_user_by_email',
    'get_user_by_id',
    'create_user',
    'update_user_password',
    'update_last_login',
    'increment_failed_login',
    'is_user_blocked',
    'get_user_sessions',
    
    # Config
    'JWT_ACCESS_TOKEN_EXPIRE_MINUTES',
]
