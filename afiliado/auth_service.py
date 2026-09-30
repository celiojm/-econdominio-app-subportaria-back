import os
import secrets
import jwt
import logging
from datetime import datetime, timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from typing import Optional, Tuple, Dict
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Configurações
# Lê a mesma SECRET_KEY do .env compartilhada pelos outros módulos de auth
# ativos (mobile/auth.py, mobile/auth_service.py, admin/auth_service.py,
# app/api|services/auth.py) — antes era hardcoded, ignorando o .env.
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

ph = PasswordHasher()


class AfiliadoAuthService:
    """Serviço de autenticação para afiliados"""
    
    @staticmethod
    def verificar_senha(senha: str, hash: str) -> bool:
        try:
            ph.verify(hash, senha)
            return True
        except VerifyMismatchError:
            return False
    
    @staticmethod
    def criar_token(user_id: int, email: str, nome: str, is_admin: int) -> str:
        payload = {
            "sub": user_id,
            "email": email,
            "nome": nome,
            "is_admin": is_admin,
            "exp": datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
            "iat": datetime.utcnow()
        }
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    @staticmethod
    def verificar_token(token: str) -> Optional[Dict]:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expirado")
            return None
        except jwt.InvalidTokenError:
            logger.warning("Token inválido")
            return None
    
    @staticmethod
    def autenticar_email(email: str, senha: str, db) -> Tuple[bool, Optional[Dict], Optional[str]]:
        try:
            afiliado = db.execute(text("""
                SELECT 
                    a.id, a.afiliado_usuario_id, a.nome_completo, a.email, a.whatsapp,
                    a.codigo_afiliado, a.tipo_comissao, a.percentual,
                    a.is_admin, a.ativo,
                    au.senha_hash
                FROM afiliados a
                INNER JOIN afiliado_usuarios au ON au.id = a.afiliado_usuario_id
                WHERE a.email = :email AND a.ativo = 1
            """), {"email": email}).fetchone()
            
            if not afiliado:
                return False, None, "Email não encontrado"
            
            if not AfiliadoAuthService.verificar_senha(senha, afiliado.senha_hash):
                return False, None, "Senha incorreta"
            
            user_data = {
                'id': afiliado.id,
                'afiliado_id': afiliado.id,
                'nome': afiliado.nome_completo,
                'email': afiliado.email,
                'whatsapp': afiliado.whatsapp,
                'codigo_afiliado': afiliado.codigo_afiliado,
                'is_admin': afiliado.is_admin
            }
            
            return True, user_data, None
            
        except Exception as e:
            logger.error(f"Erro na autenticação por email: {e}")
            return False, None, str(e)
    
    @staticmethod
    def autenticar_whatsapp(whatsapp: str, senha: str, db) -> Tuple[bool, Optional[Dict], Optional[str]]:
        try:
            afiliado = db.execute(text("""
                SELECT 
                    a.id, a.afiliado_usuario_id, a.nome_completo, a.email, a.whatsapp,
                    a.codigo_afiliado, a.tipo_comissao, a.percentual,
                    a.is_admin, a.ativo,
                    au.senha_hash
                FROM afiliados a
                INNER JOIN afiliado_usuarios au ON au.id = a.afiliado_usuario_id
                WHERE a.whatsapp = :whatsapp AND a.ativo = 1
            """), {"whatsapp": whatsapp}).fetchone()
            
            if not afiliado:
                return False, None, "WhatsApp não encontrado"
            
            if not AfiliadoAuthService.verificar_senha(senha, afiliado.senha_hash):
                return False, None, "Senha incorreta"
            
            user_data = {
                'id': afiliado.id,
                'afiliado_id': afiliado.id,
                'nome': afiliado.nome_completo,
                'email': afiliado.email,
                'whatsapp': afiliado.whatsapp,
                'codigo_afiliado': afiliado.codigo_afiliado,
                'is_admin': afiliado.is_admin
            }
            
            return True, user_data, None
            
        except Exception as e:
            logger.error(f"Erro na autenticação por WhatsApp: {e}")
            return False, None, str(e)


afiliado_auth_service = AfiliadoAuthService()
