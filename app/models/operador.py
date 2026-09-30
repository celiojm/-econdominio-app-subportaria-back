from sqlalchemy import Column, Integer, String
from app.models.base import Base


class Operador(Base):
    __tablename__ = "operadores"
    
    # Colunas principais - SEM FOREIGN KEYS
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    Nome = Column(String(150), nullable=False, index=True)
    Senha = Column(String(200), nullable=False)
    Nivel_id = Column(Integer, nullable=False, index=True)
    Condomino_id = Column(Integer, nullable=False, index=True)
    Nomedocondomino = Column(String(200), nullable=False)
    telefone = Column(String(20), nullable=True)
    email = Column(String(150), nullable=True)
    
    def __repr__(self):
        return f"<Operador(id={self.id}, Nome='{self.Nome}', Nivel_id={self.Nivel_id}, Condomino_id={self.Condomino_id})>"
    
    def __str__(self):
        return f"{self.Nome} (ID: {self.id})"
    
    @property
    def is_admin(self):
        return self.Nivel_id == 1
    
    @property
    def is_master(self):
        return self.Nome.lower() == "admin" and self.Nivel_id == 1
