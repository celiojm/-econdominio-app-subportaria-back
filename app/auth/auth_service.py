# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/app/auth/auth_service.py
# ==============================================================================
# Serviço de Autenticação Mobile
# Lógica de negócio para autenticação usando mobile_operadores
# ==============================================================================

import os
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict
import logging

from fastapi import HTTPException, status

from app.auth.auth_utils import (
    hash_password,
    verify_password,
    validate_password_strength,
    create_access_token,
    generate_refresh_token,
    hash_token,
    validate_email,
    ACCESS_TOKEN_EXPIRE,
    REFRESH_TOKEN_EXPIRE
)

# ============================================
# CONFIGURAÇÃO
# ============================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_WINDOW_MINUTES = int(os.getenv("LOGIN_ATTEMPT_WINDOW_MINUTES", "15"))
LOCKOUT_DURATION_MINUTES = int(os.getenv("LOCKOUT_DURATION_MINUTES", "30"))

# ============================================
# RATE LIMITING
# ============================================

def check_login_attempts(db, email: str, ip_address: str) -> Tuple[bool, Optional[str]]:
    """Verifica tentativas de login para proteção contra brute force"""
    window_start = datetime.now() - timedelta(minutes=LOGIN_WINDOW_MINUTES)
    
    with db.cursor() as cursor:
        query_email = """
            SELECT COUNT(*) as count 
            FROM mobile_login_attempts 
            WHERE email = %s AND sucesso = 0 AND tentativa_em > %s
        """
        cursor.execute(query_email, (email, window_start))
        result_email = cursor.fetchone()
        email_attempts = result_email['count'] if result_email else 0
        
        query_ip = """
            SELECT COUNT(*) as count 
            FROM mobile_login_attempts 
            WHERE ip = %s AND sucesso = 0 AND tentativa_em > %s
        """
        cursor.execute(query_ip, (ip_address, window_start))
        result_ip = cursor.fetchone()
        ip_attempts = result_ip['count'] if result_ip else 0
    
    if email_attempts >= MAX_LOGIN_ATTEMPTS:
        return False, f"Muitas tentativas falhas. Tente novamente em {LOCKOUT_DURATION_MINUTES} minutos"
    
    if ip_attempts >= MAX_LOGIN_ATTEMPTS * 3:
        return False, f"Muitas tentativas do seu IP. Tente novamente em {LOCKOUT_DURATION_MINUTES} minutos"
    
    return True, None


def log_login_attempt(db, email: str, ip_address: str, user_agent: Optional[str], success: bool, motivo_falha: Optional[str] = None):
    """Registra tentativa de login"""
    try:
        with db.cursor() as cursor:
            query = """
                INSERT INTO mobile_login_attempts 
                (email, ip, user_agent, sucesso, motivo_falha, tentativa_em)
                VALUES (%s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (email, ip_address, user_agent, 1 if success else 0, motivo_falha))
            db.commit()
    except Exception as e:
        logger.error(f"Erro ao registrar tentativa de login: {e}")
        db.rollback()


def update_failed_login_count(db, user_id: int, increment: bool = True):
    """Atualiza contador de logins falhos"""
    try:
        with db.cursor() as cursor:
            if increment:
                cursor.execute("UPDATE mobile_operadores SET login_falhos = login_falhos + 1 WHERE id = %s", (user_id,))
                cursor.execute("SELECT login_falhos FROM mobile_operadores WHERE id = %s", (user_id,))
                result = cursor.fetchone()
                
                if result and result['login_falhos'] >= MAX_LOGIN_ATTEMPTS:
                    bloqueado_ate = datetime.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                    cursor.execute("UPDATE mobile_operadores SET bloqueado_ate = %s WHERE id = %s", (bloqueado_ate, user_id))
            else:
                cursor.execute("UPDATE mobile_operadores SET login_falhos = 0, bloqueado_ate = NULL WHERE id = %s", (user_id,))
            db.commit()
    except Exception as e:
        logger.error(f"Erro ao atualizar contador: {e}")
        db.rollback()


# ============================================
# AUTENTICAÇÃO
# ============================================

def authenticate_user(db, email: str, password: str) -> Optional[Dict]:
    """Autentica usuário"""
    with db.cursor() as cursor:
        query = """
            SELECT id, email, nome, senha_hash, condominio_id, role, nivel_id,
                   ativo, bloqueado_ate, login_falhos, ultimo_login, criado_em
            FROM mobile_operadores WHERE email = %s
        """
        cursor.execute(query, (email,))
        user = cursor.fetchone()
    
    if not user:
        return None
    
    if not user.get("ativo"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuário desativado")
    
    bloqueado_ate = user.get("bloqueado_ate")
    if bloqueado_ate and datetime.now() < bloqueado_ate:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bloqueado até {bloqueado_ate.strftime('%d/%m/%Y %H:%M')}"
        )
    
    if not verify_password(password, user['senha_hash']):
        update_failed_login_count(db, user['id'], increment=True)
        return None
    
    update_failed_login_count(db, user['id'], increment=False)
    return user


def create_tokens_for_user(user: Dict) -> Tuple[str, str]:
    """Cria tokens"""
    token_data = {
        "sub": str(user["id"]),
        "email": user["email"],
        "id_condominio": user["condominio_id"],
        "nivel_id": user.get("nivel_id", 3),
        "role": user.get("role", "morador")
    }
    
    access_token = create_access_token(token_data)
    refresh_token = generate_refresh_token()
    
    return access_token, refresh_token


def store_refresh_token(db, user_id: int, refresh_token: str, ip_address: str, user_agent: Optional[str], dispositivo: Optional[str] = None):
    """Armazena refresh token"""
    token_hash_value = hash_token(refresh_token)
    expira_em = datetime.now() + timedelta(days=REFRESH_TOKEN_EXPIRE)
    
    try:
        with db.cursor() as cursor:
            query = """
                INSERT INTO mobile_refresh_tokens 
                (usuario_id, token_hash, expira_em, ip, user_agent, dispositivo, criado_em)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (user_id, token_hash_value, expira_em, ip_address, user_agent, dispositivo))
            db.commit()
    except Exception as e:
        logger.error(f"Erro ao armazenar refresh token: {e}")
        db.rollback()
        raise


