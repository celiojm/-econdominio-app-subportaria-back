import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from functools import lru_cache

# Carregar .env
load_dotenv()

class Settings(BaseSettings):
    """
    Configurações da aplicação carregadas do .env
    TODOS os valores de ambiente devem estar aqui
    """
    # ========== DATABASE ==========
    DATABASE_URL: str
    DATABASE_HOST: str = "127.0.0.1"
    DATABASE_PORT: int = 3306
    DATABASE_NAME: str = "AdmGeral"
    DATABASE_USER: str = "econdo"
    DATABASE_PASSWORD: str = ""
    
    # ========== STORAGE ==========
    IMAGE_STORAGE_BASE_URL: str
    IMAGE_STORAGE_API_KEY: str = ""
    
    # ========== JWT ==========
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200  # 30 dias
    
    # ========== GOOGLE CLOUD ==========
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_CLOUD_PROJECT: str = ""
    
    # ========== APIs EXTERNAS ==========
    ETIQUETA_API_URL: str
    
    # ========== WHATSAPP / EVOLUTION API ==========
    WHATSAPP_ENABLED: bool = True
    WHATSAPP_HOST: str = ""
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_INSTANCE: str = ""
    
    # Novos campos para compatibilidade
    WHATSAPP_INSTANCE_KEY: str = ""
    EVOLUTION_API_URL: str = ""
    EVOLUTION_INSTANCE_NAME: str = ""
    EVOLUTION_API_KEY: str = ""
    
    # ========== CONDOMINIO ==========
    NOME_CONDOMINIO: str = "Meu Condomínio"
    
    # ========== CORS ==========
    ALLOWED_ORIGINS: str = "*"
    
    # ========== AMBIENTE ==========
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"

@lru_cache()
def get_settings() -> Settings:
    """Retorna instância única (singleton) das configurações"""
    return Settings()

# Instância global
settings = get_settings()
