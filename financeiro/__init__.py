# ================================================================================
#  PATH: backend/financeiro/__init__.py
#  DESCRIPTION: Módulo financeiro – Econdomínio / Inforseg
# ================================================================================

"""
Módulo Financeiro - Econdomínio / Inforseg

Este pacote contém:
- financeiro_modelos: Models SQLAlchemy e Schemas Pydantic
- financeiro_asaas_service: Integração com API Asaas
- financeiro_assinaturas: Rotas de gestão de assinaturas
- financeiro_cobrancas: Rotas de gestão de cobranças
- financeiro_webhook: Endpoint para webhooks do Asaas
- financeiro_rotas: Agregador de rotas do módulo
"""

from .financeiro_rotas import router as financeiro_router

__all__ = ["financeiro_router"]
