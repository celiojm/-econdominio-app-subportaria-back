# ==============================================================================
# ARQUIVO: /home/visionlpr/backend/mobile/database.py
# DESCRIÇÃO: Configuração de conexão MySQL com pool de conexões
# CONFIGURAÇÃO: Lê APENAS do arquivo .env - NÃO contém valores hardcoded
# ==============================================================================

import mysql.connector
from mysql.connector import pooling
import os
from dotenv import load_dotenv
import sys

# Carregar .env do diretório pai (backend principal)
env_path = "/home/visionlpr/backend/.env"
load_dotenv(env_path)

# Validar variáveis obrigatórias do .env
required_vars = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
missing_vars = []

for var in required_vars:
    if not os.getenv(var):
        missing_vars.append(var)

if missing_vars:
    print(f"❌ ERRO: Variáveis faltando no .env: {', '.join(missing_vars)}")
    print(f"❌ Arquivo .env: {env_path}")
    sys.exit(1)

# Configurações do banco de dados do .env (SEM fallback)
db_config = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "charset": "utf8mb4",
    "collation": "utf8mb4_unicode_ci"
}

# Pool de conexões
try:
    connection_pool = pooling.MySQLConnectionPool(
        pool_name="mobile_pool",
        pool_size=10,
        pool_reset_session=True,
        **db_config
    )
    print("✅ Pool de conexões MySQL criado com sucesso")
except Exception as e:
    print(f"❌ ERRO ao criar pool de conexões: {e}")
    print(f"❌ Verifique as credenciais no .env: {env_path}")
    sys.exit(1)

def get_db_connection():
    """Retorna uma conexão do pool"""
    return connection_pool.get_connection()
