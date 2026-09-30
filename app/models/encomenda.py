from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Enum, JSON, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base
import enum

class StatusEncomenda(str, enum.Enum):
    pendente = "pendente"
    entregue = "entregue"

class Encomenda(Base):
    __tablename__ = "encomendas"
    
    id = Column(Integer, primary_key=True, index=True)
    condominio_id = Column(Integer, ForeignKey("condominios.id"), nullable=False, default=1)
    nome_destinatario = Column(String(200), nullable=False)
    apartamento = Column(String(50), nullable=False)
    bloco = Column(String(10))
    codigo_rastreio = Column(String(100))
    remetente = Column(String(200))
    status = Column(Enum(StatusEncomenda), default=StatusEncomenda.pendente)
    data_recebimento = Column(DateTime, default=func.now())
    data_entrega = Column(DateTime)
    nome_retirou = Column(String(200))
    assinatura_base64 = Column(Text)
    data_assinatura = Column(DateTime)
    operador_entrega = Column(String(200))
    observacoes = Column(Text)
    telefone_morador = Column(String(20))
    imagem_etiqueta = Column(Text)
    ocr_data = Column(JSON)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    morador_id = Column(Integer, ForeignKey("moradores.id"))
    
    # Relacionamentos
    condominio = relationship("Condominio", back_populates="encomendas")
    morador = relationship("Morador", back_populates="encomendas")
