"""
================================================================================
ARQUIVO: financeiro_modelos.py
PASTA:   /home/visionlpr/encomenda_v2/backend/financeiro/
CAMINHO: /home/visionlpr/encomenda_v2/backend/financeiro/financeiro_modelos.py
================================================================================
Modelos SQLAlchemy e Schemas Pydantic para o módulo financeiro do Econdomínio
Inclui: Plano, Assinatura, Cobranca, EventoWebhook, Enums e Schemas
================================================================================
"""
from sqlalchemy import Column, Integer, String, DECIMAL, Date, Enum as SQLEnum, TIMESTAMP, Text, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal


# ========================== ENUMS ==========================

class AssinaturaStatusEnum(str, Enum):
    """Status possíveis de uma assinatura - atributos em minúsculas"""
    ativa = 'ativa'
    suspensa = 'suspensa'
    cancelada = 'cancelada'
    inadimplente = 'inadimplente'


class CobrancaStatusEnum(str, Enum):
    """Status possíveis de uma cobrança - atributos em minúsculas"""
    pendente = 'pendente'
    pago = 'pago'
    vencido = 'vencido'
    cancelado = 'cancelado'
    estornado = 'estornado'


class FormaPagamentoEnum(str, Enum):
    """Formas de pagamento aceitas - atributos em MAIÚSCULAS"""
    PIX = 'pix'
    BOLETO = 'boleto'
    CARTAO = 'cartao'
    TRANSFERENCIA = 'transferencia'
    DINHEIRO = 'dinheiro'


class CicloEnum(str, Enum):
    """Ciclos de cobrança (compatível com Asaas)"""
    MONTHLY = 'MONTHLY'
    QUARTERLY = 'QUARTERLY'
    SEMIANNUALLY = 'SEMIANNUALLY'
    YEARLY = 'YEARLY'


# ========================== SCHEMAS PYDANTIC ==========================

class CobrancaCreate(BaseModel):
    """Schema para criação de cobrança"""
    id_condominio: int
    id_assinatura: Optional[int] = None
    valor: Decimal
    data_vencimento: date
    descricao: Optional[str] = None
    referencia: Optional[str] = None
    asaas_customer_id: Optional[str] = None
    
    class Config:
        from_attributes = True


class CobrancaUpdate(BaseModel):
    """Schema para atualização de cobrança"""
    valor: Optional[Decimal] = None
    data_vencimento: Optional[date] = None
    status: Optional[CobrancaStatusEnum] = None
    forma_pagamento: Optional[FormaPagamentoEnum] = None
    descricao: Optional[str] = None
    referencia: Optional[str] = None
    
    class Config:
        from_attributes = True


class CobrancaResponse(BaseModel):
    """Schema de resposta para cobrança"""
    id_cobranca: int
    id_condominio: int
    id_assinatura: Optional[int] = None
    valor: float
    valor_pago: Optional[float] = None
    desconto: Optional[float] = 0
    juros: Optional[float] = 0
    multa: Optional[float] = 0
    data_vencimento: Optional[str] = None
    data_pagamento: Optional[str] = None
    status: str
    forma_pagamento: Optional[str] = None
    descricao: Optional[str] = None
    referencia: Optional[str] = None
    asaas_payment_id: Optional[str] = None
    nome_condominio: Optional[str] = None
    condominio: Optional[str] = None
    data_criacao: Optional[str] = None
    
    class Config:
        from_attributes = True


class CobrancaListResponse(BaseModel):
    """Schema de resposta para lista de cobranças"""
    items: List[CobrancaResponse]
    total: int
    page: int
    per_page: int
    pages: int
    
    class Config:
        from_attributes = True


class DashboardResponse(BaseModel):
    """Schema de resposta para dashboard"""
    mrr: float = 0
    mrrVariacao: float = 0
    recebidoMes: float = 0
    recebidoVariacao: float = 0
    inadimplencia: float = 0
    inadimplenciaPercentual: float = 0
    assinaturasAtivas: int = 0
    assinaturasNovas: int = 0
    total_pendente: float = 0
    total_pago_mes: float = 0
    total_vencido: float = 0
    qtd_pendente: int = 0
    qtd_vencido: int = 0
    data_atualizacao: Optional[str] = None
    
    class Config:
        from_attributes = True


class PixResponse(BaseModel):
    """Schema de resposta para PIX"""
    id_cobranca: int
    qr_code: str
    qr_code_image: str
    expiration_date: Optional[str] = None
    value: float
    status: str
    success: bool
    source: str
    message: Optional[str] = None
    
    class Config:
        from_attributes = True


class BoletoResponse(BaseModel):
    """Schema de resposta para Boleto"""
    id_cobranca: int
    boleto_url: Optional[str] = None
    invoice_url: Optional[str] = None
    codigo_barras: Optional[str] = None
    nosso_numero: Optional[str] = None
    due_date: Optional[str] = None
    value: float
    status: str
    success: bool
    source: str
    message: Optional[str] = None
    
    class Config:
        from_attributes = True


