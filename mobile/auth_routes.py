"""
================================================================================
ARQUIVO: auth_routes.py
PASTA:   ~/backend/mobile/
CAMINHO: /home/visionlpr/backend/mobile/auth_routes.py
================================================================================
Rotas de autenticação do módulo mobile
Endpoints: login, logout, refresh, me, esqueci-senha, redefinir-senha
Com suporte a login por email OU whatsapp
ADMIN_SISTEMA tem acesso a TODOS os condomínios
================================================================================
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.database import get_db
from .auth_models import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    OperadorCreate,
    OperadorUpdate,
    AlterarSenhaRequest,
    OperadorResponse,
    EsqueciSenhaRequest,
    RedefinirSenhaRequest,
    MobileOperador,
    RoleEnum
)
from . import auth_service
from . import esqueci_senha_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mobile/auth", tags=["Mobile - Autenticação"])

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


def can_access_condominio(operador: MobileOperador, condominio_id: int) -> bool:
    """
    Verifica se o operador pode acessar o condomínio especificado.
    
    Regras:
    - admin_sistema: acesso a TODOS os condomínios
    - outros: apenas ao próprio condomínio
    """
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    
    # Admin sistema tem acesso a TODOS os condomínios
    if role == "admin_sistema":
        return True
    
    # Outros: apenas ao próprio condomínio
    return operador.condominio_id == condominio_id


async def get_current_operador(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> MobileOperador:
    """
    Dependency para obter operador atual a partir do JWT.
    Usar em rotas protegidas: operador = Depends(get_current_operador)
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

    operador = auth_service.get_operador_by_id(db, int(payload["sub"]))
    if not operador or not operador.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operador não encontrado ou inativo"
        )

    return operador


async def require_admin_sistema(
    operador: MobileOperador = Depends(get_current_operador)
) -> MobileOperador:
    """Dependency que exige admin_sistema"""
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    if role != "admin_sistema":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores do sistema"
        )
    return operador


async def require_sindico_or_admin(
    operador: MobileOperador = Depends(get_current_operador)
) -> MobileOperador:
    """Dependency que exige síndico ou admin_sistema"""
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    if role not in ["admin_sistema", "sindico"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a síndicos e administradores"
        )
    return operador


# ========================== ROTAS PÚBLICAS ==========================

