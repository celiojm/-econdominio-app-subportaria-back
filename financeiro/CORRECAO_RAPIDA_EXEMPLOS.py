#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CORREÇÃO RÁPIDA - Adicionar envio de email ao código existente
Este script pode ser usado como referência para adicionar envio de email
nas funções que já existem no sistema
"""

import os
from datetime import date, datetime
from typing import Optional

# ============================================================================
# EXEMPLO 1: ADICIONAR AO CÓDIGO DE CRIAÇÃO DE COBRANÇA
# ============================================================================

# ===== ANTES (código atual sem email) =====
def criar_cobranca_OLD(cliente_id, valor, vencimento, descricao):
    """
    CÓDIGO ANTIGO - apenas cria cobrança no Asaas
    """
    # Buscar dados do cliente
    cliente = db.get_cliente(cliente_id)
    
    # Criar cobrança no Asaas
    cobranca_asaas = asaas_api.create_payment(
        customer=cliente.asaas_customer_id,
        billingType="BOLETO",
        value=valor,
        dueDate=vencimento.strftime("%Y-%m-%d"),
        description=descricao
    )
    
    # Salvar no banco
    cobranca = Cobranca(
        cliente_id=cliente_id,
        asaas_id=cobranca_asaas['id'],
        valor=valor,
        vencimento=vencimento,
        status='PENDING'
    )
    db.add(cobranca)
    db.commit()
    
    return cobranca


# ===== DEPOIS (código corrigido COM email) =====
def criar_cobranca_NEW(cliente_id, valor, vencimento, descricao):
    """
    CÓDIGO NOVO - cria cobrança no Asaas E envia email
    """
    # ADICIONAR IMPORT no topo do arquivo:
    from financeiro_email import enviar_email_cobranca
    
    # Buscar dados do cliente
    cliente = db.get_cliente(cliente_id)
    
    # Criar cobrança no Asaas
    cobranca_asaas = asaas_api.create_payment(
        customer=cliente.asaas_customer_id,
        billingType="BOLETO",
        value=valor,
        dueDate=vencimento.strftime("%Y-%m-%d"),
        description=descricao
    )
    
    # Salvar no banco
    cobranca = Cobranca(
        cliente_id=cliente_id,
        asaas_id=cobranca_asaas['id'],
        valor=valor,
        vencimento=vencimento,
        status='PENDING'
    )
    db.add(cobranca)
    db.commit()
    
    # ========================================
    # NOVO: ENVIAR EMAIL PARA O CLIENTE
    # ========================================
    try:
        email_enviado = enviar_email_cobranca(
            email=cliente.email,
            nome=cliente.nome,
            condominio=cliente.condominio_nome,  # ou buscar do banco
            valor=valor,
            vencimento=vencimento,
            link=cobranca_asaas.get('invoiceUrl', cobranca_asaas.get('bankSlipUrl')),
            codigo=cobranca_asaas['id'],
            descricao=descricao
        )
        
        if email_enviado:
            print(f"✅ Email de cobrança enviado para {cliente.email}")
            # Opcional: registrar no banco que email foi enviado
            cobranca.email_enviado = True
            db.commit()
        else:
            print(f"⚠️ Falha ao enviar email para {cliente.email}")
            
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")
        # Não falhar a criação da cobrança por causa do email
    
    return cobranca


# ============================================================================
# EXEMPLO 2: ADICIONAR AO WEBHOOK DO ASAAS (CONFIRMAÇÃO DE PAGAMENTO)
# ============================================================================

# ===== ANTES (webhook sem email) =====
@app.route('/webhook/asaas', methods=['POST'])
def webhook_asaas_OLD():
    """
    CÓDIGO ANTIGO - apenas atualiza status no banco
    """
    data = request.json
    
    if data['event'] == 'PAYMENT_RECEIVED':
        payment = data['payment']
        
        # Atualizar cobrança no banco
        cobranca = db.query(Cobranca).filter_by(asaas_id=payment['id']).first()
        if cobranca:
            cobranca.status = 'RECEIVED'
            cobranca.data_pagamento = datetime.now()
            db.commit()
    
    return {"status": "ok"}


# ===== DEPOIS (webhook COM email) =====
@app.route('/webhook/asaas', methods=['POST'])
def webhook_asaas_NEW():
    """
    CÓDIGO NOVO - atualiza status E envia email de confirmação
    """
    # ADICIONAR IMPORT no topo do arquivo:
    from financeiro_email import enviar_email_pagamento_confirmado
    
    data = request.json
    
    if data['event'] == 'PAYMENT_RECEIVED':
        payment = data['payment']
        
        # Atualizar cobrança no banco
        cobranca = db.query(Cobranca).filter_by(asaas_id=payment['id']).first()
        if cobranca:
            cobranca.status = 'RECEIVED'
            cobranca.data_pagamento = datetime.now()
            db.commit()
            
            # ========================================
            # NOVO: ENVIAR EMAIL DE CONFIRMAÇÃO
            # ========================================
            try:
                cliente = cobranca.cliente
                
                # Mapear forma de pagamento
                forma_pagamento_map = {
                    'BOLETO': 'Boleto Bancário',
                    'CREDIT_CARD': 'Cartão de Crédito',
                    'PIX': 'PIX',
                    'UNDEFINED': 'Não informado'
                }
                forma = forma_pagamento_map.get(payment.get('billingType'), 'Não informado')
                
                email_enviado = enviar_email_pagamento_confirmado(
                    email=cliente.email,
                    nome=cliente.nome,
                    condominio=cliente.condominio_nome,
                    valor_pago=float(payment['value']),
                    data_pagamento=datetime.fromisoformat(payment['paymentDate']),
                    codigo=payment['id'],
                    forma_pagamento=forma
                )
                
                if email_enviado:
                    print(f"✅ Email de confirmação enviado para {cliente.email}")
                else:
                    print(f"⚠️ Falha ao enviar confirmação para {cliente.email}")
                    
            except Exception as e:
                print(f"❌ Erro ao enviar email de confirmação: {e}")
    
    return {"status": "ok"}


# ============================================================================
# EXEMPLO 3: CRIAR JOB PARA ENVIAR LEMBRETES DE VENCIMENTO
# ============================================================================

def job_enviar_lembretes_vencimento():
    """
    Job que deve rodar diariamente para enviar lembretes
    Configurar no crontab:
    0 9 * * * cd /path/to/financeiro && python3 job_lembretes.py
    """
    from datetime import timedelta
    from financeiro_email import enviar_email_cobranca
    
    # Data de vencimento: daqui a 3 dias
    data_lembrete = date.today() + timedelta(days=3)
    
    # Buscar cobranças que vencem em 3 dias e estão pendentes
    cobrancas = db.query(Cobranca).filter(
        Cobranca.vencimento == data_lembrete,
        Cobranca.status == 'PENDING',
        Cobranca.lembrete_enviado == False  # adicionar este campo no banco
    ).all()
    
    print(f"📧 Enviando {len(cobrancas)} lembretes de vencimento...")
    
    enviados = 0
    for cobranca in cobrancas:
        try:
            cliente = cobranca.cliente
            
            # Buscar link de pagamento do Asaas
            payment = asaas_api.get_payment(cobranca.asaas_id)
            link = payment.get('invoiceUrl', payment.get('bankSlipUrl', '#'))
            
            sucesso = enviar_email_cobranca(
                email=cliente.email,
                nome=cliente.nome,
                condominio=cliente.condominio_nome,
                valor=cobranca.valor,
                vencimento=cobranca.vencimento,
                link=link,
                codigo=cobranca.asaas_id,
                descricao=f"⚠️ LEMBRETE: Vencimento em 3 dias\n{cobranca.descricao}"
            )
            
            if sucesso:
                cobranca.lembrete_enviado = True
                db.commit()
                enviados += 1
                print(f"  ✅ Lembrete enviado para {cliente.email}")
            else:
                print(f"  ⚠️ Falha ao enviar para {cliente.email}")
                
        except Exception as e:
            print(f"  ❌ Erro com {cliente.email}: {e}")
    
    print(f"✅ {enviados}/{len(cobrancas)} lembretes enviados com sucesso")
    return enviados


# ============================================================================
# EXEMPLO 4: CRIAR JOB PARA COBRAR ATRASADOS
# ============================================================================

def job_cobrar_atrasados():
    """
    Job que deve rodar diariamente para cobrar valores em atraso
    Configurar no crontab:
    0 10 * * * cd /path/to/financeiro && python3 job_atrasados.py
    """
    from financeiro_email import enviar_email_cobranca_vencida
    
    # Buscar cobranças vencidas e pendentes
    cobrancas = db.query(Cobranca).filter(
        Cobranca.vencimento < date.today(),
        Cobranca.status == 'PENDING'
    ).all()
    
    print(f"🚨 Processando {len(cobrancas)} cobranças atrasadas...")
    
    enviados = 0
    for cobranca in cobrancas:
        try:
            cliente = cobranca.cliente
            
            # Calcular dias de atraso
            dias_atraso = (date.today() - cobranca.vencimento).days
            
            # Calcular valor com juros
            # Exemplo: 2% de multa + 0.033% ao dia
            multa = cobranca.valor * 0.02
            juros_diario = cobranca.valor * 0.00033 * dias_atraso
            valor_com_juros = cobranca.valor + multa + juros_diario
            
            # Buscar link de pagamento
            payment = asaas_api.get_payment(cobranca.asaas_id)
            link = payment.get('invoiceUrl', payment.get('bankSlipUrl', '#'))
            
            # Enviar apenas se não enviou hoje (evitar spam)
            if not cobranca.ultimo_email_atraso or \
               (date.today() - cobranca.ultimo_email_atraso).days >= 7:  # 1x por semana
                
                sucesso = enviar_email_cobranca_vencida(
                    email=cliente.email,
                    nome=cliente.nome,
                    condominio=cliente.condominio_nome,
                    valor=cobranca.valor,
                    valor_com_juros=valor_com_juros,
                    vencimento=cobranca.vencimento,
                    dias_atraso=dias_atraso,
                    link=link,
                    codigo=cobranca.asaas_id
                )
                
                if sucesso:
                    cobranca.ultimo_email_atraso = date.today()
                    db.commit()
                    enviados += 1
                    print(f"  ✅ Cobrança enviada para {cliente.email} ({dias_atraso} dias)")
                else:
                    print(f"  ⚠️ Falha ao enviar para {cliente.email}")
            else:
                print(f"  ⏭️  Pulando {cliente.email} (já enviado recentemente)")
                
        except Exception as e:
            print(f"  ❌ Erro com {cliente.email}: {e}")
    
    print(f"✅ {enviados} cobranças de atraso enviadas")
    return enviados


# ============================================================================
# EXEMPLO 5: ADICIONAR CAMPOS NECESSÁRIOS NO BANCO DE DADOS
# ============================================================================

# SQL para adicionar campos de controle de email na tabela Cobranca
SQL_ADD_EMAIL_FIELDS = """
-- Adicionar campos de controle de email
ALTER TABLE cobrancas ADD COLUMN email_enviado BOOLEAN DEFAULT FALSE;
ALTER TABLE cobrancas ADD COLUMN lembrete_enviado BOOLEAN DEFAULT FALSE;
ALTER TABLE cobrancas ADD COLUMN ultimo_email_atraso DATE;
ALTER TABLE cobrancas ADD COLUMN data_email_confirmacao DATETIME;

