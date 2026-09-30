#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
EXEMPLO DE INTEGRAÇÃO - Sistema de Cobranças
Como integrar o módulo de email no sistema existente
"""

from datetime import date, datetime, timedelta
from typing import Optional
import logging

# Importar o módulo de email
from financeiro_email import (
    enviar_email_cobranca,
    enviar_email_cobranca_vencida,
    enviar_email_pagamento_confirmado
)

logger = logging.getLogger(__name__)


class CobrancaEmailIntegration:
    """
    Classe para integrar o envio de emails ao sistema de cobranças
    """
    
    @staticmethod
    def enviar_email_nova_cobranca(cobranca_data: dict) -> bool:
        """
        Envia email quando uma nova cobrança é criada
        
        Args:
            cobranca_data: Dicionário com dados da cobrança:
                - cliente_email: str
                - cliente_nome: str
                - condominio_nome: str
                - valor: float
                - vencimento: date
                - link_pagamento: str
                - codigo_cobranca: str
                - descricao: str (opcional)
                
        Returns:
            bool: True se email foi enviado com sucesso
            
        Exemplo de uso:
            ```python
            # Após criar cobrança no Asaas
            cobranca_criada = criar_cobranca_asaas(...)
            
            # Enviar email
            dados = {
                "cliente_email": "cliente@email.com",
                "cliente_nome": "João Silva",
                "condominio_nome": "Residencial Exemplo",
                "valor": 500.00,
                "vencimento": date(2025, 1, 15),
                "link_pagamento": cobranca_criada['invoiceUrl'],
                "codigo_cobranca": cobranca_criada['id'],
                "descricao": "Taxa de condomínio - Janeiro/2025"
            }
            
            sucesso = CobrancaEmailIntegration.enviar_email_nova_cobranca(dados)
            ```
        """
        try:
            logger.info(f"Enviando email de nova cobrança para {cobranca_data.get('cliente_email')}")
            
            resultado = enviar_email_cobranca(
                email=cobranca_data["cliente_email"],
                nome=cobranca_data["cliente_nome"],
                condominio=cobranca_data["condominio_nome"],
                valor=cobranca_data["valor"],
                vencimento=cobranca_data["vencimento"],
                link=cobranca_data["link_pagamento"],
                codigo=cobranca_data["codigo_cobranca"],
                descricao=cobranca_data.get("descricao")
            )
            
            if resultado:
                logger.info(f"✅ Email enviado com sucesso para {cobranca_data.get('cliente_email')}")
            else:
                logger.error(f"❌ Falha ao enviar email para {cobranca_data.get('cliente_email')}")
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao enviar email de nova cobrança: {e}")
            return False
    
    @staticmethod
    def enviar_email_lembrete_vencimento(cobranca_data: dict, dias_antes: int = 3) -> bool:
        """
        Envia lembrete X dias antes do vencimento
        
        Args:
            cobranca_data: Dados da cobrança
            dias_antes: Quantos dias antes do vencimento enviar
            
        Returns:
            bool: True se email foi enviado
            
        Exemplo de uso:
            ```python
            # Em um job diário que verifica vencimentos
            cobrancas_proximas = buscar_cobrancas_vencendo_em(3)
            
            for cobranca in cobrancas_proximas:
                dados = {
                    "cliente_email": cobranca.cliente_email,
                    "cliente_nome": cobranca.cliente_nome,
                    "condominio_nome": cobranca.condominio_nome,
                    "valor": cobranca.valor,
                    "vencimento": cobranca.vencimento,
                    "link_pagamento": cobranca.link_pagamento,
                    "codigo_cobranca": cobranca.codigo,
                    "descricao": f"Lembrete: Vence em {dias_antes} dias"
                }
                
                CobrancaEmailIntegration.enviar_email_lembrete_vencimento(dados, dias_antes)
            ```
        """
        try:
            logger.info(f"Enviando lembrete de vencimento para {cobranca_data.get('cliente_email')}")
            
            # Adicionar informação de lembrete na descrição
            descricao_original = cobranca_data.get("descricao", "")
            descricao_completa = f"⚠️ LEMBRETE: Vence em {dias_antes} dia(s)\n{descricao_original}"
            
            cobranca_data_copia = cobranca_data.copy()
            cobranca_data_copia["descricao"] = descricao_completa
            
            return CobrancaEmailIntegration.enviar_email_nova_cobranca(cobranca_data_copia)
            
        except Exception as e:
            logger.error(f"Erro ao enviar lembrete de vencimento: {e}")
            return False
    
    @staticmethod
    def enviar_email_cobranca_atrasada(cobranca_data: dict) -> bool:
        """
        Envia email de cobrança vencida/atrasada
        
        Args:
            cobranca_data: Dicionário com dados:
                - cliente_email: str
                - cliente_nome: str
                - condominio_nome: str
                - valor_original: float
                - valor_com_juros: float
                - vencimento: date
                - dias_atraso: int
                - link_pagamento: str
                - codigo_cobranca: str
                
        Returns:
            bool: True se email foi enviado
            
        Exemplo de uso:
            ```python
            # Em um job diário que verifica cobranças vencidas
            cobrancas_vencidas = buscar_cobrancas_vencidas()
            
            for cobranca in cobrancas_vencidas:
                dias_atraso = (date.today() - cobranca.vencimento).days
                valor_com_juros = calcular_valor_com_juros(cobranca.valor, dias_atraso)
                
                dados = {
                    "cliente_email": cobranca.cliente_email,
                    "cliente_nome": cobranca.cliente_nome,
                    "condominio_nome": cobranca.condominio_nome,
                    "valor_original": cobranca.valor,
                    "valor_com_juros": valor_com_juros,
                    "vencimento": cobranca.vencimento,
                    "dias_atraso": dias_atraso,
                    "link_pagamento": cobranca.link_pagamento,
                    "codigo_cobranca": cobranca.codigo
                }
                
                CobrancaEmailIntegration.enviar_email_cobranca_atrasada(dados)
            ```
        """
        try:
            logger.info(f"Enviando email de cobrança vencida para {cobranca_data.get('cliente_email')}")
            
            resultado = enviar_email_cobranca_vencida(
                email=cobranca_data["cliente_email"],
                nome=cobranca_data["cliente_nome"],
                condominio=cobranca_data["condominio_nome"],
                valor=cobranca_data["valor_original"],
                valor_com_juros=cobranca_data["valor_com_juros"],
                vencimento=cobranca_data["vencimento"],
                dias_atraso=cobranca_data["dias_atraso"],
                link=cobranca_data["link_pagamento"],
                codigo=cobranca_data["codigo_cobranca"]
            )
            
            if resultado:
                logger.info(f"✅ Email de cobrança vencida enviado para {cobranca_data.get('cliente_email')}")
            else:
                logger.error(f"❌ Falha ao enviar email de cobrança vencida para {cobranca_data.get('cliente_email')}")
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao enviar email de cobrança vencida: {e}")
            return False
    
    @staticmethod
    def enviar_email_confirmacao_pagamento(pagamento_data: dict) -> bool:
        """
        Envia email de confirmação quando pagamento é recebido
        
        Args:
            pagamento_data: Dicionário com dados:
                - cliente_email: str
                - cliente_nome: str
                - condominio_nome: str
                - valor_pago: float
                - data_pagamento: datetime
                - codigo_cobranca: str
                - forma_pagamento: str (PIX, Cartão, Boleto, etc)
                
        Returns:
            bool: True se email foi enviado
            
        Exemplo de uso:
            ```python
            # No webhook do Asaas quando recebe confirmação de pagamento
            @app.route('/webhook/asaas', methods=['POST'])
            def webhook_asaas():
                data = request.json
                
                if data['event'] == 'PAYMENT_RECEIVED':
                    pagamento = data['payment']
                    
                    dados = {
                        "cliente_email": pagamento['customer']['email'],
                        "cliente_nome": pagamento['customer']['name'],
                        "condominio_nome": "Seu Condomínio",
                        "valor_pago": pagamento['value'],
                        "data_pagamento": datetime.fromisoformat(pagamento['paymentDate']),
                        "codigo_cobranca": pagamento['id'],
                        "forma_pagamento": pagamento['billingType']
                    }
                    
                    CobrancaEmailIntegration.enviar_email_confirmacao_pagamento(dados)
                
                return {"status": "ok"}
            ```
        """
        try:
            logger.info(f"Enviando confirmação de pagamento para {pagamento_data.get('cliente_email')}")
            
            resultado = enviar_email_pagamento_confirmado(
                email=pagamento_data["cliente_email"],
                nome=pagamento_data["cliente_nome"],
                condominio=pagamento_data["condominio_nome"],
                valor_pago=pagamento_data["valor_pago"],
                data_pagamento=pagamento_data["data_pagamento"],
                codigo=pagamento_data["codigo_cobranca"],
                forma_pagamento=pagamento_data["forma_pagamento"]
            )
            
            if resultado:
                logger.info(f"✅ Confirmação de pagamento enviada para {pagamento_data.get('cliente_email')}")
            else:
                logger.error(f"❌ Falha ao enviar confirmação para {pagamento_data.get('cliente_email')}")
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao enviar confirmação de pagamento: {e}")
            return False


# ============================================================================
# EXEMPLO DE USO EM ROTAS/ENDPOINTS
# ============================================================================

def exemplo_rota_criar_cobranca():
    """
    Exemplo de como integrar em uma rota Flask/FastAPI
    """
    
    # Simular criação de cobrança
    print("\n📝 Criando nova cobrança...")
    
    # 1. Criar cobrança no Asaas
    cobranca_asaas = {
        "id": "pay_123456789",
        "invoiceUrl": "https://pagamento.asaas.com/pay_123456789",
        "value": 500.00,
        "dueDate": "2025-01-15"
    }
    
    # 2. Salvar cobrança no banco de dados
    # ... código para salvar no banco ...
    
    # 3. Enviar email para o cliente
    dados_email = {
        "cliente_email": "celiojm@gmail.com",
        "cliente_nome": "Celio Junior",
        "condominio_nome": "Econdominio Teste",
        "valor": cobranca_asaas["value"],
        "vencimento": datetime.strptime(cobranca_asaas["dueDate"], "%Y-%m-%d").date(),
        "link_pagamento": cobranca_asaas["invoiceUrl"],
        "codigo_cobranca": cobranca_asaas["id"],
        "descricao": "Taxa de condomínio - Janeiro/2025"
    }
    
    sucesso = CobrancaEmailIntegration.enviar_email_nova_cobranca(dados_email)
    
    if sucesso:
        print("✅ Cobrança criada e email enviado!")
    else:
        print("⚠️  Cobrança criada mas email não foi enviado")
    
    return sucesso


def exemplo_job_verificar_vencimentos():
    """
    Exemplo de job diário para enviar lembretes
    """
    print("\n🔔 Job: Verificando vencimentos próximos...")
    
    # Buscar cobranças que vencem em 3 dias
    # cobrancas = db.query(Cobranca).filter(
    #     Cobranca.vencimento == date.today() + timedelta(days=3),
    #     Cobranca.status == 'PENDING'
    # ).all()
    
    # Exemplo simulado
    cobrancas = [
        {
            "cliente_email": "celiojm@gmail.com",
            "cliente_nome": "Celio Junior",
            "condominio_nome": "Econdominio Teste",
            "valor": 500.00,
            "vencimento": date.today() + timedelta(days=3),
            "link_pagamento": "https://pagamento.asaas.com/exemplo",
            "codigo_cobranca": "pay_lembrete_001"
        }
    ]
    
    enviados = 0
    for cobranca in cobrancas:
        if CobrancaEmailIntegration.enviar_email_lembrete_vencimento(cobranca, dias_antes=3):
            enviados += 1
    
    print(f"✅ {enviados} lembretes enviados")
    return enviados


def exemplo_job_cobrar_atrasados():
    """
    Exemplo de job diário para cobrar atrasados
    """
    print("\n🚨 Job: Processando cobranças atrasadas...")
    
    # Buscar cobranças vencidas
    # cobrancas = db.query(Cobranca).filter(
    #     Cobranca.vencimento < date.today(),
    #     Cobranca.status == 'PENDING'
    # ).all()
    
    # Exemplo simulado
    cobrancas = [
        {
            "cliente_email": "celiojm@gmail.com",
            "cliente_nome": "Celio Junior",
            "condominio_nome": "Econdominio Teste",
            "valor_original": 500.00,
            "vencimento": date.today() - timedelta(days=10)
        }
    ]
    
    enviados = 0
    for cobranca in cobrancas:
        dias_atraso = (date.today() - cobranca["vencimento"]).days
        valor_com_juros = cobranca["valor_original"] * 1.10  # 10% de juros (exemplo)
        
        dados = {
            **cobranca,
            "valor_com_juros": valor_com_juros,
            "dias_atraso": dias_atraso,
            "link_pagamento": "https://pagamento.asaas.com/exemplo",
            "codigo_cobranca": "pay_atrasado_001"
        }
        
        if CobrancaEmailIntegration.enviar_email_cobranca_atrasada(dados):
            enviados += 1
    
    print(f"✅ {enviados} cobranças de atraso enviadas")
    return enviados


# ============================================================================
# TESTE
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*80)
    print("🧪 TESTE DE INTEGRAÇÃO - Sistema de Cobranças")
    print("="*80)
    
    # Configurar ambiente
    import os
    env_file = "/home/visionlpr/backendV2/.env"
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value
    
    # Executar exemplos
    try:
        exemplo_rota_criar_cobranca()
        print()
        exemplo_job_verificar_vencimentos()
        print()
        exemplo_job_cobrar_atrasados()
        
        print("\n" + "="*80)
        print("✅ Testes de integração concluídos!")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n❌ Erro nos testes: {e}")
        import traceback
        traceback.print_exc()
