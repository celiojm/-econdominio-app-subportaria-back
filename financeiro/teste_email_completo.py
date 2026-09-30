#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SCRIPT DE TESTE - Módulo de Email de Cobranças
Testa todas as funções de envio de email
"""

import os
import sys
from datetime import date, datetime, timedelta

# Adicionar o diretório do módulo ao path
sys.path.insert(0, '/home/visionlpr/backendV2/financeiro')

print("\n" + "="*80)
print("🧪 TESTE COMPLETO - MÓDULO DE EMAIL DE COBRANÇAS")
print("="*80 + "\n")

# Carregar variáveis de ambiente
env_file = "/home/visionlpr/backendV2/.env"
print("📁 Carregando configurações...")
try:
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value
    print("✅ Variáveis de ambiente carregadas com sucesso!\n")
except Exception as e:
    print(f"❌ Erro ao carregar .env: {e}\n")
    sys.exit(1)

# Importar o módulo
try:
    from financeiro_email import (
        email_service,
        enviar_email_cobranca,
        enviar_email_cobranca_vencida,
        enviar_email_pagamento_confirmado
    )
    print("✅ Módulo financeiro_email importado com sucesso!\n")
except Exception as e:
    print(f"❌ Erro ao importar módulo: {e}\n")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Configurações para teste
EMAIL_TESTE = "celiojm@gmail.com"
NOME_TESTE = "Celio Junior"
CONDOMINIO_TESTE = "Econdominio Teste"

print("="*80)
print("TESTE 1: Email de Nova Cobrança")
print("="*80)

try:
    resultado = enviar_email_cobranca(
        email=EMAIL_TESTE,
        nome=NOME_TESTE,
        condominio=CONDOMINIO_TESTE,
        valor=350.50,
        vencimento=date.today() + timedelta(days=7),
        link="https://pagamento.asaas.com/exemplo123",
        codigo="COB-TEST-001",
        descricao="Taxa de condomínio - Janeiro/2025"
    )
    
    if resultado:
        print("✅ SUCESSO: Email de nova cobrança enviado!")
    else:
        print("❌ FALHA: Não foi possível enviar o email de nova cobrança")
except Exception as e:
    print(f"❌ ERRO: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("TESTE 2: Email de Cobrança Vencida")
print("="*80)

try:
    resultado = enviar_email_cobranca_vencida(
        email=EMAIL_TESTE,
        nome=NOME_TESTE,
        condominio=CONDOMINIO_TESTE,
        valor=350.50,
        valor_com_juros=385.55,  # Valor com multa e juros
        vencimento=date.today() - timedelta(days=15),
        dias_atraso=15,
        link="https://pagamento.asaas.com/exemplo456",
        codigo="COB-TEST-002"
    )
    
    if resultado:
        print("✅ SUCESSO: Email de cobrança vencida enviado!")
    else:
        print("❌ FALHA: Não foi possível enviar o email de cobrança vencida")
except Exception as e:
    print(f"❌ ERRO: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("TESTE 3: Email de Confirmação de Pagamento")
print("="*80)

try:
    resultado = enviar_email_pagamento_confirmado(
        email=EMAIL_TESTE,
        nome=NOME_TESTE,
        condominio=CONDOMINIO_TESTE,
        valor_pago=350.50,
        data_pagamento=datetime.now(),
        codigo="COB-TEST-003",
        forma_pagamento="PIX"
    )
    
    if resultado:
        print("✅ SUCESSO: Email de confirmação de pagamento enviado!")
    else:
        print("❌ FALHA: Não foi possível enviar o email de confirmação")
except Exception as e:
    print(f"❌ ERRO: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("📊 RESUMO DOS TESTES")
print("="*80)
print("\n✅ Testes concluídos!")
print(f"📧 Verifique o email: {EMAIL_TESTE}")
print("⏱️  Aguarde 1-2 minutos para receber os emails")
print("\nSe todos os emails chegaram, o módulo está funcionando perfeitamente!")
print("\n" + "="*80 + "\n")
