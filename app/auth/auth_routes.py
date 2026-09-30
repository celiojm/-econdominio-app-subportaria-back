# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/app/auth/auth_routes.py
# ==============================================================================
# Rotas de Autenticação FastAPI
# Endpoints para login, logout, refresh, registro, etc.
# ==============================================================================

from fastapi import APIRouter, Depends, HTTPException, status, Request

from app.auth.auth_models import (
    LoginRequest, LoginResponse, RefreshTokenRequest,
    LogoutRequest, RegisterRequest, UserResponse,
    MessageResponse, TokenResponse
)
from app.auth.auth_dependencies import (
    get_db, get_current_user, get_client_ip, get_user_agent,
    require_admin, log_audit, TokenData
)
from app.auth.auth_service import (
    authenticate_user, create_tokens_for_user, store_refresh_token,
    verify_refresh_token, revoke_refresh_token, revoke_all_user_tokens,
    update_last_login, get_user_by_id, create_user,
    check_login_attempts, log_login_attempt
)
from app.auth.auth_utils import ACCESS_TOKEN_EXPIRE

# ============================================
# ROUTER
# ============================================

router = APIRouter(prefix="/auth", tags=["Autenticação"])

# ============================================
# POST /auth/login
# ============================================

@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    credentials: LoginRequest,
    db=Depends(get_db)
):
    """Login com email e senha"""
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    
    # Verificar rate limiting
    is_allowed, error_msg = check_login_attempts(db, credentials.email, ip_address)
    if not is_allowed:
        log_login_attempt(db, credentials.email, ip_address, user_agent, False, "rate_limit")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_msg
        )
    
    # Autenticar usuário
    user = authenticate_user(db, credentials.email, credentials.password)
    
    if not user:
        log_login_attempt(db, credentials.email, ip_address, user_agent, False, "credenciais_invalidas")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Criar tokens
    access_token, refresh_token = create_tokens_for_user(user)
    
    # Armazenar refresh token
    store_refresh_token(db, user['id'], refresh_token, ip_address, user_agent)
    
    # Atualizar último login
    update_last_login(db, user['id'])
    
    # Registrar login bem sucedido
    log_login_attempt(db, credentials.email, ip_address, user_agent, True)
    
    # Log de auditoria
    await log_audit(db, user['id'], user['condominio_id'], "login", request=request)
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE * 60,
        user=UserResponse(**user)
    )


# ============================================
# POST /auth/refresh
# ============================================

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    refresh_request: RefreshTokenRequest,
    db=Depends(get_db)
):
    """Renovar access token usando refresh token"""
    user_id = verify_refresh_token(db, refresh_request.refresh_token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido ou expirado"
        )
    
    user = get_user_by_id(db, user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado"
        )
    
    if not user.get("ativo"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário desativado"
        )
    
    # Revogar refresh token antigo (rotação)
    revoke_refresh_token(db, refresh_request.refresh_token)
    
    # Criar novos tokens
    access_token, new_refresh_token = create_tokens_for_user(user)
    
    # Armazenar novo refresh token
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    
    store_refresh_token(db, user['id'], new_refresh_token, ip_address, user_agent)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE * 60
    )


# ============================================
# POST /auth/logout
# ============================================

@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    logout_request: LogoutRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """Logout - revoga refresh token"""
    if logout_request.refresh_token:
        revoke_refresh_token(db, logout_request.refresh_token)
    else:
        revoke_all_user_tokens(db, current_user.user_id)
    
    await log_audit(db, current_user.user_id, current_user.id_condominio, "logout", request=request)
    
    return MessageResponse(message="Logout realizado com sucesso")


# ============================================
# GET /auth/me
# ============================================

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """Retorna informações do usuário autenticado"""
    user = get_user_by_id(db, current_user.user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado"
        )
    
    return UserResponse(**user)


# ============================================
# POST /auth/register (APENAS ADMINS)
# ============================================

@router.post("/register", response_model=UserResponse, dependencies=[Depends(require_admin)])
async def register_user(
    request: Request,
    user_data: RegisterRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """Registrar novo usuário (apenas admins)"""
    if current_user.role != "admin_sistema":
        if user_data.condominio_id != current_user.id_condominio:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Você só pode criar usuários no seu próprio condomínio"
            )
    
    new_user = create_user(
        db,
        email=user_data.email,
        nome=user_data.nome,
        password=user_data.password,
        condominio_id=user_data.condominio_id,
        role=user_data.role,
        nivel_id=user_data.nivel_id,
        created_by_user_id=current_user.user_id
    )
    
    await log_audit(
        db, current_user.user_id, current_user.id_condominio,
        "create_user", "mobile_operadores", new_user['id'],
        {"email": user_data.email, "role": user_data.role},
        request
    )
    
    return UserResponse(**new_user)


# ============================================
# POST /auth/logout-all
# ============================================

@router.post("/logout-all", response_model=MessageResponse)
async def logout_all_devices(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """Deslogar de todos os dispositivos"""
    revoke_all_user_tokens(db, current_user.user_id)
    
    await log_audit(
        db, current_user.user_id, current_user.id_condominio,
        "logout_all_devices", request=request
    )
    
    return MessageResponse(
        message="Deslogado de todos os dispositivos",
        detail="Todos os seus tokens foram revogados. Faça login novamente."
    )


# ============================================
# GET /auth/health (público)
# ============================================

@router.get("/health")
async def auth_health():
    """Health check do sistema de autenticação"""
    from app.auth.auth_utils import get_auth_info
    
    info = get_auth_info()
    
    return {
        "status": "ok",
        "service": "authentication",
        **info
    }
