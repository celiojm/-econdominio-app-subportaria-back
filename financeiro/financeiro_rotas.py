from fastapi import APIRouter

# Importar routers dos sub-módulos
from .financeiro_cobrancas import router as cobrancas_router
from .financeiro_condominios import router as condominios_router
from .financeiro_planos import router as planos_router
from .financeiro_pagamentos import router as pagamentos_router
# NOVO: Router de autenticação própria
from .auth_routes import router as auth_router

# Tentar importar outros routers se existirem
try:
    from .financeiro_assinaturas import router as assinaturas_router
    HAS_ASSINATURAS = True
except ImportError:
    HAS_ASSINATURAS = False

try:
    from .financeiro_webhook import router as webhook_router
    HAS_WEBHOOK = True
except ImportError:
    HAS_WEBHOOK = False

try:
    from .financeiro_sync import router as sync_router
    HAS_SYNC = True
except ImportError:
    HAS_SYNC = False

# NOVO: Router de email
try:
    from .financeiro_email_routes import router as email_router
    HAS_EMAIL = True
except ImportError:
    HAS_EMAIL = False

# NOVO: Router de WhatsApp
try:
    from .financeiro_whatsapp_routes import router as whatsapp_router
    HAS_WHATSAPP = True
except ImportError:
    HAS_WHATSAPP = False

# Router principal do módulo financeiro
router = APIRouter()

# Incluir routers dos sub-módulos
router.include_router(auth_router, tags=["Autenticação"])

router.include_router(cobrancas_router, tags=["Cobranças"])
router.include_router(condominios_router, tags=["Condomínios"])
router.include_router(planos_router, tags=["Planos"])
router.include_router(pagamentos_router, tags=["Pagamentos"])

if HAS_ASSINATURAS:
    router.include_router(assinaturas_router, tags=["Assinaturas"])

if HAS_WEBHOOK:
    router.include_router(webhook_router, tags=["Webhook"])

if HAS_SYNC:
    router.include_router(sync_router, tags=["Sincronização"])

# Email
if HAS_EMAIL:
    router.include_router(email_router, tags=["Email"])

# WhatsApp
if HAS_WHATSAPP:
    router.include_router(whatsapp_router, tags=["WhatsApp"])

# Tentar incluir router de previsão se existir
try:
    from financeiro.financeiro_previsao import router as previsao_router
    router.include_router(previsao_router, tags=["Previsão"])
except ImportError:
    pass

# Dashboard
try:
    from financeiro.financeiro_dashboard import router as dashboard_router
    router.include_router(dashboard_router, tags=["Dashboard"])
except ImportError:
    pass

# Router de Afiliados
try:
    from financeiro.financeiro_afiliados import router as afiliados_router
    router.include_router(afiliados_router, tags=["Afiliados"])
except ImportError:
    pass
# Marketing
try:
    from financeiro.financeiro_marketing import router as marketing_router
    router.include_router(marketing_router, tags=["Marketing"])
except ImportError:
    pass
# Marketing
try:
    from financeiro.financeiro_marketing import router as marketing_router
    router.include_router(marketing_router, tags=["Marketing"])
except ImportError:
    pass
