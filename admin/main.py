# ============================================================================
# ARQUIVO: main.py (admin)
# PASTA: /home/visionlpr/backend/admin/
# DESCRIÇÃO: Router principal do módulo admin/painel
# VERSÃO: 2.0.12
# ============================================================================

from fastapi import APIRouter

# Importar sub-routers existentes
from .auth import router as auth_router
from .operadores import router as operadores_router
from .cobrancas import router as cobrancas_router
from .whatsapp import router as whatsapp_router
from .password_reset_routes import router as password_reset_router
from .moradores_importacao import router as moradores_importacao_router
from .leads import router as leads_admin_router
from .usuarios_sistema import router as usuarios_sistema_router  # 2026-10-04

# Router principal
router = APIRouter()

# Incluir sub-routers COM PREFIXO /painel
router.include_router(auth_router, prefix="/painel")
router.include_router(operadores_router, prefix="/painel")
router.include_router(cobrancas_router, prefix="/painel")
router.include_router(whatsapp_router)  # Já tem prefix="/painel/whatsapp" interno
router.include_router(password_reset_router, prefix="/painel")
router.include_router(moradores_importacao_router, prefix="/painel")
router.include_router(leads_admin_router, prefix="/painel")
router.include_router(usuarios_sistema_router, prefix="/painel")  # 2026-10-04: Usuários do sistema (só master)

# Endpoint de health check
@router.get("/painel/health")
async def health_check():
    return {
        "status": "ok",
        "module": "admin/painel",
        "version": "2.0.12",
        "features": ["auth", "operadores", "cobrancas", "whatsapp", "password_reset", "moradores_importacao", "leads"]
    }
