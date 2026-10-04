# ========================================
# Auth Routes - Rotas de Autenticação
# Módulo Admin - Endpoints de Login
# ========================================

from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import pymysql
from pymysql.cursors import DictCursor

from .auth_models import (
    LoginRequest, 
    TokenResponse, 
    UserResponse,
    UserCreate,
    UserUpdate,
    PasswordChange,
    PasswordReset
)
from .auth_service import auth_service
from app.services.nivel_sistema import nivel_do_id  # 2026-10-04
from .database import get_db_connection

# ========================================
# Router e Security
# ========================================

router = APIRouter(prefix="/auth", tags=["Autenticação Admin"])
security = HTTPBearer(auto_error=False)


# ========================================
# Dependency: Usuário Atual
# ========================================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    authorization: Optional[str] = Header(None)
) -> dict:
    """
    Dependency para obter usuário atual do token
    
    Aceita token via:
    - Header Authorization: Bearer <token>
    """
    token = None
    
    # Tenta obter token do HTTPBearer
    if credentials:
        token = credentials.credentials
    # Fallback: header Authorization direto
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Token não fornecido",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Verifica token
    payload = auth_service.verificar_token(token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Busca usuário no banco
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            user = auth_service.buscar_por_id(int(payload['sub']), cursor)
            if not user:
                raise HTTPException(
                    status_code=401,
                    detail="Usuário não encontrado"
                )
            return user
    finally:
        conn.close()


async def get_current_active_user(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """Verifica se usuário está ativo"""
    if not current_user.get('ativo'):
        raise HTTPException(
            status_code=403,
            detail="Usuário desativado"
        )
    return current_user


# ========================================
# Endpoints de Autenticação
# ========================================

@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Login com email OU telefone (WhatsApp)
    
    Exemplos de uso:
    - {"identificador": "admin@econdominio.app.br", "senha": "123456"}
    - {"identificador": "48984046118", "senha": "123456"}
    - {"nome": "admin@econdominio.app.br", "senha": "123456"}  # Compatibilidade
    """
    identificador = request.get_identificador()
    
    if not identificador:
        raise HTTPException(
            status_code=400,
            detail="Informe email ou telefone para login"
        )
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sucesso, user_data, erro = auth_service.autenticar(
                identificador, 
                request.senha, 
                cursor
            )
            conn.commit()
            
            if not sucesso:
                raise HTTPException(
                    status_code=401,
                    detail=erro or "Credenciais inválidas"
                )
            
            # Gera token
            token = auth_service.criar_token(
                user_id=user_data['id'],
                email=user_data['email'],
                role=user_data['role'],
                condominio_id=user_data['condominio_id'],
                nome=user_data.get('nome'),
                nivel_sistema=nivel_do_id(user_data['id']) if user_data.get('role') == 'admin_sistema' else None
            )
            
            return TokenResponse(
                access_token=token,
                token_type="bearer",
                expires_in=3600 * 24,  # 24 horas
                user=UserResponse(
                    id=user_data['id'],
                    email=user_data['email'],
                    nome=user_data['nome'],
                    telefone=user_data.get('telefone'),
                    condominio_id=user_data['condominio_id'],
                    condominio_nome=user_data.get('condominio_nome'),
                    role=user_data['role'],
                    nivel_id=user_data['nivel_id'],
                    ativo=user_data['ativo'],
                    nivel_sistema=nivel_do_id(user_data['id']) if user_data.get('role') == 'admin_sistema' else None
                )
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno: {str(e)}"
        )
    finally:
        conn.close()


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_active_user)):
    """
    Retorna dados do usuário autenticado
    
    Requer token válido no header Authorization
    """
    return UserResponse(
        id=current_user['id'],
        email=current_user['email'],
        nome=current_user['nome'],
        telefone=current_user.get('telefone'),
        condominio_id=current_user['condominio_id'],
        condominio_nome=current_user.get('condominio_nome'),
        role=current_user['role'],
        nivel_id=current_user['nivel_id'],
        ativo=current_user['ativo'],
        nivel_sistema=nivel_do_id(current_user['id']) if current_user.get('role') == 'admin_sistema' else None
    )


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """
    Logout do usuário
    
    Nota: Como usamos JWT stateless, o logout é feito no cliente
    removendo o token. Este endpoint é para compatibilidade.
    """
    return {"message": "Logout realizado com sucesso"}


@router.post("/refresh")
async def refresh_token(current_user: dict = Depends(get_current_active_user)):
    """
    Renova o token JWT
    
    Retorna um novo token com tempo de expiração renovado
    """
    token = auth_service.criar_token(
        user_id=current_user['id'],
        email=current_user['email'],
        role=current_user['role'],
        condominio_id=current_user['condominio_id'],
        nome=current_user.get('nome'),
        nivel_sistema=nivel_do_id(current_user['id']) if current_user.get('role') == 'admin_sistema' else None
    )
    
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=3600 * 24
    )


@router.post("/change-password")
async def change_password(
    request: PasswordChange,
    current_user: dict = Depends(get_current_active_user)
):
    """
    Altera a senha do usuário autenticado
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Busca hash atual
            cursor.execute(
                "SELECT senha_hash FROM mobile_operadores WHERE id = %s",
                (current_user['id'],)
            )
            result = cursor.fetchone()
            
            if not result:
                raise HTTPException(status_code=404, detail="Usuário não encontrado")
            
            # Verifica senha atual
            if not auth_service.verificar_senha(request.senha_atual, result['senha_hash']):
                raise HTTPException(status_code=400, detail="Senha atual incorreta")
            
            # Atualiza senha
            novo_hash = auth_service.hash_senha(request.nova_senha)
            cursor.execute(
                "UPDATE mobile_operadores SET senha_hash = %s WHERE id = %s",
                (novo_hash, current_user['id'])
            )
            conn.commit()
            
            return {"message": "Senha alterada com sucesso"}
    finally:
        conn.close()


# ========================================
# Endpoints de Validação
# ========================================

@router.get("/validate")
async def validate_token(current_user: dict = Depends(get_current_user)):
    """
    Valida se o token é válido
    
    Retorna dados básicos do usuário se válido
    """
    return {
        "valid": True,
        "user_id": current_user['id'],
        "email": current_user['email'],
        "role": current_user['role']
    }


@router.get("/check-email/{email}")
async def check_email_exists(email: str):
    """
    Verifica se um email já está cadastrado
    
    Útil para validação de formulários
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM mobile_operadores WHERE LOWER(email) = LOWER(%s)",
                (email,)
            )
            exists = cursor.fetchone() is not None
            return {"exists": exists, "email": email}
    finally:
        conn.close()


@router.get("/check-telefone/{telefone}")
async def check_telefone_exists(telefone: str):
    """
    Verifica se um telefone já está cadastrado
    
    Útil para validação de formulários
    """
    telefone_normalizado = auth_service.normalizar_telefone(telefone)
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM mobile_operadores WHERE telefone = %s",
                (telefone_normalizado,)
            )
            exists = cursor.fetchone() is not None
            return {"exists": exists, "telefone": telefone_normalizado}
    finally:
        conn.close()
