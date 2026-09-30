import os  # 2026-09-30: segredos vêm do ambiente
#!/usr/bin/env python3
"""
================================================================================
SCRIPT: testar_email_smtp.py
DESCRIÇÃO: Testa conexão e envio de email via SMTP
LOCALIZAÇÃO: ~/backendV2/financeiro/
USO: python3 testar_email_smtp.py seu-email@teste.com
================================================================================
"""

import sys
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Configurações (mesmas do .env)
SMTP_HOST = "mail.econdominio.com.br"
SMTP_PORT = 465
SMTP_USER = "financeiro@econdominio.com.br"
SMTP_PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD", "")
SMTP_FROM = "financeiro@econdominio.com.br"

def testar_conexao():
    """Testa apenas a conexão e autenticação"""
    print("=" * 80)
    print("🧪 TESTE 1: Conexão e Autenticação SMTP")
    print("=" * 80)
    print(f"📡 Servidor: {SMTP_HOST}:{SMTP_PORT}")
    print(f"👤 Usuário: {SMTP_USER}")
    print(f"🔒 Método: SSL")
    print()
    
    try:
        print("🔌 Conectando ao servidor SMTP...")
        context = ssl.create_default_context()
        
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
            print("✅ Conexão estabelecida!")
            
            print("🔐 Tentando autenticar...")
            server.login(SMTP_USER, SMTP_PASSWORD)
            print("✅ Autenticação bem-sucedida!")
            
            print("📧 Verificando capacidades do servidor...")
            print(server.ehlo_resp.decode() if hasattr(server, 'ehlo_resp') else "N/A")
            
        print()
        print("=" * 80)
        print("✅ TESTE 1: PASSOU - Conexão e autenticação OK!")
        print("=" * 80)
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print()
        print("=" * 80)
        print("❌ TESTE 1: FALHOU - Erro de autenticação")
        print("=" * 80)
        print(f"Erro: {e}")
        print()
        print("Possíveis causas:")
        print("  1. Senha incorreta")
        print("  2. Usuário incorreto")
        print("  3. Conta de email bloqueada")
        return False
        
    except smtplib.SMTPException as e:
        print()
        print("=" * 80)
        print("❌ TESTE 1: FALHOU - Erro SMTP")
        print("=" * 80)
        print(f"Erro: {e}")
        return False
        
    except Exception as e:
        print()
        print("=" * 80)
        print("❌ TESTE 1: FALHOU - Erro inesperado")
        print("=" * 80)
        print(f"Erro: {e}")
        return False

