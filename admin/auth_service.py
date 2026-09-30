# ========================================
# Auth Service - Lógica de Autenticação
# Módulo Admin - Login por Email OU WhatsApp
# ========================================

import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# ========================================
# CONFIGURAÇÃO JWT - USA SECRET_KEY CORRETA
# ========================================
# IMPORTANTE: Usa a mesma SECRET_KEY do config.py para 
# garantir compatibilidade com outros módulos
JWT_SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# Password hasher (Argon2)
ph = PasswordHasher()


class AuthService:
    """
    Serviço de autenticação para módulo Admin
    
    Características:
    - Login por email OU telefone (WhatsApp)
    - Usa SECRET_KEY compartilhada com outros módulos
    - Compatível com tabela mobile_operadores
    """
    
    def __init__(self, db_connection=None):
        self.db = db_connection
    
    # ========================================
    # Métodos de Identificação
    # ========================================
    
    @staticmethod
    def is_email(identificador: str) -> bool:
        """Verifica se o identificador é um email"""
        return '@' in identificador
    
    @staticmethod
    def is_telefone(identificador: str) -> bool:
        """Verifica se o identificador é um telefone"""
        numeros = re.sub(r'\D', '', identificador)
        return len(numeros) >= 10 and len(numeros) <= 13
    
    @staticmethod
    def normalizar_telefone(telefone: str) -> str:
        """Remove caracteres não numéricos do telefone"""
        return re.sub(r'\D', '', telefone)
    
    # ========================================
    # Métodos de Senha
    # ========================================
    
    @staticmethod
    def hash_senha(senha: str) -> str:
        """Gera hash Argon2 da senha"""
        return ph.hash(senha)
    
    @staticmethod

    def verificar_senha(senha: str, hash_senha: str) -> bool:
        """Verifica se a senha confere com o hash (suporta argon2 e bcrypt)"""
        try:
            if hash_senha.startswith('$argon2'):
                ph.verify(hash_senha, senha)
                return True
            else:
                import bcrypt as _bcrypt
                return _bcrypt.checkpw(senha.encode('utf-8'), hash_senha.encode('utf-8'))
        except VerifyMismatchError:
            return False
        except Exception:
            return False 
    
    # ========================================
    # Métodos JWT
    # ========================================
    
    @staticmethod
    def criar_token(user_id: int, email: str, role: str, condominio_id: int) -> str:
        """
        Cria token JWT com dados do usuário
        
        Payload inclui:
        - sub: ID do usuário
        - email: Email do usuário
        - role: Papel/função
        - condominio_id: ID do condomínio
        - exp: Expiração
        - iat: Data de criação
        """
        now = datetime.utcnow()
        payload = {
            "sub": str(user_id),
            "email": email,
            "role": role,
            "condominio_id": condominio_id,
            "exp": now + timedelta(hours=JWT_EXPIRATION_HOURS),
            "iat": now,
            "type": "access"
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def verificar_token(token: str) -> Optional[dict]:
        """
        Verifica e decodifica token JWT
        
        Retorna:
        - dict com payload se válido
        - None se inválido ou expirado
        """
        try:
            payload = jwt.decode(
                token, 
                JWT_SECRET_KEY, 
                algorithms=[JWT_ALGORITHM]
            )
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
    
    # ========================================
    # Métodos de Autenticação
    # ========================================
    
    def autenticar(self, identificador: str, senha: str, cursor) -> Tuple[bool, Optional[dict], str]:
        """
        Autentica usuário por email OU telefone
        
        Args:
            identificador: Email ou telefone
            senha: Senha do usuário
            cursor: Cursor do banco de dados
        
        Returns:
            Tuple (sucesso, dados_usuario, mensagem_erro)
        """
        # Determina se é email ou telefone
        if self.is_email(identificador):
            # Busca por email
            query = """
                SELECT 
                    o.id, o.email, o.nome, o.telefone, o.senha_hash,
                    o.condominio_id, o.role, o.nivel_id, o.ativo,
                    o.login_falhos, o.bloqueado_ate,
                    c.nome as condominio_nome
                FROM mobile_operadores o
                LEFT JOIN condominios c ON o.condominio_id = c.id
                WHERE LOWER(o.email) = LOWER(%s)
            """
            cursor.execute(query, (identificador,))
        else:
            # Busca por telefone (normalizado)
            telefone_normalizado = self.normalizar_telefone(identificador)
            query = """
                SELECT 
                    o.id, o.email, o.nome, o.telefone, o.senha_hash,
                    o.condominio_id, o.role, o.nivel_id, o.ativo,
                    o.login_falhos, o.bloqueado_ate,
                    c.nome as condominio_nome
                FROM mobile_operadores o
                LEFT JOIN condominios c ON o.condominio_id = c.id
                WHERE o.telefone = %s
            """
            cursor.execute(query, (telefone_normalizado,))
        
        user = cursor.fetchone()
        
        if not user:
            return False, None, "Usuário não encontrado"
        
        # Verifica se está bloqueado
        if user['bloqueado_ate']:
            if datetime.now() < user['bloqueado_ate']:
                return False, None, "Conta bloqueada temporariamente. Tente novamente mais tarde."
        
        # Verifica se está ativo
        if not user['ativo']:
            return False, None, "Conta desativada. Entre em contato com o administrador."
        
        # Verifica senha
        if not self.verificar_senha(senha, user['senha_hash']):
            # Incrementa contador de falhas
            self._registrar_falha_login(user['id'], cursor)
            return False, None, "Senha incorreta"
        
        # Login bem sucedido - limpa falhas e atualiza último login
        self._registrar_sucesso_login(user['id'], cursor)
        
        # Prepara dados do usuário (sem senha_hash)
        user_data = {
            'id': user['id'],
            'email': user['email'],
            'nome': user['nome'],
            'telefone': user['telefone'],
            'condominio_id': user['condominio_id'],
            'condominio_nome': user['condominio_nome'],
            'role': user['role'],
            'nivel_id': user['nivel_id'],
            'ativo': user['ativo']
        }
        
        return True, user_data, ""
    
    def _registrar_falha_login(self, user_id: int, cursor):
        """Registra tentativa de login falha"""
        # Incrementa contador
        cursor.execute("""
            UPDATE mobile_operadores 
            SET login_falhos = COALESCE(login_falhos, 0) + 1
            WHERE id = %s
        """, (user_id,))
        
        # Verifica se deve bloquear (5 tentativas)
        cursor.execute("""
            SELECT login_falhos FROM mobile_operadores WHERE id = %s
        """, (user_id,))
        result = cursor.fetchone()
        
        if result and result['login_falhos'] >= 5:
            # Bloqueia por 15 minutos
            cursor.execute("""
                UPDATE mobile_operadores 
                SET bloqueado_ate = %s
                WHERE id = %s
            """, (datetime.now() + timedelta(minutes=15), user_id))
    
    def _registrar_sucesso_login(self, user_id: int, cursor):
        """Registra login bem sucedido"""
        cursor.execute("""
            UPDATE mobile_operadores 
            SET login_falhos = 0, 
                bloqueado_ate = NULL,
                ultimo_login = %s
            WHERE id = %s
        """, (datetime.now(), user_id))
    
    # ========================================
    # Métodos de Busca de Usuário
    # ========================================
    
    def buscar_por_id(self, user_id: int, cursor) -> Optional[dict]:
        """Busca usuário por ID"""
        query = """
            SELECT 
                o.id, o.email, o.nome, o.telefone,
                o.condominio_id, o.role, o.nivel_id, o.ativo,
                c.nome as condominio_nome
            FROM mobile_operadores o
            LEFT JOIN condominios c ON o.condominio_id = c.id
            WHERE o.id = %s
        """
        cursor.execute(query, (user_id,))
        return cursor.fetchone()
    
    def buscar_por_email(self, email: str, cursor) -> Optional[dict]:
        """Busca usuário por email"""
        query = """
            SELECT 
                o.id, o.email, o.nome, o.telefone,
                o.condominio_id, o.role, o.nivel_id, o.ativo,
                c.nome as condominio_nome
            FROM mobile_operadores o
            LEFT JOIN condominios c ON o.condominio_id = c.id
            WHERE LOWER(o.email) = LOWER(%s)
        """
        cursor.execute(query, (email,))
        return cursor.fetchone()
    
    def buscar_por_telefone(self, telefone: str, cursor) -> Optional[dict]:
        """Busca usuário por telefone"""
        telefone_normalizado = self.normalizar_telefone(telefone)
        query = """
            SELECT 
                o.id, o.email, o.nome, o.telefone,
                o.condominio_id, o.role, o.nivel_id, o.ativo,
                c.nome as condominio_nome
            FROM mobile_operadores o
            LEFT JOIN condominios c ON o.condominio_id = c.id
            WHERE o.telefone = %s
        """
        cursor.execute(query, (telefone_normalizado,))
        return cursor.fetchone()


# Instância global do serviço
auth_service = AuthService()
