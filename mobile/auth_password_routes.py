"""
Rotas de Recuperação de Senha
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from mobile.password_reset import (
    send_reset_link,
    verify_reset_token,
    reset_password_with_token
)

router = APIRouter(prefix="/auth", tags=["auth-password"])

class ForgotPasswordRequest(BaseModel):
    identifier: str  # Email ou Telefone
    method: str  # "email" ou "whatsapp"

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

@router.post("/forgot-password")
async def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Envia link de recuperação de senha
    """
    success = await send_reset_link(
        db=db,
        identifier=request.identifier,
        method=request.method
    )
    
    return {
        "success": True,
        "message": "Se o usuário existir, um link foi enviado"
    }

@router.get("/verify-reset-token")
async def verify_token(token: str = Query(...)):
    """
    Verifica se token é válido
    """
    user_id = verify_reset_token(token)
    
    if not user_id:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")
    
    return {"valid": True}

@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Redefine senha com token
    """
    success = await reset_password_with_token(
        db=db,
        token=request.token,
        new_password=request.new_password
    )
    
    return {
        "success": True,
        "message": "Senha redefinida com sucesso"
    }