def testar_envio_email(destinatario):
    """Testa envio de email completo"""
    print()
    print("=" * 80)
    print("🧪 TESTE 2: Envio de Email Completo")
    print("=" * 80)
    print(f"📧 Destinatário: {destinatario}")
    print()
    
    try:
        # Criar mensagem
        msg = MIMEMultipart('alternative')
        msg['From'] = SMTP_FROM
        msg['To'] = destinatario
        msg['Subject'] = "🧪 Teste - Sistema Financeiro INFORSEG"
        
        # Corpo HTML
        corpo_html = f"""
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4;">
            <div style="max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <div style="background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); padding: 20px; border-radius: 8px; text-align: center; margin-bottom: 20px;">
                    <h1 style="color: white; margin: 0;">🧪 Email de Teste</h1>
                    <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0;">Sistema Financeiro INFORSEG</p>
                </div>
                
                <div style="padding: 20px;">
                    <h2 style="color: #1e3a8a;">✅ Sistema de Email Funcionando!</h2>
                    <p>Se você está vendo este email, significa que o sistema de envio está configurado corretamente.</p>
                    
                    <div style="background: #f0f9ff; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #3b82f6;">
                        <p style="margin: 0;"><strong>📡 Servidor:</strong> {SMTP_HOST}</p>
                        <p style="margin: 5px 0 0 0;"><strong>📧 De:</strong> {SMTP_FROM}</p>
                        <p style="margin: 5px 0 0 0;"><strong>🕐 Data/Hora:</strong> {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}</p>
                    </div>
                    
                    <p style="color: #059669; font-weight: bold;">✓ Configurações corretas!</p>
                    <p style="color: #059669; font-weight: bold;">✓ SMTP conectado!</p>
                    <p style="color: #059669; font-weight: bold;">✓ Email enviado com sucesso!</p>
                </div>
                
                <div style="background: #f8fafc; padding: 15px; border-radius: 8px; margin-top: 20px; text-align: center;">
                    <p style="color: #64748b; font-size: 12px; margin: 0;">
                        Este é um email automático de teste<br>
                        © 2025 INFORSEG - Todos os direitos reservados
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))
        
        print("📝 Mensagem criada")
        print("🔌 Conectando ao servidor SMTP...")
        
        # Enviar
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
            print("✅ Conectado!")
            print("🔐 Autenticando...")
            server.login(SMTP_USER, SMTP_PASSWORD)
            print("✅ Autenticado!")
            print("📤 Enviando email...")
            server.send_message(msg)
            print("✅ Email enviado!")
        
        print()
        print("=" * 80)
        print("✅ TESTE 2: PASSOU - Email enviado com sucesso!")
        print("=" * 80)
        print()
        print(f"📬 Verifique a caixa de entrada de: {destinatario}")
        print("   (Pode levar alguns segundos para chegar)")
        print("   (Verifique também a pasta de SPAM)")
        return True
        
    except Exception as e:
        print()
        print("=" * 80)
        print("❌ TESTE 2: FALHOU - Erro ao enviar email")
        print("=" * 80)
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 20 + "TESTE DE EMAIL SMTP - INFORSEG" + " " * 28 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    
    if len(sys.argv) < 2:
        print("❌ Erro: Informe o email de destino")
        print()
        print("Uso:")
        print(f"  python3 {sys.argv[0]} seu-email@exemplo.com")
        print()
        print("Exemplo:")
        print(f"  python3 {sys.argv[0]} teste@gmail.com")
        sys.exit(1)
    
    destinatario = sys.argv[1]
    
    # Validar email básico
    if '@' not in destinatario or '.' not in destinatario.split('@')[1]:
        print(f"❌ Erro: Email inválido: {destinatario}")
        sys.exit(1)
    
    # Executar testes
    print(f"🎯 Email de destino: {destinatario}")
    print()
    
    teste1 = testar_conexao()
    
    if teste1:
        teste2 = testar_envio_email(destinatario)
        
        print()
        print("=" * 80)
        print("📊 RESUMO DOS TESTES")
        print("=" * 80)
        print(f"Teste 1 - Conexão/Auth:  {'✅ PASSOU' if teste1 else '❌ FALHOU'}")
        print(f"Teste 2 - Envio Email:   {'✅ PASSOU' if teste2 else '❌ FALHOU'}")
        print("=" * 80)
        
        if teste1 and teste2:
            print()
            print("🎉 TODOS OS TESTES PASSARAM!")
            print("✅ O sistema de email está funcionando corretamente")
            print()
            print("💡 Se o botão no frontend ainda não funciona:")
            print("   1. Verifique se a rota está registrada no main.py")
            print("   2. Verifique os logs do backend")
            print("   3. Verifique o console do navegador (F12)")
            sys.exit(0)
        else:
            print()
            print("⚠️ ALGUNS TESTES FALHARAM")
            print("Corrija os erros acima antes de continuar")
            sys.exit(1)
    else:
        print()
        print("❌ Teste de conexão falhou. Não é possível prosseguir.")
        print()
        print("💡 Verifique:")
        print("   1. Credenciais no .env")
        print("   2. Firewall/porta 465")
        print("   3. Conexão com internet")
        sys.exit(1)

if __name__ == "__main__":
    main()
