#!/usr/bin/env python3
"""
================================================================================
ARQUIVO: auth_cli.py
PASTA:   /home/visionlpr/encomenda_v2/backend/financeiro/
CAMINHO: /home/visionlpr/encomenda_v2/backend/financeiro/auth_cli.py
================================================================================
CLI para gerenciamento de usuários do módulo financeiro
Uso: python -m financeiro.auth_cli [comando] [opções]
================================================================================
"""

import sys
import os
import getpass
import argparse

# Adicionar path do backend
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from financeiro.auth_service import (
    create_user,
    get_user_by_email,
    hash_password,
    verify_password,
    revoke_all_user_tokens,
    cleanup_expired_tokens,
    cleanup_old_attempts
)
from financeiro.auth_modelos import UsuarioFinanceiro


def get_db():
    """Obter sessão do banco"""
    db = SessionLocal()
    try:
        return db
    except:
        db.close()
        raise


def cmd_criar_usuario(args):
    """Criar novo usuário"""
    db = get_db()
    
    # Verificar se email já existe
    existing = get_user_by_email(db, args.email)
    if existing:
        print(f"❌ Email '{args.email}' já cadastrado!")
        db.close()
        return 1
    
    # Solicitar senha se não fornecida
    if args.senha:
        senha = args.senha
    else:
        senha = getpass.getpass("Senha: ")
        senha2 = getpass.getpass("Confirmar senha: ")
        if senha != senha2:
            print("❌ Senhas não conferem!")
            db.close()
            return 1
    
    if len(senha) < 8:
        print("❌ Senha deve ter no mínimo 8 caracteres!")
        db.close()
        return 1
    
    # Criar usuário
    user = create_user(
        db=db,
        email=args.email,
        password=senha,
        nome=args.nome,
        tipo=args.tipo
    )
    
    print(f"✅ Usuário criado com sucesso!")
    print(f"   ID: {user.id}")
    print(f"   Email: {user.email}")
    print(f"   Nome: {user.nome}")
    print(f"   Tipo: {user.tipo}")
    
    db.close()
    return 0


def cmd_listar_usuarios(args):
    """Listar todos os usuários"""
    db = get_db()
    
    users = db.query(UsuarioFinanceiro).all()
    
    if not users:
        print("Nenhum usuário cadastrado.")
        db.close()
        return 0
    
    print(f"\n{'ID':<5} {'Email':<30} {'Nome':<20} {'Tipo':<12} {'Ativo':<6} {'Último Login'}")
    print("-" * 100)
    
    for u in users:
        ultimo = u.ultimo_login.strftime("%d/%m/%Y %H:%M") if u.ultimo_login else "-"
        ativo = "✓" if u.ativo else "✗"
        print(f"{u.id:<5} {u.email:<30} {u.nome:<20} {u.tipo:<12} {ativo:<6} {ultimo}")
    
    print(f"\nTotal: {len(users)} usuário(s)")
    db.close()
    return 0


def cmd_alterar_senha(args):
    """Alterar senha de um usuário"""
    db = get_db()
    
    user = get_user_by_email(db, args.email)
    if not user:
        print(f"❌ Usuário '{args.email}' não encontrado!")
        db.close()
        return 1
    
    # Solicitar nova senha
    if args.senha:
        nova_senha = args.senha
    else:
        nova_senha = getpass.getpass("Nova senha: ")
        nova_senha2 = getpass.getpass("Confirmar nova senha: ")
        if nova_senha != nova_senha2:
            print("❌ Senhas não conferem!")
            db.close()
            return 1
    
    if len(nova_senha) < 8:
        print("❌ Senha deve ter no mínimo 8 caracteres!")
        db.close()
        return 1
    
    # Atualizar senha
    user.senha_hash = hash_password(nova_senha)
    db.commit()
    
    # Revogar todos os tokens
    revoke_all_user_tokens(db, user.id)
    
    print(f"✅ Senha alterada para '{args.email}'")
    print("   Todas as sessões foram encerradas.")
    
    db.close()
    return 0


def cmd_ativar_usuario(args):
    """Ativar um usuário"""
    db = get_db()
    
    user = get_user_by_email(db, args.email)
    if not user:
        print(f"❌ Usuário '{args.email}' não encontrado!")
        db.close()
        return 1
    
    user.ativo = True
    user.bloqueado_ate = None
    user.login_falhos = 0
    db.commit()
    
    print(f"✅ Usuário '{args.email}' ativado")
    db.close()
    return 0