-- Adicionar índices para performance
CREATE INDEX idx_cobrancas_vencimento ON cobrancas(vencimento);
CREATE INDEX idx_cobrancas_status ON cobrancas(status);
CREATE INDEX idx_cobrancas_email_enviado ON cobrancas(email_enviado);
"""

# ============================================================================
# RESUMO DE INTEGRAÇÃO
# ============================================================================

RESUMO_INTEGRACAO = """
========================================
RESUMO - COMO INTEGRAR O MÓDULO DE EMAIL
========================================

1. COPIAR ARQUIVO:
   cp financeiro_email.py /home/visionlpr/backendV2/financeiro/

2. CORRIGIR .ENV:
   EMAIL_FROM_NAME=Financeiro Econdominio  # SEM acento!

3. ADICIONAR IMPORTS nos arquivos existentes:
   from financeiro_email import (
       enviar_email_cobranca,
       enviar_email_cobranca_vencida,
       enviar_email_pagamento_confirmado
   )

4. ADICIONAR ENVIO DE EMAIL nas funções:
   
   A) Na função de criar cobrança:
      → Após criar cobrança no Asaas
      → Chamar enviar_email_cobranca()
   
   B) No webhook do Asaas:
      → Quando event == 'PAYMENT_RECEIVED'
      → Chamar enviar_email_pagamento_confirmado()
   
   C) Criar jobs diários (opcional):
      → job_lembretes.py - lembretes 3 dias antes
      → job_atrasados.py - cobrar valores vencidos

5. ADICIONAR CAMPOS NO BANCO (opcional):
   → email_enviado
   → lembrete_enviado
   → ultimo_email_atraso
   → data_email_confirmacao

6. TESTAR:
   cd /home/visionlpr/backendV2/financeiro
   python3 teste_email_completo.py

========================================
"""

if __name__ == "__main__":
    print(RESUMO_INTEGRACAO)
    print("\n📚 Veja os exemplos acima para saber como integrar o módulo!")
    print("💡 Copie e cole as funções NEW no seu código")
    print("✅ Teste antes de colocar em produção!\n")
