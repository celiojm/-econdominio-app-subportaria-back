# ========================================
# Database - Conexão com MySQL
# Módulo Admin
# ========================================

import os
import pymysql
from pymysql.cursors import DictCursor
from contextlib import contextmanager

# ========================================
# Configurações do Banco
# ========================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'AdmGeral'),
    'charset': 'utf8mb4',
    'cursorclass': DictCursor,
    'autocommit': False
}


def get_db_connection():
    """
    Cria e retorna uma conexão com o banco de dados
    
    Uso:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT ...")
                result = cursor.fetchall()
            conn.commit()
        finally:
            conn.close()
    """
    return pymysql.connect(**DB_CONFIG)


@contextmanager
def get_db_cursor():
    """
    Context manager para cursor do banco de dados
    
    Uso:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT ...")
            result = cursor.fetchall()
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            yield cursor
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def test_connection() -> bool:
    """
    Testa a conexão com o banco de dados
    
    Returns:
        True se conectou com sucesso, False caso contrário
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao conectar ao banco: {e}")
        return False


# ========================================
# Queries Utilitárias
# ========================================

def table_exists(table_name: str) -> bool:
    """Verifica se uma tabela existe no banco"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM information_schema.tables 
            WHERE table_schema = %s AND table_name = %s
        """, (DB_CONFIG['database'], table_name))
        result = cursor.fetchone()
        return result['count'] > 0


def get_table_columns(table_name: str) -> list:
    """Retorna lista de colunas de uma tabela"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """, (DB_CONFIG['database'], table_name))
        return cursor.fetchall()
