# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/app/main.py
# ALTERAÇÃO 2026-09-26: middleware proteger_financeiro (auth em /api/financeiro/*)
# ==============================================================================
# Main.py ATUALIZADO com autenticação mobile
# ==============================================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import logging

from .database import engine, Base

from .api import auth, moradores, encomendas, operadores, condominio, configuracoes, etiqueta, storage, whatsapp, locais_armazenamento, locais_armazenamento_admin, lotes

from .routers import ocr_router
from app.routers import routers_mobi_receber
from app.routers import routes_storage
from admin.main import router as admin_router
# Importar rotas do financeiro
from financeiro.financeiro_rotas import router as financeiro_router
from financeiro_resgatar import router as resgatar_router
from financeiro.prospeccao_routes import router as prospeccao_router
from financeiro.financeiro_contas_pagar import router as contas_pagar_router
from financeiro.financeiro_assinaturas import router as assinaturas_router
from financeiro.financeiro_assinaturas import router as assinaturas_router
from financeiro.financeiro_condominio_routes import router as financeiro_cond_router
from financeiro.financeiro_nfe import router as nfe_router
from financeiro.financeiro_preco_routes import router as preco_router

# ============================================
# NOVO: Importar rotas de autenticação mobile
# ============================================
from mobile.auth_routes import router as mobile_auth_router
from mobile.auth_password_routes import router as password_reset_router
from mobile.assinatura_routes import router as assinatura_router
# Importar rotas de cadastro cliente (PÚBLICO)
from cliente.router import cliente_router
from afiliado import afiliado_router, afiliado_auth_router
from afiliado.indicacao_routes import router as indicacao_router
from afiliado.afiliado_routes import router as afiliado_router
from afiliado.indicacao_routes import router as indicacao_router  # ← já existe (linha 31)

from afiliado.indicacao_routes import router as indicacao_router
from financeiro.afiliados_routes import router as financeiro_afiliados_router
#webhook_whatsApp
from app.routers.whatsapp_webhook import router as whatsapp_webhook_router
# Leads públicos (funil /conheca do site + WhatsApp externo)
from leads.router import router as leads_router
# Configurar logging
logger = logging.getLogger(__name__)

# Criar as tabelas
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sistema de Gestão de Encomendas")

# Exception handler para erro 422 - DEVE VIR ANTES DOS ROUTERS
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    exc_str = f'{exc}'.replace('\n', ' ').replace('   ', ' ')
    logger.error("=" * 80)
    logger.error(f"❌ ERRO 422 - VALIDAÇÃO FALHOU")
    logger.error(f"Request: {request.method} {request.url}")
    logger.error(f"Detalhes: {exc_str}")
    logger.error(f"Erros: {exc.errors()}")
    logger.error(f"Body recebido: {exc.body}")
    logger.error("=" * 80)

    content = {
        'status_code': 422,
        'message': 'Erro de validação',
        'detail': [
            {k: str(v) if not isinstance(v, (str, int, float, bool, list)) else v
             for k, v in err.items() if k != 'ctx'}
            for err in exc.errors()
        ],
        'body': str(exc.body)[:500]
    }

    return JSONResponse(content=content, status_code=422)

# Exige token interno em /api/financeiro/* (flag FINANCEIRO_AUTH_ENFORCE). Registrado antes do
# CORS para que as respostas 401 também recebam os headers de CORS.
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.protecao_financeiro import proteger_financeiro
app.add_middleware(BaseHTTPMiddleware, dispatch=proteger_financeiro)
# 2026-09-30: log de auditoria (grava em mobile_audit_logs as escritas bem-sucedidas)
from app.services.auditoria import AuditoriaMiddleware
app.add_middleware(AuditoriaMiddleware)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://financeiro.econdominio.app.br",
        "https://portaria.econdominio.com.br",
        "https://econdominio.com.br",
        "https://www.econdominio.com.br",
        "https://admin.econdominio.com.br",
        "https://admin.econdominio.app.br",
        "https://painel.econdominio.com.br",
        "https://painel.econdominio.app.br",
        "https://portaria.econdominio.app.br",
        "https://afiliado.econdominio.com.br",
        "https://afiliado.econdominio.app.br",
        "http://localhost:3006",
        "http://localhost:5173",
        "http://localhost:3000"  # NOVO: Desenvolvimento React
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# ROTAS DE AUTENTICAÇÃO MOBILE (NOVO)
# ============================================
app.include_router(mobile_auth_router)
app.include_router(admin_router)
app.include_router(password_reset_router, prefix="/api")

# Incluir rotas existentes
app.include_router(routers_mobi_receber.router, prefix="/api/mobi", tags=["mobile-receber"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(moradores.router, prefix="/api/moradores", tags=["moradores"])
app.include_router(encomendas.router, prefix="/api/encomendas", tags=["encomendas"])
from app.api import auditoria as auditoria_api  # 2026-09-30: relatório de log
app.include_router(auditoria_api.router, prefix="/api/auditoria", tags=["auditoria"])
from app.api import unidades as unidades_api  # 2026-09-30: cadastro de unidades
app.include_router(unidades_api.router, prefix="/api/unidades", tags=["unidades"])
app.include_router(lotes.router, prefix="/api/lotes", tags=["lotes"])
app.include_router(condominio.router, prefix="/api/condominios", tags=["condominios"])
app.include_router(locais_armazenamento.router, prefix="/api/locais-armazenamento", tags=["locais-armazenamento"])
app.include_router(locais_armazenamento_admin.router, prefix="/api/admin/locais-armazenamento", tags=["locais-armazenamento-admin"])
app.include_router(operadores.router, prefix="/api/operadores", tags=["operadores"])
app.include_router(leads_router, prefix="/api")

app.include_router(configuracoes.router, prefix="/api/configuracoes", tags=["configuracoes"])
app.include_router(etiqueta.router, prefix="/api/etiqueta", tags=["etiqueta"])
app.include_router(routes_storage.router)
app.include_router(storage.router, prefix="/api/storage", tags=["storage"])
app.include_router(ocr_router.router)
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["whatsapp"])
app.include_router(indicacao_router)
app.include_router(financeiro_afiliados_router)
# Rotas do módulo financeiro
app.include_router(assinaturas_router, prefix="/api/financeiro", tags=["assinaturas"])
app.include_router(financeiro_router, prefix="/api/financeiro", tags=["Financeiro"])
app.include_router(prospeccao_router, tags=["prospeccao"])
app.include_router(assinaturas_router, prefix="/api/financeiro", tags=["assinaturas"])
app.include_router(contas_pagar_router, prefix="/api/financeiro", tags=["contas-pagar"])
app.include_router(resgatar_router, prefix="/api/financeiro", tags=["resgatar"])
app.include_router(financeiro_cond_router, prefix="/painel/financeiro", tags=["financeiro-condominio"])
app.include_router(nfe_router)
app.include_router(preco_router, prefix="/api/financeiro", tags=["precos"])

# Rotas públicas de cadastro de clientes
app.include_router(cliente_router, prefix="/api")
app.include_router(assinatura_router)
app.include_router(afiliado_auth_router)

app.include_router(afiliado_router)
app.include_router(indicacao_router)  # ← ADICIONE ESTA LINHA
#webhook whatsapp


app.include_router(whatsapp_webhook_router, prefix="/api")
#webhook gmail
from app.routers.gmail_webhook import router as gmail_webhook_router
app.include_router(gmail_webhook_router)
@app.get("/")
def read_root():
    return {"message": "Sistema de Gestão de Encomendas"}

@app.get("/health")  # ← ADICIONE ESTA
def health_check_simple():
    return {"status": "ok"}

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