def verify_refresh_token(db, refresh_token: str) -> Optional[int]:
    """Verifica refresh token"""
    token_hash_value = hash_token(refresh_token)
    
    with db.cursor() as cursor:
        query = """
            SELECT usuario_id 
            FROM mobile_refresh_tokens 
            WHERE token_hash = %s AND expira_em > NOW() AND revogado = 0
        """
        cursor.execute(query, (token_hash_value,))
        result = cursor.fetchone()
        
        if result:
            try:
                cursor.execute("UPDATE mobile_refresh_tokens SET ultimo_uso = NOW() WHERE token_hash = %s", (token_hash_value,))
                db.commit()
            except:
                pass
    
    return result['usuario_id'] if result else None


def revoke_refresh_token(db, refresh_token: str):
    """Revoga token"""
    token_hash_value = hash_token(refresh_token)
    
    try:
        with db.cursor() as cursor:
            cursor.execute("UPDATE mobile_refresh_tokens SET revogado = 1, revogado_em = NOW() WHERE token_hash = %s", (token_hash_value,))
            db.commit()
    except Exception as e:
        logger.error(f"Erro ao revogar: {e}")
        db.rollback()


def revoke_all_user_tokens(db, user_id: int):
    """Revoga todos os tokens"""
    try:
        with db.cursor() as cursor:
            cursor.execute("UPDATE mobile_refresh_tokens SET revogado = 1, revogado_em = NOW() WHERE usuario_id = %s AND revogado = 0", (user_id,))
            db.commit()
    except Exception as e:
        logger.error(f"Erro: {e}")
        db.rollback()


def update_last_login(db, user_id: int):
    """Atualiza último login"""
    try:
        with db.cursor() as cursor:
            cursor.execute("UPDATE mobile_operadores SET ultimo_login = NOW() WHERE id = %s", (user_id,))
            db.commit()
    except Exception as e:
        logger.error(f"Erro: {e}")
        db.rollback()


def get_user_by_id(db, user_id: int) -> Optional[Dict]:
    """Busca usuário por ID"""
    with db.cursor() as cursor:
        query = """
            SELECT id, email, nome, telefone, condominio_id, role, nivel_id,
                   ativo, email_verificado, ultimo_login, criado_em
            FROM mobile_operadores WHERE id = %s
        """
        cursor.execute(query, (user_id,))
        return cursor.fetchone()


# ============================================
# REGISTRO
# ============================================

def create_user(db, email: str, nome: str, password: str, condominio_id: int,
                role: str = "morador", nivel_id: int = 3, telefone: Optional[str] = None,
                created_by_user_id: Optional[int] = None) -> Dict:
    """Cria usuário"""
    if not validate_email(email):
        raise HTTPException(status_code=400, detail="Email inválido")
    
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM mobile_operadores WHERE email = %s", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email já cadastrado")
        
        senha_hash_value = hash_password(password)
        
        try:
            query = """
                INSERT INTO mobile_operadores 
                (email, nome, telefone, senha_hash, condominio_id, role, nivel_id, ativo, criado_por, criado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, NOW())
            """
            cursor.execute(query, (email, nome, telefone, senha_hash_value, condominio_id, role, nivel_id, created_by_user_id))
            db.commit()
            
            cursor.execute("SELECT id, email, nome, condominio_id, role, nivel_id, ativo, criado_em FROM mobile_operadores WHERE email = %s", (email,))
            return cursor.fetchone()
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao criar usuário: {e}")
            raise HTTPException(status_code=500, detail="Erro ao criar usuário")
