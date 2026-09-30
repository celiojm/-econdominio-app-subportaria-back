# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/app/auth/auth_dependencies.py
# ==============================================================================
# Dependências FastAPI para Autenticação e Autorização
# Middleware e injeção de dependências
# ==============================================================================

from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.auth.auth_utils import decode_access_token
from app.auth.auth_models import TokenData

# ============================================
# SECURITY SCHEME
# ============================================

security = HTTPBearer()

# ============================================
# DATABASE DEPENDENCY
# ============================================

def get_db():
    """Dependency para obter sessão do banco de dados"""
    import pymysql
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    connection = pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "AdmGeral"),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    
    try:
        yield connection
    finally:
        connection.close()


# ============================================
# TOKEN VALIDATION
# ============================================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db = Depends(get_db)
) -> TokenData:
    """Valida JWT token e retorna dados do usuário autenticado"""
    token = credentials.credentials
    
    try:
        payload = decode_access_token(token)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = int(payload.get("sub"))
    email = payload.get("email")
    id_condominio = payload.get("id_condominio")
    role = payload.get("role")
    nivel_id = payload.get("nivel_id")
    
    if not user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
    
    with db.cursor() as cursor:
        query = """
            SELECT id, email, nome, condominio_id, role, nivel_id, ativo, bloqueado_ate
            FROM mobile_operadores
            WHERE id = %s
        """
        cursor.execute(query, (user_id,))
        user = cursor.fetchone()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
        )
    
    if not user.get("ativo"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário desativado. Entre em contato com o administrador.",
        )
    
    bloqueado_ate = user.get("bloqueado_ate")
    if bloqueado_ate:
        from datetime import datetime
        if datetime.now() < bloqueado_ate:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Usuário bloqueado até {bloqueado_ate.strftime('%d/%m/%Y %H:%M')}",
            )
    
    return TokenData(
        user_id=user_id,
        email=email,
        id_condominio=id_condominio,
        role=role,
        nivel_id=nivel_id
    )


async def get_current_active_user(
    current_user: TokenData = Depends(get_current_user)
) -> TokenData:
    """Alias para get_current_user"""
    return current_user


# ============================================
# RBAC - ROLE-BASED ACCESS CONTROL
# ============================================

class RoleChecker:
    """Dependency class para verificar roles"""
    
    def __init__(self, allowed_roles: list):
        self.allowed_roles = allowed_roles
    
    def __call__(self, current_user: TokenData = Depends(get_current_user)):
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado. Roles permitidas: {', '.join(self.allowed_roles)}"
            )
        return current_user


# Helpers para roles específicas
require_admin_sistema = RoleChecker(["admin_sistema"])
require_admin = RoleChecker(["admin_sistema", "admin_condominio"])
require_porteiro = RoleChecker(["admin_sistema", "admin_condominio", "porteiro"])


# ============================================
# TENANT ISOLATION
# ============================================

def verify_tenant_access(condominio_id: int, current_user: TokenData = Depends(get_current_user)):
    """Verifica se usuário tem acesso ao condomínio especificado"""
    if current_user.role == "admin_sistema":
        return
    
    if current_user.id_condominio != condominio_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado a este condomínio"
        )


# ============================================
# CLIENT INFO
# ============================================

def get_client_ip(request: Request) -> str:
    """Obtém IP real do cliente"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    if request.client:
        return request.client.host
    
    return "unknown"


def get_user_agent(request: Request) -> Optional[str]:
    """Obtém User-Agent do request"""
    return request.headers.get("User-Agent")


# ============================================
# AUDIT LOG HELPER
# ============================================

async def log_audit(
    db,
    usuario_id: int,
    condominio_id: int,
    acao: str,
    entidade: Optional[str] = None,
    entidade_id: Optional[int] = None,
    detalhes: Optional[dict] = None,
    request: Request = None
):
    """Registra ação no log de auditoria"""
    import json
    
    ip_address = get_client_ip(request) if request else None
    user_agent = get_user_agent(request) if request else None
    detalhes_json = json.dumps(detalhes) if detalhes else None
    
    try:
        with db.cursor() as cursor:
            query = """
                INSERT INTO mobile_audit_logs 
                (usuario_id, condominio_id, acao, entidade, entidade_id, detalhes, ip, user_agent, criado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(query, (
                usuario_id, condominio_id, acao, entidade, entidade_id,
                detalhes_json, ip_address, user_agent
            ))
            db.commit()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Erro ao registrar audit log: {e}")
