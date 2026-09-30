# ================================================================================
# ARQUIVO: afiliado_models.py
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/afiliado_models.py
# ================================================================================
# DESCRIÇÃO: Modelos SQLAlchemy e Schemas Pydantic para o sistema de afiliados
# ================================================================================

from sqlalchemy import Column, Integer, String, DECIMAL, DateTime, Enum as SQLEnum, Text, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base
from enum import Enum
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal


# ========================== ENUMS ==========================

class TipoComissaoEnum(str, Enum):
    """Tipos de comissão disponíveis"""
    PRIMEIRO_MES = 'primeiro_mes'
    RECORRENTE = 'recorrente'


class StatusComissaoEnum(str, Enum):
    """Status possíveis de uma comissão"""
    PENDENTE = 'pendente'
    LIBERADA = 'liberada'
    PAGA = 'paga'
    CANCELADA = 'cancelada'


class TipoComissaoRegistroEnum(str, Enum):
    """Tipo de registro da comissão"""
    PRIMEIRA = 'primeira'
    RECORRENTE = 'recorrente'


class StatusSaqueEnum(str, Enum):
    """Status possíveis de um saque"""
    SOLICITADO = 'solicitado'
    EM_ANALISE = 'em_analise'
    APROVADO = 'aprovado'
    PAGO = 'pago'
    REJEITADO = 'rejeitado'


# ========================== SCHEMAS PYDANTIC ==========================

class AfiliadoCreate(BaseModel):
    """Schema para criação de afiliado"""
    usuario_id: int
    tipo_comissao: TipoComissaoEnum
    codigo_afiliado: Optional[str] = None  # Se não fornecido, será gerado
    percentual: Optional[Decimal] = None  # Se não fornecido, usa padrão do tipo
    observacoes: Optional[str] = None
    
    class Config:
        from_attributes = True


class AfiliadoUpdate(BaseModel):
    """Schema para atualização de afiliado"""
    tipo_comissao: Optional[TipoComissaoEnum] = None
    percentual: Optional[Decimal] = None
    ativo: Optional[bool] = None
    observacoes: Optional[str] = None
    
    class Config:
        from_attributes = True


class AfiliadoResponse(BaseModel):
    """Schema de resposta para afiliado"""
    id: int
    usuario_id: int
    nome_completo: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    cpf: Optional[str] = None
    conta_pix: Optional[str] = None
    tipo_pix: Optional[str] = None
    codigo_afiliado: str
    tipo_comissao: str
    percentual: float
    ativo: bool
    is_admin: Optional[int] = 0
    data_cadastro: str
    link_afiliado: str
    total_indicacoes: Optional[int] = 0
    total_comissoes: Optional[float] = 0
    saldo_disponivel: Optional[float] = 0

    class Config:
        from_attributes = True



class DashboardAfiliadoResponse(BaseModel):
    """Schema de resposta para dashboard do afiliado"""
    total_indicacoes: int = 0
    indicacoes_ativas: int = 0
    indicacoes_trial: int = 0
    indicacoes_canceladas: int = 0
    total_ganho: float = 0.0
    total_pago: float = 0.0
    total_pendente: float = 0.0
    saldo_disponivel: float = 0.0
    proximos_pagamentos: float = 0.0
    comissoes_mes_atual: float = 0.0
    
    class Config:
        from_attributes = True


class CondominioIndicadoResponse(BaseModel):
    """Schema de resposta para condomínio indicado"""
    id: int
    condominio_id: int
    nome_condominio: str
    cnpj: Optional[str] = None
    data_vinculo: str
    status_assinatura: str
    total_comissoes: float = 0.0
    ultima_comissao: Optional[str] = None
    plano_atual: Optional[str] = None
    valor_plano: Optional[float] = None
    
    class Config:
        from_attributes = True


class ComissaoResponse(BaseModel):
    """Schema de resposta para comissão"""
    id: int
    condominio_id: int
    nome_condominio: str
    cobranca_id: int
    valor_comissao: float
    status: str
    tipo: str
    motivo_bloqueio: Optional[str] = None
    data_criacao: str
    data_liberacao: Optional[str] = None
    data_pagamento: Optional[str] = None
    
    class Config:
        from_attributes = True


