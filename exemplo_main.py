"""
Exemplo de Integração no main.py

Adicione este código ao seu main.py existente em /home/visionlpr/backend/app/main.py
"""

# ============================================
# 1. IMPORTS NO TOPO DO ARQUIVO
# ============================================

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

# Importar rotas de autenticação
from auth_routes import router as auth_router
from auth_dependencies import get_current_user, TokenData

# ============================================
# 2. CONFIGURAR CORS
# ============================================

app = FastAPI(title="E-Condomínio API", version="2.0.0")

# CORS - permitir frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://portaria.econdominio.com.br",
        "http://localhost:3000"  # Desenvolvimento
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# 3. INCLUIR ROTAS DE AUTENTICAÇÃO
# ============================================

app.include_router(auth_router)

# ============================================
# 4. EXEMPLO DE ROTA PROTEGIDA COM MULTI-TENANT
# ============================================

@app.get("/api/moradores")
async def listar_moradores(
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Exemplo de rota protegida que respeita multi-tenant
    
    IMPORTANTE: Sempre filtrar por id_condominio do usuário autenticado
    """
    # NUNCA confiar em id_condominio vindo do frontend
    # SEMPRE usar current_user.id_condominio
    
    with db.cursor() as cursor:
        query = """
            SELECT id, nome, apartamento, telefone
            FROM moradores
            WHERE condominio_id = %s
            ORDER BY nome
        """
        cursor.execute(query, (current_user.id_condominio,))
        moradores = cursor.fetchall()
    
    return {
        "condominio_id": current_user.id_condominio,
        "total": len(moradores),
        "moradores": moradores
    }


@app.post("/api/moradores")
async def criar_morador(
    dados: dict,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Criar morador - sempre no condomínio do usuário autenticado
    """
    # FORÇAR condominio_id do usuário autenticado
    dados['condominio_id'] = current_user.id_condominio
    
    # ... resto da lógica de criação
    
    return {"message": "Morador criado", "condominio_id": current_user.id_condominio}


# ============================================
# 5. EXEMPLO COM RBAC (Role-Based Access)
# ============================================

from auth_dependencies import require_admin, require_porteiro

@app.delete("/api/moradores/{morador_id}", dependencies=[Depends(require_admin)])
async def deletar_morador(
    morador_id: int,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Deletar morador - apenas admins
    
    Verifica que o morador pertence ao condomínio do admin
    """
    with db.cursor() as cursor:
        # Verificar se morador existe E pertence ao condomínio do usuário
        cursor.execute(
            "SELECT id FROM moradores WHERE id = %s AND condominio_id = %s",
            (morador_id, current_user.id_condominio)
        )
        morador = cursor.fetchone()
        
        if not morador:
            raise HTTPException(
                status_code=404,
                detail="Morador não encontrado ou não pertence ao seu condomínio"
            )
        
        cursor.execute("DELETE FROM moradores WHERE id = %s", (morador_id,))
        db.commit()
    
    return {"message": "Morador deletado"}


@app.post("/api/encomendas", dependencies=[Depends(require_porteiro)])
async def registrar_encomenda(
    dados: dict,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Registrar encomenda - porteiros e admins
    """
    # Garantir que encomenda é do condomínio correto
    dados['condominio_id'] = current_user.id_condominio
    dados['registrado_por'] = current_user.user_id
    
    # ... lógica de criação
    
    return {"message": "Encomenda registrada"}


# ============================================
# 6. HEALTH CHECK
# ============================================

@app.get("/api/health")
async def health_check():
    """Health check da API"""
    return {"status": "ok", "service": "econdominio-api"}


# ============================================
# 7. ROOT
# ============================================

@app.get("/")
async def root():
    return {
        "service": "E-Condomínio API",
        "version": "2.0.0",
        "docs": "/docs",
        "auth": "/auth/health"
    }


# ============================================
# 8. STARTUP EVENT (OPCIONAL)
# ============================================

@app.on_event("startup")
async def startup_event():
    """
    Executado ao iniciar a aplicação
    """
    import logging
    from auth_utils import get_auth_info
    
    logger = logging.getLogger(__name__)
    
    # Log de info sobre autenticação
    auth_info = get_auth_info()
    logger.info("🔐 Sistema de Autenticação Inicializado")
    for key, value in auth_info.items():
        logger.info(f"   {key}: {value}")