class AssinaturaCreate(BaseModel):
    """Schema para criação de assinatura"""
    id_condominio: int
    id_plano: Optional[int] = None
    tipo_plano: str = 'basico'
    valor: Decimal
    ciclo: str = 'MONTHLY'
    data_inicio: date
    renovacao_automatica: bool = True
    asaas_customer_id: Optional[str] = None
    
    class Config:
        from_attributes = True


class AssinaturaUpdate(BaseModel):
    """Schema para atualização de assinatura"""
    id_plano: Optional[int] = None
    tipo_plano: Optional[str] = None
    valor: Optional[Decimal] = None
    ciclo: Optional[str] = None
    status: Optional[AssinaturaStatusEnum] = None
    renovacao_automatica: Optional[bool] = None
    
    class Config:
        from_attributes = True


class AssinaturaResponse(BaseModel):
    """Schema de resposta para assinatura"""
    id_assinatura: int
    id_condominio: int
    id_plano: Optional[int] = None
    tipo_plano: str
    nome_plano: Optional[str] = None
    valor: float
    ciclo: str
    periodicidade: Optional[str] = None
    dias_validade: Optional[int] = None
    data_inicio: Optional[str] = None
    data_renovacao: Optional[str] = None
    validade_ate: Optional[str] = None
    status: str
    renovacao_automatica: bool
    asaas_customer_id: Optional[str] = None
    asaas_subscription_id: Optional[str] = None
    data_criacao: Optional[str] = None
    
    class Config:
        from_attributes = True


class PlanoResponse(BaseModel):
    """Schema de resposta para plano"""
    id_plano: int
    codigo: str
    nome: str
    max_unidades: int
    dias_validade: int
    valor: float
    ativo: bool
    periodicidade: Optional[str] = None
    economia_percentual: Optional[float] = None
    economia_valor: Optional[float] = None
    
    class Config:
        from_attributes = True


# ========================== MODELOS SQLALCHEMY ==========================

class Plano(Base):
    """Modelo para tabela de planos de assinatura"""
    __tablename__ = "planos"
    
    id_plano = Column(Integer, primary_key=True, autoincrement=True)
    codigo = Column(String(20), nullable=False, unique=True)  # Ex: '50-30d', '100-90d'
    nome = Column(String(100), nullable=False)                 # Ex: 'Até 50 unidades - 30 dias'
    max_unidades = Column(Integer, nullable=False)             # 50, 100, 150, 200, 300, 500
    dias_validade = Column(Integer, nullable=False)            # 30, 90, 180, 360
    valor = Column(DECIMAL(10, 2), nullable=False)
    ativo = Column(Integer, default=1)
    data_criacao = Column(TIMESTAMP, server_default=func.now())
    
    # Relacionamento com assinaturas
    assinaturas = relationship("Assinatura", back_populates="plano")
    
    def to_dict(self):
        return {
            "id_plano": self.id_plano,
            "codigo": self.codigo,
            "nome": self.nome,
            "max_unidades": self.max_unidades,
            "dias_validade": self.dias_validade,
            "valor": float(self.valor) if self.valor else 0,
            "ativo": bool(self.ativo),
            "periodicidade": self._get_periodicidade()
        }
    
    def _get_periodicidade(self):
        """Retorna descrição da periodicidade"""
        periodos = {
            30: "Mensal (30 dias)",
            90: "Trimestral (90 dias)",
            180: "Semestral (180 dias)",
            360: "Anual (360 dias)"
        }
        return periodos.get(self.dias_validade, f"{self.dias_validade} dias")


