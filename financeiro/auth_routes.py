"""
================================================================================
ARQUIVO: auth_routes.py
PASTA:   /home/visionlpr/encomenda_v2/backend/financeiro/
CAMINHO: /home/visionlpr/encomenda_v2/backend/financeiro/auth_routes.py
================================================================================
Rotas de autenticação do módulo financeiro
Endpoints: login, logout, refresh, me, alterar-senha, sessões
================================================================================
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.database import get_db
from .auth_modelos import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    UsuarioCreate,
    UsuarioUpdate,
    AlterarSenhaRequest,
    UsuarioResponse,
    UsuarioFinanceiro
)
from . import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Autenticação"])

# Security scheme
security = HTTPBearer(auto_error=False)


# ========================== HELPERS ==========================

def get_client_ip(request: Request) -> str:
    """Obtém IP real do cliente (considerando proxy)"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    return request.client.host if request.client else "unknown"


def get_device_info(request: Request) -> str:
    """Obtém informações do dispositivo"""
    return request.headers.get("User-Agent", "unknown")[:255]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> UsuarioFinanceiro:
    """
    Dependency para obter usuário atual a partir do JWT.
    Usar em rotas protegidas: user = Depends(get_current_user)
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token não fornecido",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    payload = auth_service.verify_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    user = auth_service.get_user_by_id(db, int(payload["sub"]))
    if not user or not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado ou inativo"
        )
    
    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[UsuarioFinanceiro]:
    """
    Dependency opcional - não falha se não houver token.
    """
    if not credentials:
        return None
    
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def require_admin(user: UsuarioFinanceiro = Depends(get_current_user)) -> UsuarioFinanceiro:
    """
    Dependency que exige usuário admin.
    """
    if user.tipo != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores"
        )
    return user


# ========================== ROTAS PÚBLICAS ==========================

@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Realiza login e retorna tokens JWT.
    
    Rate limiting: 5 tentativas por 5 minutos.
    Bloqueio: 15 minutos após exceder limite.
    """
    ip = get_client_ip(request)
    device = get_device_info(request)
    
    # Verificar rate limit por IP
    allowed, remaining_seconds = auth_service.check_rate_limit(db, ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas. Tente novamente em {remaining_seconds} segundos.",
            headers={"Retry-After": str(remaining_seconds)}
        )
    
    # Buscar usuário
    user = auth_service.get_user_by_email(db, data.email)
    
    if not user:
        # Registrar tentativa falha
        auth_service.record_login_attempt(db, ip, data.email, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas"
        )
    
    # Verificar se usuário está bloqueado
    blocked, block_remaining = auth_service.is_user_blocked(user)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Conta bloqueada. Tente novamente em {block_remaining} segundos.",
            headers={"Retry-After": str(block_remaining)}
        )
    
    # Verificar se usuário está ativo
    if not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada. Contate o administrador."
        )
    
    # Verificar senha
    if not auth_service.verify_password(data.password, user.senha_hash):
        auth_service.record_login_attempt(db, ip, data.email, success=False)
        auth_service.increment_failed_login(db, user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas"
        )
    
    # Login bem sucedido
    auth_service.record_login_attempt(db, ip, data.email, success=True)
    auth_service.update_last_login(db, user)
    
    # Gerar tokens
    access_token, access_expires = auth_service.create_access_token(user)
    refresh_token_str, _ = auth_service.create_refresh_token(db, user, ip, device)
    
    logger.info(f"✅ Login: {user.email} de {ip}")
    
    return LoginResponse(
        success=True,
        access_token=access_token,
        refresh_token=refresh_token_str,
        token_type="Bearer",
        expires_in=auth_service.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user.to_dict()
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(
    data: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Renova o access token usando um refresh token válido.
    """
    ip = get_client_ip(request)
    
    # Verificar refresh token
    refresh_token = auth_service.verify_refresh_token(db, data.refresh_token)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido ou expirado"
        )
    
    # Buscar usuário
    user = auth_service.get_user_by_id(db, refresh_token.usuario_id)
    if not user or not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado ou inativo"
        )
    
    # Gerar novo access token
    access_token, _ = auth_service.create_access_token(user)
    
    logger.debug(f"🔄 Token renovado: {user.email}")
    
    return RefreshResponse(
        success=True,
        access_token=access_token,
        expires_in=auth_service.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/logout")
async def logout(
    data: RefreshRequest,
    db: Session = Depends(get_db),
    user: UsuarioFinanceiro = Depends(get_current_user)
):
    """
    Revoga o refresh token atual (logout da sessão atual).
    """
    revoked = auth_service.revoke_refresh_token(db, data.refresh_token)
    
    if revoked:
        logger.info(f"🚪 Logout: {user.email}")
        return {"success": True, "message": "Logout realizado com sucesso"}
    
    return {"success": False, "message": "Token não encontrado"}


@router.post("/logout-all")
async def logout_all(
    db: Session = Depends(get_db),
    user: UsuarioFinanceiro = Depends(get_current_user)
):
    """
    Revoga TODOS os refresh tokens do usuário (logout de todas as sessões).
    """
    count = auth_service.revoke_all_user_tokens(db, user.id)
    
    logger.info(f"🚪 Logout ALL: {user.email} ({count} sessões)")
    
    return {
        "success": True,
        "message": f"Logout realizado em {count} sessão(ões)"
    }


# ========================== ROTAS PROTEGIDAS ==========================

@router.get("/me", response_model=UsuarioResponse)
async def get_me(user: UsuarioFinanceiro = Depends(get_current_user)):
    """
    Retorna dados do usuário logado.
    """
    return UsuarioResponse(**user.to_dict())


@router.get("/check")
async def check_auth(user: UsuarioFinanceiro = Depends(get_current_user)):
    """
    Verifica se o token é válido.
    """
    return {
        "authenticated": True,
        "user_id": user.id,
        "email": user.email,
        "tipo": user.tipo
    }


@router.put("/alterar-senha")
async def alterar_senha(
    data: AlterarSenhaRequest,
    db: Session = Depends(get_db),
    user: UsuarioFinanceiro = Depends(get_current_user)
):
    """
    Altera a senha do usuário logado.
    Revoga todos os tokens após alteração.
    """
    # Verificar senha atual
    if not auth_service.verify_password(data.senha_atual, user.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Senha atual incorreta"
        )
    
    # Atualizar senha
    auth_service.update_user_password(db, user, data.nova_senha)
    
    return {
        "success": True,
        "message": "Senha alterada com sucesso. Faça login novamente."
    }


@router.get("/sessoes")
async def listar_sessoes(
    db: Session = Depends(get_db),
    user: UsuarioFinanceiro = Depends(get_current_user)
):
    """
    Lista todas as sessões ativas do usuário.
    """
    sessions = auth_service.get_user_sessions(db, user.id)
    return {
        "sessoes": sessions,
        "total": len(sessions)
    }


@router.delete("/sessoes/{session_id}")
async def revogar_sessao(
    session_id: int,
    db: Session = Depends(get_db),
    user: UsuarioFinanceiro = Depends(get_current_user)
):
    """
    Revoga uma sessão específica do usuário.
    """
    from .auth_modelos import RefreshToken
    
    token = db.query(RefreshToken).filter(
        RefreshToken.id == session_id,
        RefreshToken.usuario_id == user.id
    ).first()
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sessão não encontrada"
        )
    
    token.revogado = True
    token.revogado_em = datetime.utcnow()
    db.commit()
    
    return {"success": True, "message": "Sessão revogada"}


# ========================== ROTAS ADMIN ==========================

@router.post("/usuarios", response_model=UsuarioResponse)
async def criar_usuario(
    data: UsuarioCreate,
    db: Session = Depends(get_db),
    admin: UsuarioFinanceiro = Depends(require_admin)
):
    """
    Cria um novo usuário (apenas admins).
    """
    # Verificar se email já existe
    existing = auth_service.get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email já cadastrado"
        )
    
    # Validar tipo
    if data.tipo not in ["admin", "operador", "visualizador"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipo inválido. Use: admin, operador ou visualizador"
        )
    
    user = auth_service.create_user(
        db=db,
        email=data.email,
        password=data.password,
        nome=data.nome,
        tipo=data.tipo,
        created_by=admin.id
    )
    
    return UsuarioResponse(**user.to_dict())


@router.get("/usuarios")
async def listar_usuarios(
    db: Session = Depends(get_db),
    admin: UsuarioFinanceiro = Depends(require_admin)
):
    """
    Lista todos os usuários (apenas admins).
    """
    users = db.query(UsuarioFinanceiro).all()
    return {
        "usuarios": [u.to_dict() for u in users],
        "total": len(users)
    }


@router.put("/usuarios/{user_id}")
async def atualizar_usuario(
    user_id: int,
    data: UsuarioUpdate,
    db: Session = Depends(get_db),
    admin: UsuarioFinanceiro = Depends(require_admin)
):
    """
    Atualiza um usuário (apenas admins).
    """
    user = auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado"
        )
    
    if data.nome is not None:
        user.nome = data.nome
    if data.tipo is not None:
        if data.tipo not in ["admin", "operador", "visualizador"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tipo inválido"
            )
        user.tipo = data.tipo
    if data.ativo is not None:
        user.ativo = data.ativo
        if not data.ativo:
            # Revogar todos os tokens se desativar
            auth_service.revoke_all_user_tokens(db, user.id)
    
    db.commit()
    
    return {"success": True, "user": user.to_dict()}


@router.delete("/usuarios/{user_id}")
async def deletar_usuario(
    user_id: int,
    db: Session = Depends(get_db),
    admin: UsuarioFinanceiro = Depends(require_admin)
):
    """
    Remove um usuário (apenas admins).
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível remover seu próprio usuário"
        )
    
    user = auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado"
        )
    
    db.delete(user)
    db.commit()
    
    return {"success": True, "message": f"Usuário {user.email} removido"}


# Importar datetime para revogar_sessao
from datetime import datetime
