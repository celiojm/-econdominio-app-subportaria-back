from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class Operador(Base):
    __tablename__ = "operadores"
    
    id = Column(Integer, primary_key=True, index=True)
    Nome = Column(String(150), nullable=False)
    Senha = Column(String(200), nullable=False)
    Nivel_id = Column(Integer, ForeignKey("permissoes.nivel"), nullable=False)
    Condomino_id = Column(Integer, ForeignKey("condominio.Condomino"), nullable=False)
    Nomedocondomino = Column(String(200), nullable=False)

class Morador(Base):
    __tablename__ = "moradores"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    apartamento = Column(String(50), nullable=False)
    bloco = Column(String(50))
    telefone = Column(String(20))
    email = Column(String(100))
    condominio_id = Column(Integer, default=1)
    data_cadastro = Column(DateTime, default=datetime.utcnow)
    ativo = Column(Boolean, default=True)

class Encomenda(Base):
    __tablename__ = "encomendas"
    
    id = Column(Integer, primary_key=True, index=True)
    destinatario = Column(String(200), nullable=False)
    apartamento = Column(String(50), nullable=False)
    bloco = Column(String(50))
    remetente = Column(String(200))
    empresa_entrega = Column(String(100))
    codigo_rastreio = Column(String(100))
    tipo_encomenda = Column(String(50))
    data_recebimento = Column(DateTime, default=datetime.utcnow)
    data_entrega = Column(DateTime)
    status = Column(String(20), default="Pendente")
    observacoes = Column(Text)
    recebido_por = Column(String(200))
    entregue_para = Column(String(200))
    documento_retirada = Column(String(50))
    condominio_id = Column(Integer, default=1)
    morador_id = Column(Integer, ForeignKey("moradores.id"))
    foto_url = Column(String(500))

class Condominio(Base):
    __tablename__ = "condominios"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    cnpj = Column(String(20), nullable=False, unique=True)
    endereco = Column(String(200), nullable=False)
    numero = Column(String(20), nullable=False)
    complemento = Column(String(100))
    bairro = Column(String(100), nullable=False)
    cidade = Column(String(100), nullable=False)
    estado = Column(String(2), nullable=False)
    cep = Column(String(10), nullable=False)
    telefone = Column(String(20), nullable=False)
    email = Column(String(150), nullable=False)
    sindico = Column(String(150), nullable=False)
    total_apartamentos = Column(Integer, nullable=False)
    observacoes = Column(Text)
    data_cadastro = Column(DateTime, default=datetime.utcnow)
    ativo = Column(Boolean, default=True)
