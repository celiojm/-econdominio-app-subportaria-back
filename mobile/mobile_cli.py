#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
ARQUIVO: mobile_cli.py
PASTA:   ~/backend/mobile/
CAMINHO: /home/visionlpr/backend/mobile/mobile_cli.py
================================================================================
CLI para gerenciar operadores mobile
Uso: python3 mobile_cli.py [comando]
================================================================================
"""

import sys
import os
from getpass import getpass

# Adicionar path do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from mobile import auth_service
from mobile.auth_models import RoleEnum


def criar_operador():
    """Cria um novo operador interativamente"""
    print("\n" + "="*80)
    print(" 👤 CRIAR NOVO OPERADOR")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        # Coletar dados
        email = input("\n📧 Email: ").strip()
        if not email or '@' not in email:
            print("❌ Email inválido")
            return
        
        # Verificar se já existe
        existing = auth_service.get_operador_by_identifier(db, email)
        if existing:
            print(f"❌ Operador já existe (ID: {existing.id})")
            return
        
        nome = input("👤 Nome: ").strip()
        if not nome:
            print("❌ Nome obrigatório")
            return
        
        telefone = input("📱 Telefone/WhatsApp (opcional): ").strip()
        
        print("\n🔐 Roles disponíveis:")
        print("  1. admin_sistema - Acesso total")
        print("  2. sindico - Gerencia seu condomínio")
        print("  3. operador - Apenas operação")
        
        role_choice = input("Escolha (1-3): ").strip()
        role_map = {"1": "admin_sistema", "2": "sindico", "3": "operador"}
        role = role_map.get(role_choice, "operador")
        
        # Condomínio
        if role == "admin_sistema":
            condominio_id = 1
            print(f"✅ Condomínio definido automaticamente: 1 (Sistema)")
        else:
            cond_input = input(f"🏢 ID do Condomínio: ").strip()
            try:
                condominio_id = int(cond_input)
            except:
                print("❌ ID de condomínio inválido")
                return
        
        # Senha
        senha = getpass("🔑 Senha (mín 8 caracteres): ")
        if len(senha) < 8:
            print("❌ Senha muito curta (mínimo 8 caracteres)")
            return
        
        senha_confirm = getpass("🔑 Confirmar senha: ")
        if senha != senha_confirm:
            print("❌ Senhas não conferem")
            return
        
        # Criar
        print("\n⏳ Criando operador...")
        operador = auth_service.create_operador(
            db=db,
            email=email,
            password=senha,
            nome=nome,
            condominio_id=condominio_id,
            role=role,
            telefone=telefone if telefone else None
        )
        
        print("\n✅ Operador criado com sucesso!")
        print(f"   ID: {operador.id}")
        print(f"   Email: {operador.email}")
        print(f"   Nome: {operador.nome}")
        print(f"   Role: {operador.role}")
        print(f"   Telefone: {operador.telefone or 'N/A'}")
        print(f"   Condomínio: {operador.condominio_id}")
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def listar_operadores():
    """Lista todos os operadores"""
    print("\n" + "="*80)
    print(" 📋 LISTA DE OPERADORES")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        from mobile.auth_models import MobileOperador
        
        operadores = db.query(MobileOperador).all()
        
        if not operadores:
            print("\n⚠️  Nenhum operador cadastrado")
            return
        
        print(f"\nTotal: {len(operadores)} operadores\n")
        
        for op in operadores:
            role = op.role.value if isinstance(op.role, RoleEnum) else op.role
            status = "✅ ATIVO" if op.ativo else "❌ INATIVO"
            print(f"ID: {op.id:3d} | {status} | {role:15s} | Cond: {op.condominio_id:3d} | {op.email:30s} | {op.nome}")
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
    finally:
        db.close()


def alterar_senha():
    """Altera senha de um operador"""
    print("\n" + "="*80)
    print(" 🔑 ALTERAR SENHA")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        identifier = input("\n📧 Email ou Telefone do operador: ").strip()
        
        operador = auth_service.get_operador_by_identifier(db, identifier)
        if not operador:
            print("❌ Operador não encontrado")
            return
        
        print(f"\n👤 Operador encontrado: {operador.nome} ({operador.email})")
        
        nova_senha = getpass("🔑 Nova senha (mín 8 caracteres): ")
        if len(nova_senha) < 8:
            print("❌ Senha muito curta")
            return
        
        senha_confirm = getpass("🔑 Confirmar nova senha: ")
        if nova_senha != senha_confirm:
            print("❌ Senhas não conferem")
            return
        
        # Atualizar
        auth_service.update_operador_password(db, operador, nova_senha)
        
        print("\n✅ Senha alterada com sucesso!")
        print("⚠️  Todas as sessões do operador foram revogadas")
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
    finally:
        db.close()


def desativar_operador():
    """Desativa um operador"""
    print("\n" + "="*80)
    print(" 🚫 DESATIVAR OPERADOR")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        identifier = input("\n📧 Email ou Telefone do operador: ").strip()
        
        operador = auth_service.get_operador_by_identifier(db, identifier)
        if not operador:
            print("❌ Operador não encontrado")
            return
        
        print(f"\n👤 Operador: {operador.nome} ({operador.email})")
        print(f"   Status atual: {'ATIVO' if operador.ativo else 'INATIVO'}")
        
        if not operador.ativo:
            print("\n⚠️  Operador já está inativo")
            return
        
        confirm = input("\n⚠️  Confirma desativação? (s/N): ").strip().lower()
        if confirm != 's':
            print("❌ Cancelado")
            return
        
        # Desativar
        operador.ativo = False
        db.commit()
        
        # Revogar tokens
        auth_service.revoke_all_user_tokens(db, operador.id)
        
        print("\n✅ Operador desativado com sucesso!")
        print("⚠️  Todas as sessões foram revogadas")
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
    finally:
        db.close()


def ativar_operador():
    """Ativa um operador"""
    print("\n" + "="*80)
    print(" ✅ ATIVAR OPERADOR")
    print("="*80)
    
    db = SessionLocal()
    
    try:
        identifier = input("\n📧 Email ou Telefone do operador: ").strip()
        
        operador = auth_service.get_operador_by_identifier(db, identifier)
        if not operador:
            print("❌ Operador não encontrado")
            return
        
        print(f"\n👤 Operador: {operador.nome} ({operador.email})")
        print(f"   Status atual: {'ATIVO' if operador.ativo else 'INATIVO'}")
        
        if operador.ativo:
            print("\n⚠️  Operador já está ativo")
            return
        
        # Ativar
        operador.ativo = True
        db.commit()
        
        print("\n✅ Operador ativado com sucesso!")
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
    finally:
        db.close()


def menu():
    """Menu principal"""
    print("\n" + "="*80)
    print(" 🔧 GERENCIADOR DE OPERADORES MOBILE")
    print("="*80)
    print("\nComandos disponíveis:")
    print("  1. criar    - Criar novo operador")
    print("  2. listar   - Listar todos os operadores")
    print("  3. senha    - Alterar senha de operador")
    print("  4. desativar - Desativar operador")
    print("  5. ativar   - Ativar operador")
    print("  0. sair     - Sair")
    
    while True:
        print("\n" + "-"*80)
        choice = input("Escolha uma opção: ").strip()
        
        if choice == "1" or choice.lower() == "criar":
            criar_operador()
        elif choice == "2" or choice.lower() == "listar":
            listar_operadores()
        elif choice == "3" or choice.lower() == "senha":
            alterar_senha()
        elif choice == "4" or choice.lower() == "desativar":
            desativar_operador()
        elif choice == "5" or choice.lower() == "ativar":
            ativar_operador()
        elif choice == "0" or choice.lower() == "sair":
            print("\n👋 Até logo!")
            break
        else:
            print("❌ Opção inválida")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "criar":
            criar_operador()
        elif cmd == "listar":
            listar_operadores()
        elif cmd == "senha":
            alterar_senha()
        elif cmd == "desativar":
            desativar_operador()
        elif cmd == "ativar":
            ativar_operador()
        else:
            print(f"❌ Comando desconhecido: {cmd}")
            print("\nUso: python3 mobile_cli.py [criar|listar|senha|desativar|ativar]")
    else:
        menu()
