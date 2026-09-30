# ================================================================================
# ARQUIVO: __init__.py
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/__init__.py
# ================================================================================
# DESCRIÇÃO: Inicialização do módulo de afiliados
# ================================================================================

from .afiliado_models import (
    Afiliado,
    AfiliadoCondominio,
    AfiliadoComissao,
    AfiliadoSaque,
    AfiliadoLog,
    AfiliadoCreate,
    AfiliadoUpdate,
    AfiliadoResponse,
    DashboardAfiliadoResponse,
    ComissaoResponse,
    SaqueCreate,
    SaqueResponse
)

from .afiliado_routes import router as afiliado_router
from .auth_routes import router as afiliado_auth_router
from .afiliado_service import (
    calcular_comissao_afiliado,
    liberar_comissoes_pendentes,
    get_dashboard_data
)
from .afiliado_webhook import (
    processar_webhook_pagamento,
    processar_cancelamento_assinatura
)
from .afiliado_cadastro_integration import vincular_afiliado_ao_cadastro

__all__ = [
    'Afiliado',
    'AfiliadoCondominio',
    'AfiliadoComissao',
    'AfiliadoSaque',
    'AfiliadoLog',
    'afiliado_router',
    'afiliado_auth_router',
    'calcular_comissao_afiliado',
    'liberar_comissoes_pendentes',
    'get_dashboard_data',
    'processar_webhook_pagamento',
    'processar_cancelamento_assinatura',
    'vincular_afiliado_ao_cadastro'
]
