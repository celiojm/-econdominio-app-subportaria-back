# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/mobile/auth.py
# DESCRIÇÃO: Autenticação JWT para API mobile
# CONFIGURAÇÃO: Lê APENAS do arquivo .env - NÃO contém valores hardcoded
# ==============================================================================

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from jose import JWTError, jwt
from datetime import datetime, timedelta
import mysql.connector
from database import get_db_connection
import os
from dotenv import load_dotenv
import sys

# Carregar .env do diretório pai (backend principal)
env_path = "/home/visionlpr/backend/.env"
load_dotenv(env_path)

router = APIRouter(prefix="/mobile/auth", tags=["auth"])
security = HTTPBearer()

# Validar variáveis obrigatórias do .env
required_vars = ["SECRET_KEY", "JWT_ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES"]
missing_vars = []

for var in required_vars:
    if not os.getenv(var):
        missing_vars.append(var)

if missing_vars:
    print(f"❌ ERRO: Variáveis faltando no .env: {', '.join(missing_vars)}")
    print(f"❌ Arquivo .env: {env_path}")
    sys.exit(1)

# Configurações JWT do .env (SEM fallback)
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))

print(f"✅ Configurações JWT carregadas do .env")
print(f"   - Algoritmo: {ALGORITHM}")
print(f"   - Expiração: {ACCESS_TOKEN_EXPIRE_MINUTES} minutos")

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Criar token JWT"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Obter usuário atual do token JWT"""
    token = credentials.credentials
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Verificar expiração
        exp = payload.get("exp")
        if exp and datetime.fromtimestamp(exp) < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expirado"
            )
        
        user_data = {
            "username": payload.get("sub"),
            "user_id": payload.get("user_id"),
            "email": payload.get("email"),
            "nome": payload.get("nome"),
            "role": payload.get("role"),
            "condominio_id": payload.get("condominio_id"),
            "condominio_nome": payload.get("condominio_nome")
        }
        
        if not user_data["email"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido"
            )
        
        return user_data
        
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido"
        )

@router.post("/login")
async def login(credentials: dict):
    """Login mobile - aceita email/telefone e password"""
    try:
        identifier = credentials.get("identifier")  # email ou telefone
        password = credentials.get("password")
        
        if not identifier or not password:
            raise HTTPException(
                status_code=400,
                detail="Email/telefone e senha são obrigatórios"
            )
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Buscar operador por email ou telefone
        query = """
            SELECT id, email, nome, telefone, condominio_id, role, ativo, senha_hash
            FROM mobile_operadores
            WHERE (email = %s OR telefone = %s) AND ativo = 1
        """
        
        cursor.execute(query, (identifier, identifier))
        operador = cursor.fetchone()
        
        if not operador:
            cursor.close()
            conn.close()
            raise HTTPException(
                status_code=401,
                detail="Usuário não encontrado"
            )
        
        # Verificar senha com Argon2
        try:
            from argon2 import PasswordHasher
            ph = PasswordHasher()
            ph.verify(operador['senha_hash'], password)
        except Exception as e:
            cursor.close()
            conn.close()
            raise HTTPException(
                status_code=401,
                detail="Senha incorreta"
            )
        
        # Buscar nome do condomínio
        cursor.execute("""
            SELECT nome FROM condominios WHERE id = %s
        """, (operador['condominio_id'],))
        
        condominio = cursor.fetchone()
        condominio_nome = condominio['nome'] if condominio else ""
        
        cursor.close()
        conn.close()
        
        # Criar token
        access_token = create_access_token({
            "sub": str(operador['id']),
            "user_id": operador['id'],
            "email": operador['email'],
            "nome": operador['nome'],
            "role": operador['role'],
            "condominio_id": operador['condominio_id'],
            "condominio_nome": condominio_nome
        })
        
        return {
            "success": True,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": operador['id'],
                "email": operador['email'],
                "nome": operador['nome'],
                "telefone": operador['telefone'],
                "condominio_id": operador['condominio_id'],
                "condominio_nome": condominio_nome,
                "role": operador['role']
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao fazer login: {str(e)}"
        )

@router.get("/me")
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """Obter informações do usuário atual"""
    return current_user
