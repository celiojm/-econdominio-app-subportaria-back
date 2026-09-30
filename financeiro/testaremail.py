#!/usr/bin/env python3
"""
Teste de Email - Lê configurações do .env do backend
Versão: 2.0
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
import sys

# ============================================================================
# CARREGAR .ENV
# ============================================================================

ENV_FILE = "/home/visionlpr/backendV2/.env"

def load_env_file(env_file):
    """Carrega variáveis do arquivo .env"""
    env_vars = {}
    try:
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key] = value
                    os.environ[key] = value
        return env_vars
    except FileNotFoundError:
        print(f"❌ Arquivo .env não encontrado: {env_file}")
        return {}

print("\n" + "="*80)
print(" TESTE DE EMAIL - Lendo do .env")
print("="*80)

# Carregar .env
print(f"\n1️⃣  Carregando .env: {ENV_FILE}")
env_vars = load_env_file(ENV_FILE)

if not env_vars:
    print("❌ Erro ao carregar .env!")
    sys.exit(1)

print(f"✅ {len(env_vars)} variáveis carregadas")

# ============================================================================
# CONFIGURAÇÕES DE EMAIL
# ============================================================================

print("\n2️⃣  Obtendo configurações de email...")

SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
USERNAME = os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br")
PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br")
FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Financeiro Econdominio")

print(f"\n📧 Configurações:")
print(f"   Servidor: {SMTP_SERVER}:{SMTP_PORT}")
print(f"   Usuário: {USERNAME}")
print(f"   De: {FROM_EMAIL}")
print(f"   Senha: {'*' * len(PASSWORD) if PASSWORD else '❌ NÃO CONFIGURADA'}")

if not PASSWORD:
    print("\n❌ ERRO: EMAIL_SMTP_PASSWORD não está configurado no .env!")
    print(f"\nVerifique o arquivo: {ENV_FILE}")
    print("Procure pela linha: EMAIL_SMTP_PASSWORD=...")
    sys.exit(1)

print(f"\n✅ Senha encontrada: {len(PASSWORD)} caracteres")

# Mostrar senha (para debug)
print(f"   Senha (primeiros 10 chars): {PASSWORD[:10]}...")
print(f"   Senha (últimos 5 chars): ...{PASSWORD[-5:]}")

# ============================================================================
# EMAIL DE TESTE
# ============================================================================

TO_EMAIL = "celiojm@gmail.com"

# Permitir trocar email via argumento
if len(sys.argv) > 1:
    TO_EMAIL = sys.argv[1]

print(f"\n3️⃣  Preparando email de teste...")
print(f"   Para: {TO_EMAIL}")

# ============================================================================
# ENVIAR EMAIL
# ============================================================================

def testar_email():
    """Testa envio de email"""
    try:
        print(f"\n4️⃣  Criando mensagem...")
        
        # Criar mensagem
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🧪 Teste de Email - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = TO_EMAIL
        
        # Corpo HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; padding: 20px; }}
                .success {{ background: #d4edda; border: 1px solid #c3e6cb; padding: 20px; border-radius: 10px; }}
                .info {{ background: #d1ecf1; border: 1px solid #bee5eb; padding: 15px; margin-top: 20px; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="success">
                <h1 style="color: #155724; margin-top: 0;">✅ Teste de Email Bem-sucedido!</h1>
                <p><strong>Parabéns!</strong> A configuração SMTP está funcionando corretamente.</p>
            </div>
            <div class="info">
                <h3 style="color: #0c5460; margin-top: 0;">📋 Informações do Teste</h3>
                <ul>
                    <li><strong>Data/Hora:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</li>
                    <li><strong>Servidor SMTP:</strong> {SMTP_SERVER}:{SMTP_PORT}</li>
                    <li><strong>Remetente:</strong> {FROM_EMAIL}</li>
                    <li><strong>Destinatário:</strong> {TO_EMAIL}</li>
                    <li><strong>Arquivo .env:</strong> {ENV_FILE}</li>
                </ul>
            </div>
            <p style="color: #6c757d; font-size: 12px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #dee2e6;">
                Este é um email de teste automático do sistema Econdomínio Financeiro.
            </p>
        </body>
        </html>
        """
        
        # Corpo texto
        texto = f"""
TESTE DE EMAIL BEM-SUCEDIDO!

A configuração SMTP está funcionando corretamente.

Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
Servidor: {SMTP_SERVER}:{SMTP_PORT}
De: {FROM_EMAIL}
Para: {TO_EMAIL}
Arquivo .env: {ENV_FILE}

---
Email de teste automático - Econdomínio Financeiro
        """
        
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        print(f"✅ Mensagem criada")
        
        print(f"\n5️⃣  Conectando ao servidor SMTP...")
        print(f"   Servidor: {SMTP_SERVER}")
        print(f"   Porta: {SMTP_PORT}")
        
        # Conectar e enviar
        context = ssl.create_default_context()
        
        with smtplib.SMTP_SSL(
            SMTP_SERVER,
            SMTP_PORT,
            context=context,
            timeout=30
        ) as server:
            print(f"✅ Conectado ao servidor")
            
            print(f"\n6️⃣  Fazendo login...")
            print(f"   Usuário: {USERNAME}")
            print(f"   Senha: {'*' * len(PASSWORD)}")
            
            server.login(USERNAME, PASSWORD)
            print(f"✅ Login bem-sucedido")
            
            print(f"\n7️⃣  Enviando email...")
            server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
            print(f"✅ Email enviado!")
        
        print(f"\n" + "="*80)
        print(f" ✅ TESTE CONCLUÍDO COM SUCESSO!")
        print(f"="*80)
        print(f"\n📧 Verifique a caixa de entrada de: {TO_EMAIL}")
        print(f"   (Pode levar alguns segundos para chegar)\n")
        
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print(f"\n" + "="*80)
        print(f" ❌ ERRO DE AUTENTICAÇÃO!")
        print(f"="*80)
        print(f"\nDetalhes: {e}")
        print(f"\n💡 Verificações:")
        print(f"   1. Usuário: {USERNAME}")
        print(f"   2. Senha no .env: {PASSWORD}")
        print(f"   3. Arquivo: {ENV_FILE}")
        print(f"\n🔍 Comandos para verificar:")
        print(f'   grep "EMAIL_SMTP" {ENV_FILE}')
        return False
        
    except smtplib.SMTPException as e:
        print(f"\n" + "="*80)
        print(f" ❌ ERRO SMTP!")
        print(f"="*80)
        print(f"\nDetalhes: {e}")
        return False
        
    except Exception as e:
        print(f"\n" + "="*80)
        print(f" ❌ ERRO INESPERADO!")
        print(f"="*80)
        print(f"\nDetalhes: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    sucesso = testar_email()
    
    if not sucesso:
        print("\n" + "="*80)
        print(" 🔧 PRÓXIMOS PASSOS")
        print("="*80)
        print("\n1. Verifique a senha no .env:")
        print(f"   grep EMAIL_SMTP_PASSWORD {ENV_FILE}")
        print("\n2. Compare com o script que funciona:")
        print("   python3 /home/visionlpr/backendV2/monitor/relatorio_zapi.py")
        print("\n3. Verifique se o backend foi atualizado:")
        print("   grep -A 5 'def get_email_config' /home/visionlpr/backendV2/financeiro/financeiro_email.py")
        print("\n4. Reinicie o backend:")
        print("   sudo systemctl restart backend-encomenda.service")
        print("")
    
    sys.exit(0 if sucesso else 1)
