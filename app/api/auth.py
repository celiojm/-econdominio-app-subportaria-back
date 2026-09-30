from fastapi import APIRouter, Depends, HTTPException, status
# ALTERAÇÃO 2026-09-26: rota(s) de teste sem autenticação removida(s) (segurança)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from jose import JWTError, jwt
from datetime import datetime, timedelta

from app.database import get_db
from app.schemas import LoginRequest, Token, UserInfo
from app.services.auth import create_access_token, verify_password
from app.config import settings

router = APIRouter()
security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Obter usuário atual do token JWT"""
    token = credentials.credentials

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

        # Verificar expiração
        exp = payload.get("exp")
        if exp and datetime.fromtimestamp(exp) < datetime.now():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expirado",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_data = {
            "username": payload.get("sub"),
            "user_id": payload.get("user_id"),
            "condominio_id": payload.get("condominio_id"),
            "nivel": payload.get("nivel"),
            "nivel_id": payload.get("nivel_id"),
            "role": payload.get("role"),
            "condominio_nome": payload.get("condominio_nome")
        }
        if not user_data["username"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user_data
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )

@router.post("/login", response_model=Token)
async def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    """Login com verificação real no banco de dados"""
    print(f"Login attempt - Nome: {login_data.nome}, Has password: {bool(login_data.senha)}")

    try:
        # Buscar operador no banco - CORRIGIDO para usar Id maiúsculo
        query = text("""
	    SELECT
       		 o.id,
	        o.Nome,
	        o.Senha,
	        o.Nivel_id,
	        o.Condomino_id,
	        o.Nomedocondomino,
	        c.nome as condominio_nome_real
	    FROM operadores o
  	    LEFT JOIN condominios c ON o.Condomino_id = c.id
            WHERE o.Nome = :nome
        """)


        result = db.execute(query, {"nome": login_data.nome})
        operator = result.fetchone()

        if not operator:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuário não encontrado",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Verificar senha
        if not verify_password(login_data.senha, operator.Senha):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Senha incorreta",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Usar nome do condomínio real se disponível
        condominio_nome = operator.condominio_nome_real or operator.Nomedocondomino

        # Criar token com dados do operador
        access_token = create_access_token(
            data={
                "sub": operator.Nome,
                "user_id": operator.id,
                "condominio_id": operator.Condomino_id,
                "nivel": operator.Nivel_id,
                "condominio_nome": condominio_nome
            }
        )

        return {
            "access_token": access_token,
            "token_type": "bearer"
        }

    except HTTPException:
        raise
    except Exception as e:
        # Log do erro para debug
        print(f"Erro no login: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )

@router.get("/me", response_model=UserInfo)
async def get_current_user_info(
    current_user: dict = Depends(get_current_user)
):
    """Retorna informações do usuário atual"""
    return UserInfo(
        id=current_user["user_id"],
        nome=current_user["username"],
        nivel=current_user["nivel"],
        condominio_id=current_user["condominio_id"],
        condominio_nome=current_user["condominio_nome"]
    )

@router.post("/logout")
async def logout():
    """Endpoint de logout (client-side deve remover o token)"""
    return {"message": "Logout realizado com sucesso"}

@router.get("/verify")
async def verify_token(current_user: dict = Depends(get_current_user)):
    """Verifica se o token é válido"""
    return {
        "valid": True,
        "message": "Token válido",
        "user": current_user["username"],
        "condominio": current_user["condominio_nome"]
    }

@router.post("/refresh")
async def refresh_token(current_user: dict = Depends(get_current_user)):
    """Renovar token de acesso"""
    # Criar novo token com os mesmos dados
    new_token = create_access_token(
        data={
            "sub": current_user["username"],
            "user_id": current_user["user_id"],
            "condominio_id": current_user["condominio_id"],
            "nivel": current_user["nivel"],
            "condominio_nome": current_user["condominio_nome"]
        }
    )

    return {
        "access_token": new_token,
        "token_type": "bearer"
    }

@router.post("/change-password")
async def change_password(
    current_password: str,
    new_password: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Alterar senha do usuário"""
    try:
        from app.services.auth import get_password_hash

        # Verificar senha atual
        query = text("SELECT Senha FROM operadores WHERE id = :user_id")
        result = db.execute(query, {"user_id": current_user["user_id"]})
        user = result.fetchone()

        if not user or not verify_password(current_password, user.Senha):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Senha atual incorreta"
            )

        # Atualizar para nova senha
        new_hash = get_password_hash(new_password)
        update_query = text("UPDATE operadores SET Senha = :senha WHERE id = :user_id")
        db.execute(update_query, {"senha": new_hash, "user_id": current_user["user_id"]})
        db.commit()

        return {"message": "Senha alterada com sucesso"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao alterar senha: {str(e)}"
        )

# Endpoint temporário para desenvolvimento - REMOVER EM PRODUÇÃO
# [removido 2026-09-26, segurança] @router.post("/reset-admin-password-temp")
async def reset_admin_password(db: Session = Depends(get_db)):
    """TEMPORÁRIO: Reset senha do admin para admin123"""
    try:
        from app.services.auth import get_password_hash

        new_hash = get_password_hash("admin123")

        result = db.execute(
            text("UPDATE operadores SET Senha = :senha WHERE Nome = 'admin'"),
            {"senha": new_hash}
        )
        db.commit()

        return {"message": "Senha do admin resetada para 'admin123'", "hash": new_hash}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