@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Realiza login com email OU whatsapp e retorna tokens JWT.

    Rate limiting: 5 tentativas por 15 minutos.
    Bloqueio: 30 minutos após exceder limite.
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

    # Buscar operador por email OU telefone
    operador = auth_service.get_operador_by_identifier(db, data.identifier)

    if not operador:
        auth_service.record_login_attempt(db, ip, data.identifier, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas"
        )

    # Verificar se operador está bloqueado
    blocked, block_remaining = auth_service.is_user_blocked(operador)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Conta bloqueada. Tente novamente em {block_remaining} segundos.",
            headers={"Retry-After": str(block_remaining)}
        )

    # Verificar se operador está ativo
    if not operador.ativo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada. Contate o administrador."
        )

    # Verificar senha
    if not auth_service.verify_password(data.password, operador.senha_hash):
        auth_service.record_login_attempt(db, ip, data.identifier, success=False)
        auth_service.increment_failed_login(db, operador)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas"
        )

    # Login bem sucedido
    auth_service.record_login_attempt(db, ip, data.identifier, success=True)
    auth_service.update_last_login(db, operador)

    # Gerar tokens
    access_token, _ = auth_service.create_access_token(operador)
    refresh_token_str, _ = auth_service.create_refresh_token(db, operador, ip, device)

    logger.info(f"✅ Login mobile: {operador.email} de {ip}")

    return LoginResponse(
        success=True,
        access_token=access_token,
        refresh_token=refresh_token_str,
        token_type="Bearer",
        expires_in=auth_service.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=operador.to_dict()
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(
    data: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Renova o access token usando um refresh token válido"""
    ip = get_client_ip(request)

    # Verificar refresh token
    refresh_token = auth_service.verify_refresh_token(db, data.refresh_token)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido ou expirado"
        )

    # Buscar operador
    operador = auth_service.get_operador_by_id(db, refresh_token.operador_id)
    if not operador or not operador.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operador não encontrado ou inativo"
        )

    # Gerar novo access token
    access_token, _ = auth_service.create_access_token(operador)

    logger.debug(f"🔄 Token renovado: {operador.email}")

    return RefreshResponse(
        success=True,
        access_token=access_token,
        expires_in=auth_service.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/logout")
async def logout(
    data: RefreshRequest,
    db: Session = Depends(get_db),
    operador: MobileOperador = Depends(get_current_operador)
):
    """Revoga o refresh token atual (logout da sessão atual)"""
    revoked = auth_service.revoke_refresh_token(db, data.refresh_token)

    if revoked:
        logger.info(f"🚪 Logout mobile: {operador.email}")
        return {"success": True, "message": "Logout realizado com sucesso"}

    return {"success": False, "message": "Token não encontrado"}


@router.post("/logout-all")
async def logout_all(
    db: Session = Depends(get_db),
    operador: MobileOperador = Depends(get_current_operador)
):
    """Revoga TODOS os refresh tokens do operador (logout de todas as sessões)"""
    count = auth_service.revoke_all_user_tokens(db, operador.id)

    logger.info(f"🚪 Logout ALL mobile: {operador.email} ({count} sessões)")

    return {
        "success": True,
        "message": f"Logout realizado em {count} sessão(ões)"
    }


@router.post("/esqueci-senha")
async def esqueci_senha(
    data: EsqueciSenhaRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Inicia processo de recuperação de senha.
    Envia link por email OU whatsapp.
    """
    ip = get_client_ip(request)

    # Buscar operador
    operador = auth_service.get_operador_by_identifier(db, data.identifier)

    if not operador:
        logger.warning(f"⚠️  Esqueci senha: operador não encontrado - {data.identifier}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado. Verifique o número ou email informado."
        )

    # Validar método de envio
    if data.metodo_envio == "whatsapp" and not operador.telefone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Operador não possui WhatsApp cadastrado"
        )

    # Enviar link de recuperação
    success, error = esqueci_senha_service.send_reset_link(db, operador, data.metodo_envio, ip)

    if not success:
        logger.error(f"❌ Erro ao enviar recuperação: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao enviar link de recuperação via {data.metodo_envio}"
        )

    logger.info(f"✅ Link de recuperação enviado para: {operador.email} via {data.metodo_envio}")

    return {
        "success": True,
        "message": f"Link de recuperação enviado via {data.metodo_envio}."
    }


@router.post("/redefinir-senha")
async def redefinir_senha(
    data: RedefinirSenhaRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Redefine a senha usando token de recuperação"""
    # Verificar token
    operador = esqueci_senha_service.verify_reset_token(db, data.token)

    if not operador:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido, expirado ou já utilizado"
        )

    # Atualizar senha
    auth_service.update_operador_password(db, operador, data.nova_senha)

    # Marcar token como usado
    esqueci_senha_service.mark_token_as_used(db, data.token)

    logger.info(f"✅ Senha redefinida: {operador.email}")

    return {
        "success": True,
        "message": "Senha redefinida com sucesso. Faça login com a nova senha."
    }


# ========================== ROTAS PROTEGIDAS ==========================

@router.get("/me", response_model=OperadorResponse)
async def get_me(operador: MobileOperador = Depends(get_current_operador)):
    """Retorna dados do operador logado"""
    return OperadorResponse(**operador.to_dict())


@router.get("/check")
async def check_auth(operador: MobileOperador = Depends(get_current_operador)):
    """Verifica se o token é válido"""
    role = operador.role.value if isinstance(operador.role, RoleEnum) else operador.role
    return {
        "authenticated": True,
        "operador_id": operador.id,
        "email": operador.email,
        "role": role,
        "condominio_id": operador.condominio_id
    }


@router.put("/alterar-senha")
async def alterar_senha(
    data: AlterarSenhaRequest,
    db: Session = Depends(get_db),
    operador: MobileOperador = Depends(get_current_operador)
):
    """Altera a senha do operador logado. Revoga todos os tokens após alteração."""
    # Verificar senha atual
    if not auth_service.verify_password(data.senha_atual, operador.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Senha atual incorreta"
        )

    # Atualizar senha
    auth_service.update_operador_password(db, operador, data.nova_senha)

    return {
        "success": True,
        "message": "Senha alterada com sucesso. Faça login novamente."
    }


@router.get("/sessoes")
async def listar_sessoes(
    db: Session = Depends(get_db),
    operador: MobileOperador = Depends(get_current_operador)
):
    """Lista todas as sessões ativas do operador"""
    sessions = auth_service.get_user_sessions(db, operador.id)
    return {
        "sessoes": sessions,
        "total": len(sessions)
    }


# ========================== ROTAS SÍNDICO/ADMIN ==========================

@router.post("/operadores", response_model=OperadorResponse)
async def criar_operador(
    data: OperadorCreate,
    db: Session = Depends(get_db),
    current: MobileOperador = Depends(require_sindico_or_admin)
):
    """
    Cria um novo operador.

    Permissões:
    - admin_sistema: pode criar qualquer role em qualquer condomínio
    - sindico: pode criar apenas operadores no seu condomínio
    """
    # Determinar condomínio_id
    if data.role == "admin_sistema":
        # Apenas admin_sistema pode criar admin_sistema
        if current.role != RoleEnum.admin_sistema:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas admin_sistema pode criar outros admin_sistema"
            )
        target_condominio_id = 1
    else:
        # Se não especificou condomínio, usa o do criador
        target_condominio_id = data.condominio_id or current.condominio_id

    # Verificar permissão de acesso ao condomínio
    if not can_access_condominio(current, target_condominio_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para criar usuários neste condomínio"
        )

    # Verificar se email já existe
    existing = auth_service.get_operador_by_identifier(db, data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email já cadastrado"
        )

    # Verificar se telefone já existe (se fornecido)
    if data.telefone:
        existing_phone = auth_service.get_operador_by_identifier(db, data.telefone)
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Telefone já cadastrado"
            )

    # Criar operador
    operador = auth_service.create_operador(
        db=db,
        email=data.email,
        password=data.password,
        nome=data.nome,
        condominio_id=target_condominio_id,
        role=data.role,
        telefone=data.telefone,
        created_by=current.id
    )

    logger.info(f"✅ Operador criado: {operador.email} por {current.email}")

    return OperadorResponse(**operador.to_dict())


@router.get("/operadores")
async def listar_operadores(
    condominio_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current: MobileOperador = Depends(require_sindico_or_admin)
):
    """
    Lista operadores.

    Permissões:
    - admin_sistema: vê todos (ou filtra por condominio_id se especificado)
    - sindico: vê apenas do seu condomínio
    """
    query = db.query(MobileOperador)

    role = current.role.value if isinstance(current.role, RoleEnum) else current.role
    
    # Admin sistema pode ver todos ou filtrar por condomínio específico
    if role == "admin_sistema":
        if condominio_id is not None:
            query = query.filter(MobileOperador.condominio_id == condominio_id)
    else:
        # Outros: apenas do próprio condomínio
        query = query.filter(MobileOperador.condominio_id == current.condominio_id)

    operadores = query.all()

    return {
        "operadores": [op.to_dict() for op in operadores],
        "total": len(operadores)
    }


@router.put("/operadores/{operador_id}")
async def atualizar_operador(
    operador_id: int,
    data: OperadorUpdate,
    db: Session = Depends(get_db),
    current: MobileOperador = Depends(require_sindico_or_admin)
):
    """
    Atualiza um operador.
    
    Permissões:
    - admin_sistema: pode atualizar qualquer operador
    - sindico: pode atualizar apenas operadores do seu condomínio
    """
    operador = auth_service.get_operador_by_id(db, operador_id)
    if not operador:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Operador não encontrado"
        )

    # Verificar permissão de acesso ao condomínio do operador
    if not can_access_condominio(current, operador.condominio_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para editar este operador"
        )

    # Atualizar campos
    if data.nome is not None:
        operador.nome = data.nome
    if data.telefone is not None:
        operador.telefone = auth_service.normalize_phone(data.telefone) if data.telefone else None
    if data.role is not None:
        # Apenas admin_sistema pode mudar roles
        role = current.role.value if isinstance(current.role, RoleEnum) else current.role
        if role != "admin_sistema":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas admin_sistema pode alterar roles"
            )
        operador.role = data.role
    if data.ativo is not None:
        operador.ativo = data.ativo
        if not data.ativo:
            # Revogar todos os tokens se desativar
            auth_service.revoke_all_user_tokens(db, operador.id)

    db.commit()

    logger.info(f"✅ Operador atualizado: {operador.email} por {current.email}")

    return {"success": True, "operador": operador.to_dict()}


@router.delete("/operadores/{operador_id}")
async def deletar_operador(
    operador_id: int,
    db: Session = Depends(get_db),
    current: MobileOperador = Depends(require_admin_sistema)
):
    """Remove um operador (apenas admin_sistema)"""
    if operador_id == current.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível remover seu próprio usuário"
        )

    operador = auth_service.get_operador_by_id(db, operador_id)
    if not operador:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Operador não encontrado"
        )

    db.delete(operador)
    db.commit()

    logger.info(f"❌ Operador removido: {operador.email} por {current.email}")

    return {"success": True, "message": f"Operador {operador.email} removido"}
