# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/mobile/main.py
# DESCRIÇÃO: Aplicação FastAPI principal para API mobile
# ==============================================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import auth
import moradores
import encomendas

app = FastAPI(
    title="ECondominio Mobile API",
    version="1.0.0",
    description="API Backend para aplicação mobile de condomínios"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://portaria.econdominio.com.br",
        "https://mobile.inforseg.com.br",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir rotas
app.include_router(auth.router)
app.include_router(moradores.router)
app.include_router(encomendas.router)

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "ECondominio Mobile API",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
