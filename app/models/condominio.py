# ============================================================================
# ARQUIVO: condominio.py
# PASTA: app/models/
# DESCRICAO: Modelo SQLAlchemy da tabela condominios
# VERSAO: 1.1.0 - Adiciona nfe_antes_pagamento (Etapa 3, NFE_FINANCEIRO.md —
#         NF emitida antes do pagamento, por condominio). Coluna ja existe em
#         AdmGeral (ALTER aplicado antes deste deploy), so faltava o mapeamento.
# data criacao: anterior   data alteracao: 2026-09-07
# ============================================================================
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from .base import Base

# Este modelo é para a tabela "condominios" usada pelo módulo de gestão
class Condominio(Base):
    __tablename__ = "condominios"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    cnpj = Column(String(18), unique=True, nullable=False)
    endereco = Column(String(255), nullable=False)
    numero = Column(String(10))
    complemento = Column(String(100))
    bairro = Column(String(100))
    cidade = Column(String(100))
    estado = Column(String(2))
    cep = Column(String(9))
    telefone = Column(String(15))
    email = Column(String(100))
    sindico = Column(String(200))
    total_apartamentos = Column(Integer)
    usa_subportaria = Column(Boolean, default=False)
    envio_whatsapp = Column(String(1), nullable=False, server_default="S")
    permite_auto_cadastro = Column(Boolean, default=False)
    nfe_antes_pagamento = Column(Boolean, default=False)
    observacoes = Column(Text)
    data_cadastro = Column(DateTime(timezone=True), server_default=func.now())
    ativo = Column(Boolean, default=True)

# Este modelo é para a tabela "condominio" (singular) usada pelos operadores
class CondominioOperador(Base):
    __tablename__ = "condominio"
    
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Condomino = Column(Integer, unique=True, nullable=False)
    Nomedocondomino = Column(String(200), nullable=False)
    Rua = Column(String(200))
    Nr = Column(String(50))
    Bairro = Column(String(100))
    Cep = Column(String(10))
    Sindico = Column(String(100))
    Zedador = Column(String(100))
    qtdblocos = Column(Integer)
    ativo = Column(Boolean, nullable=False)