class ComissaoListResponse(BaseModel):
    """Schema de resposta para lista de comissões"""
    items: List[ComissaoResponse]
    total: int
    page: int
    per_page: int
    pages: int
    
    class Config:
        from_attributes = True


class SaqueCreate(BaseModel):
    """Schema para criação de saque"""
    valor_solicitado: Decimal = Field(gt=0, description="Valor deve ser maior que zero")
    metodo_pagamento: str = 'pix'
    dados_pagamento: str = Field(description="Chave PIX ou dados bancários")
    observacoes: Optional[str] = None
    
    @validator('valor_solicitado')
    def validate_valor_minimo(cls, v):
        if v < Decimal('100.00'):
            raise ValueError('Valor mínimo para saque é R$ 100,00')
        return v
    
    class Config:
        from_attributes = True


class SaqueUpdate(BaseModel):
    """Schema para atualização de saque pelo admin"""
    status: StatusSaqueEnum
    valor_pago: Optional[Decimal] = None
    motivo_rejeicao: Optional[str] = None
    comprovante: Optional[str] = None
    observacoes: Optional[str] = None
    
    class Config:
        from_attributes = True


class SaqueResponse(BaseModel):
    """Schema de resposta para saque"""
    id: int
    afiliado_id: int
    valor_solicitado: float
    valor_pago: Optional[float] = None
    status: str
    metodo_pagamento: str
    data_solicitacao: str
    data_aprovacao: Optional[str] = None
    data_pagamento: Optional[str] = None
    motivo_rejeicao: Optional[str] = None
    
    class Config:
        from_attributes = True


class LinkAfiliadoResponse(BaseModel):
    """Schema de resposta para link de afiliado"""
    codigo: str
    link: str
    link_whatsapp: str
    mensagem_padrao: str
    
    class Config:
        from_attributes = True


# ========================== MODELOS SQLALCHEMY ==========================

class Afiliado(Base):
    """Modelo para tabela de afiliados"""
    __tablename__ = "afiliados"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(Integer, nullable=False)
    codigo_afiliado = Column(String(50), unique=True, nullable=False)
    tipo_comissao = Column(SQLEnum('primeiro_mes', 'recorrente'), nullable=False, default='primeiro_mes')
    percentual = Column(DECIMAL(5, 2), nullable=False, default=100.00)
    ativo = Column(Integer, default=1)
    data_cadastro = Column(DateTime, server_default=func.now())
    data_atualizacao = Column(DateTime, onupdate=func.now())
    observacoes = Column(Text, nullable=True)
    
    # Relacionamentos
    condominios = relationship("AfiliadoCondominio", back_populates="afiliado")
    comissoes = relationship("AfiliadoComissao", back_populates="afiliado")
    saques = relationship("AfiliadoSaque", back_populates="afiliado")
    logs = relationship("AfiliadoLog", back_populates="afiliado")
    
    def to_dict(self):
        return {
            "id": self.id,
            "usuario_id": self.usuario_id,
            "codigo_afiliado": self.codigo_afiliado,
            "tipo_comissao": self.tipo_comissao,
            "percentual": float(self.percentual) if self.percentual else 0,
            "ativo": bool(self.ativo),
            "data_cadastro": self.data_cadastro.isoformat() if self.data_cadastro else None,
            "data_atualizacao": self.data_atualizacao.isoformat() if self.data_atualizacao else None,
            "observacoes": self.observacoes
        }


class AfiliadoCondominio(Base):
    """Modelo para vinculação afiliado-condomínio"""
    __tablename__ = "afiliado_condominios"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    afiliado_id = Column(Integer, ForeignKey('afiliados.id'), nullable=False)
    condominio_id = Column(Integer, ForeignKey('condominios.id'), nullable=False)
    data_vinculo = Column(DateTime, server_default=func.now())
    ip_origem = Column(String(50), nullable=True)
    user_agent = Column(Text, nullable=True)
    ativo = Column(Integer, default=1)
    
    # Relacionamentos
    afiliado = relationship("Afiliado", back_populates="condominios")
    
    def to_dict(self):
        return {
            "id": self.id,
            "afiliado_id": self.afiliado_id,
            "condominio_id": self.condominio_id,
            "data_vinculo": self.data_vinculo.isoformat() if self.data_vinculo else None,
            "ip_origem": self.ip_origem,
            "ativo": bool(self.ativo)
        }


