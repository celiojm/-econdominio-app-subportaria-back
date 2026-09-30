#!/bin/bash

#echo "=== Verificando main.py ==="

#cd ~/encomenda-system/backend

# Verificar se main.py existe
if [ -f "app/main.py" ]; then
    echo "✓ main.py existe"
    echo ""
    echo "Primeiras 50 linhas do main.py:"
    head -50 app/main.py
else
    echo "✗ main.py NÃO encontrado!"
    echo "Criando main.py..."
    
    cat > app/main.py << 'EOF'
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.config import settings
from app.database import engine, Base
from app.api import auth, encomenda, whatsapp, morador

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Importar todos os modelos para criar tabelas
from app.models import operador, condominios, encomenda as encomenda_model, morador as morador_model

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Iniciando aplicação...")
    try:
        # Criar tabelas se não existirem
        Base.metadata.create_all(bind=engine)
        logger.info("Tabelas do banco de dados verificadas/criadas")
    except Exception as e:
        logger.error(f"Erro ao criar tabelas: {e}")
    
    yield
    
    # Shutdown
    logger.info("Encerrando aplicação...")

# Criar aplicação FastAPI
app = FastAPI(
    title="Sistema de Gestão de Encomendas",
    description="API para gestão de encomendas em condomínios",
    version="1.0.0",
    lifespan=lifespan
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir rotas
app.include_router(auth.router, prefix="/auth", tags=["Autenticação"])
app.include_router(encomenda.router, prefix="/api/encomendas", tags=["Encomendas"])
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp"])
app.include_router(morador.router, prefix="/api/moradores", tags=["Moradores"])

@app.get("/")
async def root():
    return {
        "message": "Sistema de Gestão de Encomendas - API",
        "status": "online",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
        "whatsapp": settings.WHATSAPP_ENABLED
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
EOF
    
    echo "✓ main.py criado!"
fi
