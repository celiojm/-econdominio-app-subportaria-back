from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from .base import Base

class Morador(Base):
    __tablename__ = "moradores"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    apartamento = Column(String(50), nullable=False)
    bloco = Column(String(10))
    telefone = Column(String(20))
    email = Column(String(100))
    condominio_id = Column(Integer, ForeignKey("condominios.id"), nullable=False)
    condominio_nome = Column(String(200))
    data_cadastro = Column(DateTime(timezone=True), server_default=func.now())
    ativo = Column(Boolean, default=True)
