from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.services.auth import get_password_hash, verify_password

router = APIRouter()

@router.get("/test-password")
async def test_password(db: Session = Depends(get_db)):
    """Testar sistema de senha"""
    try:
        # Gerar novo hash
        new_hash = get_password_hash("admin123")
        
        # Buscar senha atual
        result = db.execute(text("SELECT Senha FROM operadores WHERE Nome = 'admin'"))
        current = result.fetchone()
        
        return {
            "hash_gerado": new_hash,
            "senha_atual_tamanho": len(current[0]) if current else 0,
            "instrucao_sql": f"UPDATE operadores SET Senha = '{new_hash}' WHERE Nome = 'admin';"
        }
    except Exception as e:
        return {"erro": str(e)}