class AfiliadoComissao(Base):
    """Modelo para comissões de afiliado"""
    __tablename__ = "afiliado_comissoes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    afiliado_id = Column(Integer, ForeignKey('afiliados.id'), nullable=False)
    condominio_id = Column(Integer, ForeignKey('condominios.id'), nullable=False)
    cobranca_id = Column(Integer, nullable=False)
    valor_comissao = Column(DECIMAL(10, 2), nullable=False, default=0.00)
    status = Column(SQLEnum('pendente', 'liberada', 'paga', 'cancelada'), default='pendente')
    tipo = Column(SQLEnum('primeira', 'recorrente'), nullable=False)
    motivo_bloqueio = Column(String(100), nullable=True)
    data_criacao = Column(DateTime, server_default=func.now())
    data_liberacao = Column(DateTime, nullable=True)
    data_pagamento = Column(DateTime, nullable=True)
    observacoes = Column(Text, nullable=True)
    
    # Relacionamentos
    afiliado = relationship("Afiliado", back_populates="comissoes")
    
    def to_dict(self):
        return {
            "id": self.id,
            "afiliado_id": self.afiliado_id,
            "condominio_id": self.condominio_id,
            "cobranca_id": self.cobranca_id,
            "valor_comissao": float(self.valor_comissao) if self.valor_comissao else 0,
            "status": self.status,
            "tipo": self.tipo,
            "motivo_bloqueio": self.motivo_bloqueio,
            "data_criacao": self.data_criacao.isoformat() if self.data_criacao else None,
            "data_liberacao": self.data_liberacao.isoformat() if self.data_liberacao else None,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "observacoes": self.observacoes
        }


class AfiliadoSaque(Base):
    """Modelo para saques de afiliado"""
    __tablename__ = "afiliado_saques"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    afiliado_id = Column(Integer, ForeignKey('afiliados.id'), nullable=False)
    valor_solicitado = Column(DECIMAL(10, 2), nullable=False)
    valor_pago = Column(DECIMAL(10, 2), nullable=True)
    status = Column(SQLEnum('solicitado', 'em_analise', 'aprovado', 'pago', 'rejeitado'), default='solicitado')
    metodo_pagamento = Column(String(50), default='pix')
    dados_pagamento = Column(Text, nullable=True)
    data_solicitacao = Column(DateTime, server_default=func.now())
    data_aprovacao = Column(DateTime, nullable=True)
    data_pagamento = Column(DateTime, nullable=True)
    aprovado_por = Column(Integer, nullable=True)
    motivo_rejeicao = Column(Text, nullable=True)
    comprovante = Column(Text, nullable=True)
    observacoes = Column(Text, nullable=True)
    
    # Relacionamentos
    afiliado = relationship("Afiliado", back_populates="saques")
    
    def to_dict(self):
        return {
            "id": self.id,
            "afiliado_id": self.afiliado_id,
            "valor_solicitado": float(self.valor_solicitado) if self.valor_solicitado else 0,
            "valor_pago": float(self.valor_pago) if self.valor_pago else None,
            "status": self.status,
            "metodo_pagamento": self.metodo_pagamento,
            "data_solicitacao": self.data_solicitacao.isoformat() if self.data_solicitacao else None,
            "data_aprovacao": self.data_aprovacao.isoformat() if self.data_aprovacao else None,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "aprovado_por": self.aprovado_por,
            "motivo_rejeicao": self.motivo_rejeicao,
            "observacoes": self.observacoes
        }


class AfiliadoLog(Base):
    """Modelo para logs de atividades do afiliado"""
    __tablename__ = "afiliado_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    afiliado_id = Column(Integer, ForeignKey('afiliados.id'), nullable=False)
    acao = Column(String(100), nullable=False)
    descricao = Column(Text, nullable=True)
    ip_origem = Column(String(50), nullable=True)
    user_agent = Column(Text, nullable=True)
    dados_extra = Column(Text, nullable=True)
    data_log = Column(DateTime, server_default=func.now())
    
    # Relacionamentos
    afiliado = relationship("Afiliado", back_populates="logs")
    
    def to_dict(self):
        return {
            "id": self.id,
            "afiliado_id": self.afiliado_id,
            "acao": self.acao,
            "descricao": self.descricao,
            "ip_origem": self.ip_origem,
            "data_log": self.data_log.isoformat() if self.data_log else None
        }
