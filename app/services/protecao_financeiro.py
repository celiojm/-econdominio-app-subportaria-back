# ============================================================================
# ARQUIVO: protecao_financeiro.py
# PASTA: /home/visionlpr/app_subportaria_back/app/services/
# DESCRIÇÃO: Middleware que exige token interno em /api/financeiro/* (exceto
#            auth/ e webhook/). Aceita token do painel financeiro ou do admin
#            master (role admin_sistema, ou admin nível 1).
#            FINANCEIRO_AUTH_ENFORCE=false -> só loga FIN_AUTH_DRYRUN;
#            true -> responde 401.
# VERSÃO: 1.1.0 - dependência usuario_interno (rotas de NF aceitam token do financeiro)
#         1.0.0 - criação (segurança, 2026-09-26)
# data criação: 2026-09-26 data alteração: 2026-09-26
# ============================================================================

import logging
import os
from typing import Optional

from jose import JWTError, jwt
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

PREFIXO_PROTEGIDO = "/api/financeiro/"
PREFIXOS_LIVRES = ("/api/financeiro/auth/", "/api/financeiro/webhook/")


def _enforce() -> bool:
    return os.getenv("FINANCEIRO_AUTH_ENFORCE", "false").strip().lower() in ("1", "true", "yes")


def identificar_token_interno(token: str) -> Optional[str]:
    """Devolve 'financeiro:<id>' ou 'admin:<usuario>' se o token for interno válido; senão None."""
    chave_fin = os.getenv("JWT_SECRET_KEY")
    if chave_fin:
        try:
            p = jwt.decode(token, chave_fin, algorithms=["HS256"])
            return f"financeiro:{p.get('sub')}"
        except JWTError:
            pass
    try:
        p = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    if p.get("role") == "admin_sistema" or (p.get("sub") == "admin" and str(p.get("nivel")) == "1"):
        return f"admin:{p.get('sub')}"
    return None


async def proteger_financeiro(request: Request, call_next):
    path = request.url.path
    if (request.method == "OPTIONS" or not path.startswith(PREFIXO_PROTEGIDO)
            or path.startswith(PREFIXOS_LIVRES)):
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    quem = identificar_token_interno(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if quem:
        return await call_next(request)

    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if not _enforce():
        logger.warning("FIN_AUTH_DRYRUN %s %s sem token interno válido (ip=%s, com_header=%s)",
                       request.method, path, ip, bool(auth))
        return await call_next(request)
    logger.warning("FIN_AUTH_BLOQUEADO %s %s (ip=%s)", request.method, path, ip)
    return JSONResponse(status_code=401, content={"detail": "Não autenticado"})


async def usuario_interno(request: Request) -> dict:
    """Dependência: equipe interna (token do painel financeiro ou admin master). Devolve role
    'admin_sistema' para manter compatíveis as rotas que já checam esse role."""
    auth = request.headers.get("authorization", "")
    quem = identificar_token_interno(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if not quem:
        raise HTTPException(status_code=401, detail="Não autenticado")
    origem, ident = quem.split(":", 1)
    return {"role": "admin_sistema", "origem": origem, "usuario": ident}