def cmd_desativar_usuario(args):
    """Desativar um usuário"""
    db = get_db()
    
    user = get_user_by_email(db, args.email)
    if not user:
        print(f"❌ Usuário '{args.email}' não encontrado!")
        db.close()
        return 1
    
    user.ativo = False
    db.commit()
    
    # Revogar todos os tokens
    revoke_all_user_tokens(db, user.id)
    
    print(f"✅ Usuário '{args.email}' desativado")
    print("   Todas as sessões foram encerradas.")
    
    db.close()
    return 0


def cmd_alterar_tipo(args):
    """Alterar tipo de um usuário"""
    db = get_db()
    
    user = get_user_by_email(db, args.email)
    if not user:
        print(f"❌ Usuário '{args.email}' não encontrado!")
        db.close()
        return 1
    
    if args.tipo not in ['admin', 'operador', 'visualizador']:
        print("❌ Tipo inválido! Use: admin, operador ou visualizador")
        db.close()
        return 1
    
    tipo_antigo = user.tipo
    user.tipo = args.tipo
    db.commit()
    
    print(f"✅ Tipo alterado: {tipo_antigo} → {args.tipo}")
    db.close()
    return 0


def cmd_cleanup(args):
    """Executar limpeza de tokens e tentativas antigas"""
    db = get_db()
    
    print("🧹 Executando limpeza...")
    
    tokens_removidos = cleanup_expired_tokens(db)
    tentativas_removidas = cleanup_old_attempts(db)
    
    print(f"   Tokens removidos: {tokens_removidos}")
    print(f"   Tentativas removidas: {tentativas_removidas}")
    print("✅ Limpeza concluída!")
    
    db.close()
    return 0


def cmd_revogar_sessoes(args):
    """Revogar todas as sessões de um usuário"""
    db = get_db()
    
    user = get_user_by_email(db, args.email)
    if not user:
        print(f"❌ Usuário '{args.email}' não encontrado!")
        db.close()
        return 1
    
    count = revoke_all_user_tokens(db, user.id)
    
    print(f"✅ {count} sessão(ões) revogada(s) para '{args.email}'")
    db.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="CLI para gerenciamento de usuários do módulo financeiro"
    )
    subparsers = parser.add_subparsers(dest="comando", help="Comandos disponíveis")
    
    # Comando: criar
    p_criar = subparsers.add_parser("criar", help="Criar novo usuário")
    p_criar.add_argument("email", help="Email do usuário")
    p_criar.add_argument("nome", help="Nome do usuário")
    p_criar.add_argument("--tipo", default="operador", 
                        choices=["admin", "operador", "visualizador"],
                        help="Tipo do usuário (default: operador)")
    p_criar.add_argument("--senha", help="Senha (se não fornecida, será solicitada)")
    p_criar.set_defaults(func=cmd_criar_usuario)
    
    # Comando: listar
    p_listar = subparsers.add_parser("listar", help="Listar usuários")
    p_listar.set_defaults(func=cmd_listar_usuarios)
    
    # Comando: senha
    p_senha = subparsers.add_parser("senha", help="Alterar senha de usuário")
    p_senha.add_argument("email", help="Email do usuário")
    p_senha.add_argument("--senha", help="Nova senha (se não fornecida, será solicitada)")
    p_senha.set_defaults(func=cmd_alterar_senha)
    
    # Comando: ativar
    p_ativar = subparsers.add_parser("ativar", help="Ativar usuário")
    p_ativar.add_argument("email", help="Email do usuário")
    p_ativar.set_defaults(func=cmd_ativar_usuario)
    
    # Comando: desativar
    p_desativar = subparsers.add_parser("desativar", help="Desativar usuário")
    p_desativar.add_argument("email", help="Email do usuário")
    p_desativar.set_defaults(func=cmd_desativar_usuario)
    
    # Comando: tipo
    p_tipo = subparsers.add_parser("tipo", help="Alterar tipo de usuário")
    p_tipo.add_argument("email", help="Email do usuário")
    p_tipo.add_argument("tipo", choices=["admin", "operador", "visualizador"],
                       help="Novo tipo")
    p_tipo.set_defaults(func=cmd_alterar_tipo)
    
    # Comando: revogar
    p_revogar = subparsers.add_parser("revogar", help="Revogar todas as sessões de usuário")
    p_revogar.add_argument("email", help="Email do usuário")
    p_revogar.set_defaults(func=cmd_revogar_sessoes)
    
    # Comando: cleanup
    p_cleanup = subparsers.add_parser("cleanup", help="Limpar tokens e tentativas antigas")
    p_cleanup.set_defaults(func=cmd_cleanup)
    
    # Parse e executa
    args = parser.parse_args()
    
    if not args.comando:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
