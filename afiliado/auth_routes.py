# ================================================================================
# ARQUIVO: auth_routes.py - Rotas de Autenticação de Afiliados
# ================================================================================

from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.database import get_db
from .auth_models import LoginRequest, TokenResponse
from .auth_service import afiliado_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/afiliados/auth", tags=["Autenticação Afiliados"])


@router.post("/login", response_model=TokenResponse)
def login_afiliado(credentials: LoginRequest, db: Session = Depends(get_db)):
    """
    Login de afiliado com email OU WhatsApp
    
    Exemplos:
    - POST {"identificador": "teste@econdominio.com.br", "senha": "teste123"}
    - POST {"identificador": "4830351252", "senha": "teste123"}
    """
    try:
        # Determinar tipo de login
        if credentials.is_email():
            sucesso, user_data, erro = afiliado_auth_service.autenticar_email(
                credentials.get_identificador(),
                credentials.senha,
                db
            )
        else:
            whatsapp = credentials.get_whatsapp_normalizado()
            sucesso, user_data, erro = afiliado_auth_service.autenticar_whatsapp(
                whatsapp,
                credentials.senha,
                db
            )
        
        if not sucesso:
            logger.warning(f"❌ Login falhou: {credentials.get_identificador()}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=erro or "Credenciais inválidas"
            )
        
        # Criar token
        token = afiliado_auth_service.criar_token(
            user_id=user_data['id'],
            email=user_data['email'],
            nome=user_data['nome'],
            is_admin=user_data['is_admin']
        )
        
        logger.info(f"✅ Login: {user_data['email']} (WhatsApp: {user_data['whatsapp']})")
        
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=86400,
            user={
                "id": user_data['id'],
                "afiliado_id": user_data['id'],
                "nome": user_data['nome'],
                "email": user_data['email'],
                "whatsapp": user_data['whatsapp'],
                "codigo_afiliado": user_data['codigo_afiliado'],
                "is_admin": user_data['is_admin']
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.get("/me")
def get_me(db: Session = Depends(get_db)):
    """Retorna dados do afiliado autenticado (placeholder)"""
    # TODO: Implementar verificação de token
    return {"message": "Endpoint /me - TODO"}
