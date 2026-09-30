#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de Conexão Z-API - Versão 3.1
Alertas via Email + WhatsApp
NOVA FUNCIONALIDADE: Limpa fila automaticamente quando > 100 mensagens
"""

import os
import sys
import time
import json
import requests
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================

CHECK_INTERVAL = 24000  # 5 minutos
QUEUE_LIMIT = 100  # Limite para LIMPEZA AUTOMÁTICA da fila
QUEUE_ALERT_LIMIT = 20  # Limite para apenas ALERTAR (sem limpar)
ENV_FILE = "/home/visionlpr/backendV2/.env"
STATUS_FILE = "/home/visionlpr/backendV2/venv/zapi_last_status.json"

# Número WhatsApp para alertas
WHATSAPP_ALERT_NUMBER = "5548984046118"

# As configurações Z-API são carregadas do .env
ZAPI_CONFIG = {}

# ============================================================================
# FUNÇÕES
# ============================================================================

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
        print(f"⚠️  Arquivo .env não encontrado: {env_file}")
        return {}


def get_zapi_config():
    """Retorna configurações Z-API do .env"""
    return {
        "instance_id": os.getenv("ZAPI_INSTANCE_ID", ""),
        "token": os.getenv("ZAPI_TOKEN", ""),
        "client_token": os.getenv("ZAPI_CLIENT_TOKEN", ""),
        "api_url": os.getenv("ZAPI_API_URL", "https://api.z-api.io")
    }


def get_email_config():
    """Retorna configurações de email do .env"""
    return {
        "smtp_server": os.getenv("EMAIL_SMTP_SERVER", "mail.econdominio.com.br"),
        "smtp_port": int(os.getenv("EMAIL_SMTP_PORT", "465")),
        "username": os.getenv("EMAIL_SMTP_USERNAME", "financeiro@econdominio.com.br"),
        "password": os.getenv("EMAIL_SMTP_PASSWORD", ""),
        "from_email": os.getenv("EMAIL_FROM_ADDRESS", "financeiro@econdominio.com.br"),
        "from_name": "Financeiro Econdominio",
        "alert_email": os.getenv("ALERT_EMAIL", "celiojm@gmail.com")

    }


def get_zapi_headers():
    """Retorna headers para requisições Z-API"""
    headers = {
        "Content-Type": "application/json"
    }
    
    if ZAPI_CONFIG.get('client_token'):
        headers["Client-Token"] = ZAPI_CONFIG['client_token']
    
    return headers


def send_whatsapp_message(phone, message):
    """Envia mensagem WhatsApp via Z-API"""
    try:
        url = f"{ZAPI_CONFIG['api_url']}/instances/{ZAPI_CONFIG['instance_id']}/token/{ZAPI_CONFIG['token']}/send-text"
        
        headers = get_zapi_headers()
        
        data = {
            "phone": phone,
            "message": message
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            return True, None
        else:
            return False, f"HTTP {response.status_code}: {response.text}"
            
    except Exception as e:
        return False, str(e)


def clear_message_queue():
    """
    LIMPA TODA A FILA DE MENSAGENS
    ⚠️ ATENÇÃO: Esta ação é IRREVERSÍVEL!
    """
    try:
        url = f"{ZAPI_CONFIG['api_url']}/instances/{ZAPI_CONFIG['instance_id']}/token/{ZAPI_CONFIG['token']}/queue"
        
        headers = get_zapi_headers()
        
        response = requests.delete(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            return True, None
        else:
            return False, f"HTTP {response.status_code}: {response.text}"
            
    except Exception as e:
        return False, str(e)


def check_zapi_status():
    """Verifica o status da instância Z-API"""
    try:
        url = f"{ZAPI_CONFIG['api_url']}/instances/{ZAPI_CONFIG['instance_id']}/token/{ZAPI_CONFIG['token']}/status"
        
        headers = get_zapi_headers()
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            connected = data.get('connected', False)
            smartphone_connected = data.get('smartphoneConnected', False)
            status = data.get('status', 'UNKNOWN')
            error_msg = data.get('error', None)
            
            return {
                'connected': connected,
                'smartphone_connected': smartphone_connected,
                'status': status,
                'error': error_msg,
                'full_response': data
            }
        else:
            return {
                'connected': False,
                'smartphone_connected': False,
                'status': 'ERROR',
                'error': f"HTTP {response.status_code}: {response.text}",
                'full_response': None
            }
            
    except requests.exceptions.Timeout:
        return {
            'connected': False,
            'smartphone_connected': False,
            'status': 'TIMEOUT',
            'error': 'Timeout ao conectar com Z-API',
            'full_response': None
        }
    except Exception as e:
        return {
            'connected': False,
            'smartphone_connected': False,
            'status': 'EXCEPTION',
            'error': str(e),
            'full_response': None
        }


def check_message_queue():
    """Verifica a fila de mensagens pendentes"""
    try:
        url = f"{ZAPI_CONFIG['api_url']}/instances/{ZAPI_CONFIG['instance_id']}/token/{ZAPI_CONFIG['token']}/queue"
        
        headers = get_zapi_headers()
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            queue_size = 0
            
            if isinstance(data, dict):
                if 'queue' in data:
                    queue_size = len(data['queue'])
                elif 'count' in data:
                    queue_size = data['count']
                elif 'size' in data:
                    queue_size = data['size']
                elif 'total' in data:
                    queue_size = data['total']
            elif isinstance(data, list):
                queue_size = len(data)
            
            return {
                'queue_size': queue_size,
                'error': None,
                'full_response': data
            }
        else:
            return {
                'queue_size': 0,
                'error': f"HTTP {response.status_code}: {response.text}",
                'full_response': None
            }
            
    except requests.exceptions.Timeout:
        return {
            'queue_size': 0,
            'error': 'Timeout ao verificar fila',
            'full_response': None
        }
    except Exception as e:
        return {
            'queue_size': 0,
            'error': str(e),
            'full_response': None
        }


def send_disconnection_alert(status_info, email_config):
    """Envia alerta de desconexão por EMAIL"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚨 ALERTA: Z-API Desconectado - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_config['alert_email']
        
        error_text = f"\nErro: {status_info['error']}" if status_info.get('error') else ""
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
                .alert-box {{ background: linear-gradient(135deg, #ff4444 0%, #cc0000 100%); color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; text-align: center; }}
                .alert-icon {{ font-size: 48px; margin-bottom: 10px; }}
                .info-box {{ background: #f5f5f5; border-left: 4px solid #ff4444; padding: 15px; margin: 15px 0; border-radius: 5px; }}
                .info-row {{ margin: 10px 0; }}
                .label {{ font-weight: bold; color: #666; }}
                .value {{ color: #333; }}
            </style>
        </head>
        <body>
            <div class="alert-box">
                <div class="alert-icon">🚨</div>
                <h1 style="margin: 0;">Z-API DESCONECTADO</h1>
                <p style="margin: 10px 0 0 0;">Ação imediata necessária!</p>
            </div>
            <div class="info-box">
                <div class="info-row"><span class="label">📅 Data/Hora:</span> <span class="value">{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</span></div>
                <div class="info-row"><span class="label">📱 Instância:</span> <span class="value">Meu numero</span></div>
                <div class="info-row"><span class="label">📊 Status:</span> <span class="value" style="color: #ff4444; font-weight: bold;">{status_info['status']}</span></div>
                {f'<div class="info-row"><span class="label">⚠️  Erro:</span> <span class="value">{status_info["error"]}</span></div>' if status_info.get('error') else ''}
            </div>
        </body>
        </html>
        """
        
        texto = f"🚨 Z-API DESCONECTADO\n\nData: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\nStatus: {status_info['status']}{error_text}"
        
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(email_config["smtp_server"], email_config["smtp_port"], context=context, timeout=30) as server:
            server.login(email_config["username"], email_config["password"])
            server.sendmail(email_config["from_email"], [email_config["alert_email"]], msg.as_string())
        
        return True
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")
        return False


def send_disconnection_alert_whatsapp(status_info):
    """Envia alerta de desconexão por WHATSAPP"""
    error_text = f"\n⚠️ Erro: {status_info['error']}" if status_info.get('error') else ""
    
    message = f"""🚨 *ALERTA: Z-API DESCONECTADO*

📅 Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
📱 Instância: Meu numero
📊 Status: {status_info['status']}{error_text}

🔧 *Ações necessárias:*
1. Acesse app.z-api.io
2. Verifique a conexão
3. Reconecte se necessário

---
Monitor Z-API v3.1"""
    
    success, error = send_whatsapp_message(WHATSAPP_ALERT_NUMBER, message)
    
    if not success:
        print(f"❌ Erro ao enviar WhatsApp: {error}")
    
    return success


def send_queue_alert(queue_info, email_config):
    """Envia alerta de fila por EMAIL"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚠️ ALERTA: Fila Z-API ({queue_info['queue_size']} msgs) - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_config['alert_email']
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; }}
            .alert-box {{ background: linear-gradient(135deg, #ff9800 0%, #f57c00 100%); color: white; padding: 20px; border-radius: 10px; text-align: center; }}
            .queue-number {{ font-size: 48px; font-weight: bold; color: #ff9800; text-align: center; margin: 20px 0; }}
        </style>
        </head>
        <body>
            <div class="alert-box">
                <h1>⚠️ FILA DE MENSAGENS ALTA</h1>
            </div>
            <div class="queue-number">{queue_info['queue_size']} mensagens</div>
            <p>Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
            <p>Limite alerta: {QUEUE_ALERT_LIMIT} | Limite limpeza: {QUEUE_LIMIT}</p>
        </body>
        </html>
        """
        
        texto = f"⚠️ FILA ALTA: {queue_info['queue_size']} mensagens\n\nData: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\nLimite alerta: {QUEUE_ALERT_LIMIT} | Limite limpeza: {QUEUE_LIMIT}"
        
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(email_config["smtp_server"], email_config["smtp_port"], context=context, timeout=30) as server:
            server.login(email_config["username"], email_config["password"])
            server.sendmail(email_config["from_email"], [email_config["alert_email"]], msg.as_string())
        
        return True
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")
        return False


def send_queue_alert_whatsapp(queue_info):
    """Envia alerta de fila por WHATSAPP"""
    message = f"""⚠️ *ALERTA: FILA DE MENSAGENS ALTA*

📊 Fila: *{queue_info['queue_size']} mensagens*
🎯 Limite alerta: {QUEUE_ALERT_LIMIT} msgs
🚨 Limite limpeza: {QUEUE_LIMIT} msgs
📅 Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

🔧 *Possíveis causas:*
• Volume alto de envios
• Processamento lento
• Limitação de taxa

✅ *Ações:*
1. Acesse app.z-api.io
2. Monitore a fila
3. Reduza volume se necessário

---
Monitor Z-API v3.1"""
    
    success, error = send_whatsapp_message(WHATSAPP_ALERT_NUMBER, message)
    
    if not success:
        print(f"❌ Erro ao enviar WhatsApp: {error}")
    
    return success


def send_queue_cleared_alert(queue_size_before, email_config):
    """Envia alerta de que a fila foi LIMPA automaticamente"""
    # Email
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🗑️ AÇÃO AUTOMÁTICA: Fila Z-API Limpa ({queue_size_before} msgs removidas) - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_config['alert_email']
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); color: white; padding: 20px; border-radius: 10px; text-align: center;">
                <h1>🗑️ FILA LIMPA AUTOMATICAMENTE</h1>
                <p style="font-size: 24px; margin: 10px 0;">{queue_size_before} mensagens removidas</p>
            </div>
            <p>Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
            <p><strong>A fila ultrapassou {QUEUE_LIMIT} mensagens e foi limpa automaticamente pelo sistema.</strong></p>
            <p>⚠️ Todas as mensagens pendentes foram descartadas.</p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(f"🗑️ FILA LIMPA: {queue_size_before} msgs removidas\n\nData: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(email_config["smtp_server"], email_config["smtp_port"], context=context, timeout=30) as server:
            server.login(email_config["username"], email_config["password"])
            server.sendmail(email_config["from_email"], [email_config["alert_email"]], msg.as_string())
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")
    
    # WhatsApp
    message = f"""🗑️ *AÇÃO AUTOMÁTICA: FILA LIMPA*

⚠️ A fila ultrapassou {QUEUE_LIMIT} mensagens!

📊 Mensagens removidas: *{queue_size_before}*
📅 Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

✅ *Ação executada:*
• Todas as mensagens pendentes foram descartadas
• Fila agora está vazia
• Sistema voltou ao normal

🔍 *Recomendação:*
• Investigue o motivo do acúmulo
• Verifique se há problema no envio
• Ajuste volume de mensagens se necessário

---
Monitor Z-API v3.1"""
    
    send_whatsapp_message(WHATSAPP_ALERT_NUMBER, message)


def send_smartphone_disconnected_alert(status_info, email_config):
    """Envia alerta de celular desconectado"""
    # Email
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📱 ALERTA: Celular Desconectado - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        msg["From"] = f"{email_config['from_name']} <{email_config['from_email']}>"
        msg["To"] = email_config['alert_email']
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #ff9800 0%, #f57c00 100%); color: white; padding: 20px; border-radius: 10px; text-align: center;">
                <h1>📱 CELULAR DESCONECTADO</h1>
                <p>O celular perdeu conexão com a internet!</p>
            </div>
            <p>Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
            <p>Verifique se o celular está conectado à internet.</p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(f"📱 CELULAR DESCONECTADO\n\nData: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(email_config["smtp_server"], email_config["smtp_port"], context=context, timeout=30) as server:
            server.login(email_config["username"], email_config["password"])
            server.sendmail(email_config["from_email"], [email_config["alert_email"]], msg.as_string())
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")
    
    # WhatsApp
    message = f"""📱 *ALERTA: CELULAR DESCONECTADO*

⚠️ O celular perdeu conexão com a internet!

📅 Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

🔧 *Ações:*
• Verifique a conexão de internet do celular
• Verifique se o WhatsApp está aberto
• Reconecte se necessário

---
Monitor Z-API v3.1"""
    
    send_whatsapp_message(WHATSAPP_ALERT_NUMBER, message)


def save_status(status_info, queue_info):
    """Salva o status atual em arquivo JSON"""
    try:
        status_data = {
            'timestamp': datetime.now().isoformat(),
            'connected': status_info['connected'],
            'smartphone_connected': status_info.get('smartphone_connected', False),
            'status': status_info['status'],
            'error': status_info['error'],
            'queue_size': queue_info['queue_size'],
            'queue_error': queue_info['error']
        }
        
        with open(STATUS_FILE, 'w') as f:
            json.dump(status_data, f, indent=2)
            
    except Exception as e:
        print(f"⚠️  Erro ao salvar status: {e}")


def load_last_status():
    """Carrega o último status salvo"""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r') as f:
                return json.load(f)
        return None
    except Exception as e:
        print(f"⚠️  Erro ao carregar último status: {e}")
        return None


def print_header():
    """Imprime cabeçalho do programa"""
    print("\n" + "="*80)
    print(" 🔍 MONITOR DE CONEXÃO Z-API - v3.1")
    print(" 📧 Alertas: Email + WhatsApp")
    print(" 🗑️  Limpeza automática: Fila > 100 mensagens")
    print("="*80)
    print(f" Instância: Meu numero")
    print(f" Instance ID: {ZAPI_CONFIG.get('instance_id', 'N/A')[:20]}...")
    print(f" Client Token: {'✓ Configurado' if ZAPI_CONFIG.get('client_token') else '✗ NÃO configurado'}")
    print(f" WhatsApp Alertas: {WHATSAPP_ALERT_NUMBER}")
    print(f" Email Alertas: {get_email_config()['alert_email']}")
    print(f" Intervalo: {CHECK_INTERVAL}s ({CHECK_INTERVAL//60} min)")
    print(f" Limite alerta: {QUEUE_ALERT_LIMIT} msgs")
    print(f" Limite limpeza: {QUEUE_LIMIT} msgs (APAGA TUDO!)")
    print(f" Iniciado: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("="*80 + "\n")


def monitor_loop():
    """Loop principal de monitoramento"""
    global ZAPI_CONFIG
    
    load_env_file(ENV_FILE)
    ZAPI_CONFIG = get_zapi_config()
    email_config = get_email_config()
    
    print_header()
    
    if not ZAPI_CONFIG.get('client_token'):
        print("⚠️  ATENÇÃO: ZAPI_CLIENT_TOKEN não configurado no .env!\n")
    
    check_count = 0
    disconnection_alert_sent = False
    queue_alert_sent = False
    smartphone_alert_sent = False
    
    while True:
        try:
            check_count += 1
            timestamp = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            
            print(f"[{timestamp}] Verificação #{check_count}...")
            
            status_info = check_zapi_status()
            queue_info = check_message_queue()
            last_status = load_last_status()
            
            # ===== VERIFICAR DESCONEXÃO Z-API =====
            status_changed = False
            if last_status and last_status.get('connected') and not status_info['connected']:
                status_changed = True
            
            if status_info['connected']:
                print(f"✅ Status: CONECTADO ({status_info['status']})")
                disconnection_alert_sent = False
            else:
                print(f"❌ Status: DESCONECTADO ({status_info['status']})")
                if status_info['error']:
                    print(f"   Erro: {status_info['error']}")
                
                if status_changed or (not disconnection_alert_sent and not last_status):
                    print(f"📧 Enviando alertas para email e WhatsApp...")
                    email_sent = send_disconnection_alert(status_info, email_config)
                    whatsapp_sent = send_disconnection_alert_whatsapp(status_info)
                    
                    if email_sent:
                        print(f"✅ Email enviado para {email_config['alert_email']}")
                    if whatsapp_sent:
                        print(f"✅ WhatsApp enviado para {WHATSAPP_ALERT_NUMBER}")
                    
                    disconnection_alert_sent = True
                elif disconnection_alert_sent:
                    print("⚠️  Alerta de desconexão já enviado")
            
            # ===== VERIFICAR CELULAR DESCONECTADO =====
            smartphone_changed = False
            if last_status and last_status.get('smartphone_connected') and not status_info.get('smartphone_connected'):
                smartphone_changed = True
            
            if status_info.get('smartphone_connected'):
                print(f"📱 Celular: CONECTADO à internet")
                smartphone_alert_sent = False
            else:
                print(f"📱 Celular: DESCONECTADO da internet")
                
                if smartphone_changed or (not smartphone_alert_sent and not last_status):
                    print(f"📧 Enviando alerta de celular desconectado...")
                    send_smartphone_disconnected_alert(status_info, email_config)
                    print(f"✅ Alertas enviados (email + WhatsApp)")
                    smartphone_alert_sent = True
            
            # ===== VERIFICAR FILA DE MENSAGENS =====
            print(f"📊 Fila de mensagens: {queue_info['queue_size']}")
            
            # Verificar se ultrapassou limite de LIMPEZA (100)
            if queue_info['queue_size'] > QUEUE_LIMIT:
                print(f"🚨 FILA CRÍTICA: {queue_info['queue_size']} > {QUEUE_LIMIT} mensagens!")
                print(f"🗑️  LIMPANDO FILA AUTOMATICAMENTE...")
                
                queue_size_before = queue_info['queue_size']
                
                # Limpar a fila
                success, error = clear_message_queue()
                
                if success:
                    print(f"✅ Fila limpa com sucesso! {queue_size_before} mensagens removidas")
                    print(f"📧 Enviando notificação de limpeza...")
                    send_queue_cleared_alert(queue_size_before, email_config)
                    print(f"✅ Notificações enviadas")
                else:
                    print(f"❌ ERRO ao limpar fila: {error}")
                    print(f"📧 Enviando alerta de erro...")
                    # Ainda envia alerta de fila alta
                    send_queue_alert(queue_info, email_config)
                    send_queue_alert_whatsapp(queue_info)
                
                queue_alert_sent = True
                
            # Verificar se ultrapassou limite de ALERTA (20)
            elif queue_info['queue_size'] > QUEUE_ALERT_LIMIT:
                print(f"⚠️  ATENÇÃO: Fila acima do limite de alerta ({QUEUE_ALERT_LIMIT} msgs)")
                
                last_queue_exceeded = last_status and last_status.get('queue_size', 0) > QUEUE_ALERT_LIMIT
                
                if (not last_queue_exceeded) or (not queue_alert_sent and not last_status):
                    print(f"📧 Enviando alertas de fila...")
                    email_sent = send_queue_alert(queue_info, email_config)
                    whatsapp_sent = send_queue_alert_whatsapp(queue_info)
                    
                    if email_sent:
                        print(f"✅ Email enviado")
                    if whatsapp_sent:
                        print(f"✅ WhatsApp enviado")
                    
                    queue_alert_sent = True
                elif queue_alert_sent:
                    print("⚠️  Alerta de fila já enviado")
            else:
                if queue_info['queue_size'] > 0:
                    print(f"✅ Fila dentro dos limites (alerta: {QUEUE_ALERT_LIMIT} | limpeza: {QUEUE_LIMIT})")
                queue_alert_sent = False
            
            if queue_info['error']:
                print(f"⚠️  Erro ao verificar fila: {queue_info['error']}")
            
            save_status(status_info, queue_info)
            
            print(f"⏳ Próxima verificação em {CHECK_INTERVAL} segundos...\n")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Monitoramento interrompido pelo usuário")
            print("="*80)
            sys.exit(0)
            
        except Exception as e:
            print(f"❌ Erro no loop: {e}")
            import traceback
            traceback.print_exc()
            print(f"⏳ Aguardando {CHECK_INTERVAL} segundos...\n")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        monitor_loop()
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
