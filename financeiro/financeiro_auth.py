# ================================================================================
#  PATH: backend/financeiro/financeiro_auth.py
#  DESCRIPTION: Rotas de autenticação do módulo financeiro
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException
from supertokens_python.recipe.session.framework.fastapi import verify_session
from supertokens_python.recipe.session import SessionContainer
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/me")
async def get_current_user(session: SessionContainer = Depends(verify_session())):
    """Retorna dados do usuário logado"""
    user_id = session.get_user_id()
    return {
        "user_id": user_id,
        "session_handle": session.get_handle(),
    }

@router.get("/check")
async def check_auth(session: SessionContainer = Depends(verify_session())):
    """Verifica se está autenticado"""
    return {"authenticated": True, "user_id": session.get_user_id()}