class Assinatura(Base):
    """Modelo para tabela de assinaturas"""
    __tablename__ = "assinaturas"
    
    id_assinatura = Column(Integer, primary_key=True, autoincrement=True)
    id_condominio = Column(Integer, nullable=False)
    id_plano = Column(Integer, ForeignKey('planos.id_plano'), nullable=True)
    tipo_plano = Column(String(50), nullable=False, default='basico')
    valor = Column(DECIMAL(10, 2), nullable=False)
    ciclo = Column(String(20), nullable=False, default='MONTHLY')
    data_inicio = Column(Date, nullable=False)
    data_renovacao = Column(Date, nullable=True)
    validade_ate = Column(Date, nullable=True)
    status = Column(SQLEnum('ativa', 'suspensa', 'cancelada', 'inadimplente'), default='ativa')
    renovacao_automatica = Column(Integer, default=1)
    asaas_customer_id = Column(String(100), nullable=True)
    asaas_subscription_id = Column(String(100), nullable=True)
    data_criacao = Column(TIMESTAMP, server_default=func.now())
    data_atualizacao = Column(TIMESTAMP, onupdate=func.now())
    
    # Relacionamentos
    plano = relationship("Plano", back_populates="assinaturas")
    cobrancas = relationship("Cobranca", back_populates="assinatura")
    
    def to_dict(self):
        plano_info = self.plano.to_dict() if self.plano else None
        return {
            "id_assinatura": self.id_assinatura,
            "id_condominio": self.id_condominio,
            "id_plano": self.id_plano,
            "plano": plano_info,
            "tipo_plano": self.tipo_plano,
            "nome_plano": plano_info["nome"] if plano_info else self.tipo_plano,
            "valor": float(self.valor) if self.valor else 0,
            "ciclo": self.ciclo,
            "periodicidade": plano_info["periodicidade"] if plano_info else self._get_periodicidade_legado(),
            "dias_validade": plano_info["dias_validade"] if plano_info else self._get_dias_legado(),
            "data_inicio": self.data_inicio.isoformat() if self.data_inicio else None,
            "data_renovacao": self.data_renovacao.isoformat() if self.data_renovacao else None,
            "validade_ate": self.validade_ate.isoformat() if self.validade_ate else None,
            "status": self.status,
            "renovacao_automatica": bool(self.renovacao_automatica),
            "asaas_customer_id": self.asaas_customer_id,
            "asaas_subscription_id": self.asaas_subscription_id,
            "data_criacao": self.data_criacao.isoformat() if self.data_criacao else None,
            "data_atualizacao": self.data_atualizacao.isoformat() if self.data_atualizacao else None
        }
    
    def _get_periodicidade_legado(self):
        """Para assinaturas antigas sem id_plano"""
        ciclos = {
            "MONTHLY": "Mensal",
            "QUARTERLY": "Trimestral",
            "SEMIANNUALLY": "Semestral",
            "YEARLY": "Anual"
        }
        return ciclos.get(self.ciclo, self.ciclo)
    
    def _get_dias_legado(self):
        """Para assinaturas antigas sem id_plano"""
        dias = {
            "MONTHLY": 30,
            "QUARTERLY": 90,
            "SEMIANNUALLY": 180,
            "YEARLY": 360
        }
        return dias.get(self.ciclo, 30)


class Cobranca(Base):
    """Modelo para tabela de cobranças"""
    __tablename__ = "cobrancas"
    
    id_cobranca = Column(Integer, primary_key=True, autoincrement=True)
    id_assinatura = Column(Integer, ForeignKey('assinaturas.id_assinatura'), nullable=True)
    id_condominio = Column(Integer, nullable=False)
    valor = Column(DECIMAL(10, 2), nullable=False)
    valor_pago = Column(DECIMAL(10, 2), nullable=True)
    desconto = Column(DECIMAL(10, 2), default=0)
    juros = Column(DECIMAL(10, 2), default=0)
    multa = Column(DECIMAL(10, 2), default=0)
    data_vencimento = Column(Date, nullable=False)
    data_pagamento = Column(Date, nullable=True)
    status = Column(SQLEnum('pendente', 'pago', 'vencido', 'cancelado', 'estornado'), default='pendente')
    forma_pagamento = Column(SQLEnum('pix', 'boleto', 'cartao', 'transferencia', 'dinheiro'), nullable=True)
    descricao = Column(Text, nullable=True)
    referencia = Column(String(50), nullable=True)
    asaas_payment_id = Column(String(100), nullable=True)
    asaas_customer_id = Column(String(100), nullable=True)
    data_criacao = Column(TIMESTAMP, server_default=func.now())
    data_atualizacao = Column(TIMESTAMP, onupdate=func.now())
    
    # Relacionamentos
    assinatura = relationship("Assinatura", back_populates="cobrancas")
    
    def to_dict(self):
        return {
            "id_cobranca": self.id_cobranca,
            "id_assinatura": self.id_assinatura,
            "id_condominio": self.id_condominio,
            "valor": float(self.valor) if self.valor else 0,
            "valor_pago": float(self.valor_pago) if self.valor_pago else None,
            "desconto": float(self.desconto) if self.desconto else 0,
            "juros": float(self.juros) if self.juros else 0,
            "multa": float(self.multa) if self.multa else 0,
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "status": self.status,
            "forma_pagamento": self.forma_pagamento,
            "descricao": self.descricao,
            "referencia": self.referencia,
            "asaas_payment_id": self.asaas_payment_id,
            "asaas_customer_id": self.asaas_customer_id,
            "data_criacao": self.data_criacao.isoformat() if self.data_criacao else None,
            "data_atualizacao": self.data_atualizacao.isoformat() if self.data_atualizacao else None
        }


class EventoWebhook(Base):
    """Modelo para armazenar eventos do webhook Asaas"""
    __tablename__ = "eventos_webhook"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    evento = Column(String(100), nullable=False)
    payload = Column(Text, nullable=True)
    processado = Column(Integer, default=0)
    data_recebimento = Column(TIMESTAMP, server_default=func.now())
    data_processamento = Column(TIMESTAMP, nullable=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "evento": self.evento,
            "payload": self.payload,
            "processado": bool(self.processado),
            "data_recebimento": self.data_recebimento.isoformat() if self.data_recebimento else None,
            "data_processamento": self.data_processamento.isoformat() if self.data_processamento else None
        }
