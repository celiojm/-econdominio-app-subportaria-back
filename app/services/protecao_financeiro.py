# ============================================================================
# ARQUIVO: protecao_financeiro.py
# PASTA: /home/visionlpr/app_subportaria_back/app/services/
# DESCRIÇÃO: Middleware que exige token interno em /api/financeiro/* (exceto
#            auth/ e webhook/). Aceita token do painel financeiro ou do admin
#            master (role admin_sistema, ou admin nível 1).
#            FINANCEIRO_AUTH_ENFORCE=false -> só loga FIN_AUTH_DRYRUN;
#            true -> responde 401.
#            Níveis do painel financeiro: MASTER (financeiro_usuarios.tipo = admin, ou admin master do
#            sistema) acessa tudo; COLABORADOR (demais tipos) NÃO acessa as rotas de ROTAS_SO_MASTER
#            (dashboard financeiro, pagamentos, contas a pagar, previsão, afiliados) → 403.
#            usuario_atual()/nome_usuario_atual(): quem está logado na requisição (para registrar o
#            colaborador nos contatos — o nome vem do token, não da tela).
# VERSÃO: 1.2.0 - níveis master/colaborador; usuário logado disponível às rotas (2026-10-04)
#         1.1.0 - dependência usuario_interno (rotas de NF aceitam token do financeiro)
#         1.0.0 - criação (segurança, 2026-09-26)
# data criação: 2026-09-26 data alteração: 2026-10-04
# ============================================================================

import logging
import os
from contextvars import ContextVar
from typing import Optional

from jose import JWTError, jwt
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

PREFIXO_PROTEGIDO = "/api/financeiro/"
PREFIXOS_LIVRES = ("/api/financeiro/auth/", "/api/financeiro/webhook/")
# 2026-10-04: áreas financeiras da empresa — só o master
ROTAS_SO_MASTER = (
    "/api/financeiro/dashboard",
    "/api/financeiro/pagamentos",
    "/api/financeiro/contas-pagar",
    "/api/financeiro/previsao",
    "/api/financeiro/afiliados",
)
TIPOS_MASTER = ("admin", "master")

_usuario_atual: ContextVar[Optional[dict]] = ContextVar("usuario_financeiro_atual", default=None)


def _enforce() -> bool:
    return os.getenv("FINANCEIRO_AUTH_ENFORCE", "false").strip().lower() in ("1", "true", "yes")


def ler_token_interno(token: str) -> Optional[dict]:
    """Dados de quem está logado (token do painel financeiro ou admin master do sistema); senão None."""
    chave_fin = os.getenv("JWT_SECRET_KEY")
    if chave_fin:
        try:
            p = jwt.decode(token, chave_fin, algorithms=["HS256"])
            tipo = (p.get("tipo") or "operador").lower()
            return {"origem": "financeiro", "id": p.get("sub"), "nome": p.get("nome") or p.get("email") or "financeiro",
                    "email": p.get("email"), "tipo": tipo, "master": tipo in TIPOS_MASTER}
        except JWTError:
            pass
    try:
        p = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    if p.get("role") == "admin_sistema" or (p.get("sub") == "admin" and str(p.get("nivel")) == "1"):
        return {"origem": "admin", "id": p.get("sub"), "nome": p.get("nome") or p.get("sub") or "admin",
                "email": None, "tipo": "admin", "master": True}
    return None


def identificar_token_interno(token: str) -> Optional[str]:
    """Compatibilidade: devolve 'financeiro:<id>' ou 'admin:<usuario>'; senão None."""
    d = ler_token_interno(token)
    return f"{d['origem']}:{d['id']}" if d else None


def _do_header(request: Request) -> Optional[dict]:
    auth = request.headers.get("authorization", "")
    return ler_token_interno(auth[7:].strip()) if auth.lower().startswith("bearer ") else None


def usuario_atual() -> Optional[dict]:
    """Quem está logado na requisição em andamento (preenchido pelo middleware)."""
    return _usuario_atual.get()


def nome_usuario_atual(padrao: Optional[str] = None) -> Optional[str]:
    """Nome de quem está logado; se não houver login interno, o valor recebido (padrao)."""
    u = _usuario_atual.get()
    return u["nome"] if u and u.get("nome") else padrao


async def proteger_financeiro(request: Request, call_next):
    path = request.url.path
    if (request.method == "OPTIONS" or not path.startswith(PREFIXO_PROTEGIDO)
            or path.startswith(PREFIXOS_LIVRES)):
        return await call_next(request)

    quem = _do_header(request)
    if quem:
        if not quem["master"] and path.startswith(ROTAS_SO_MASTER):
            logger.info("FIN_NIVEL_BLOQUEADO %s %s (colaborador %s)", request.method, path, quem.get("nome"))
            return JSONResponse(status_code=403, content={"detail": "Acesso restrito ao master"})
        _usuario_atual.set(quem)
        return await call_next(request)

    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if not _enforce():
        logger.warning("FIN_AUTH_DRYRUN %s %s sem token interno válido (ip=%s, com_header=%s)",
                       request.method, path, ip, bool(request.headers.get("authorization")))
        return await call_next(request)
    logger.warning("FIN_AUTH_BLOQUEADO %s %s (ip=%s)", request.method, path, ip)
    return JSONResponse(status_code=401, content={"detail": "Não autenticado"})


async def usuario_interno(request: Request) -> dict:
    """Dependência: equipe interna (token do painel financeiro ou admin master). Devolve role
    'admin_sistema' para manter compatíveis as rotas que já checam esse role, mais nome/tipo/master."""
    quem = _do_header(request)
    if not quem:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return {"role": "admin_sistema", "origem": quem["origem"], "usuario": quem["id"],
            "nome": quem["nome"], "email": quem["email"], "tipo": quem["tipo"], "master": quem["master"]}
