# ================================================================================
#  PATH: backend/financeiro/financeiro_sync.py
#  DESCRIPTION: Sincronização UNIFICADA - Pagamentos Asaas + Validades + Assinaturas
#  VERSÃO: 2.0.0 - Endpoint único para todas as sincronizações
# ================================================================================

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date, timedelta
from decimal import Decimal
import httpx
import logging
import os
import re

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# ================================================================================
#  CONFIGURAÇÕES ASAAS
# ================================================================================

ASAAS_AMBIENTE = os.getenv("ASAAS_AMBIENTE", "production")

ASAAS_URLS = {
    "sandbox": "https://api-sandbox.asaas.com/v3",
    "production": "https://api.asaas.com/v3"
}

ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "")  # 2026-09-30: chave so no .env

ASAAS_BASE_URL = ASAAS_URLS.get(ASAAS_AMBIENTE, ASAAS_URLS["sandbox"])


def get_asaas_headers():
    return {
        "Content-Type": "application/json",
        "access_token": ASAAS_API_KEY
    }


# ================================================================================
#  FUNÇÕES AUXILIARES
# ================================================================================

def extrair_dias_do_plano(descricao: str) -> int:
    """
    Extrai os dias de validade da descrição do plano.
    Ex: "Até 50 unidades - 30 dias" -> 30
    """
    if not descricao:
        return 30  # default
    
    descricao_lower = descricao.lower()
    
    # Tentar extrair número antes de "dias"
    match = re.search(r'(\d+)\s*dias?', descricao_lower)
    if match:
        return int(match.group(1))
    
    # Padrões conhecidos
    if 'trimestral' in descricao_lower or '90' in descricao:
        return 90
    if 'semestral' in descricao_lower or '180' in descricao:
        return 180
    if 'anual' in descricao_lower or '365' in descricao:
        return 365
    
    return 30  # default mensal


def calcular_nova_validade(validade_atual: date, dias: int, data_pagamento: date = None) -> date:
    """
    Calcula nova validade baseada na data do pagamento.
    Se a validade atual for maior que data_pagamento + dias, mantém a atual.
    Caso contrário, usa data_pagamento + dias.
    """
    if data_pagamento is None:
        data_pagamento = date.today()
    
    nova_validade = data_pagamento + timedelta(days=dias)
    
    # Se já tem validade maior, mantém a maior
    if validade_atual and validade_atual > nova_validade:
        return validade_atual
    
    return nova_validade


# ================================================================================
#  ENDPOINT PRINCIPAL - SINCRONIZAÇÃO UNIFICADA
# ================================================================================

@router.post("/sync/pagamentos")
async def sincronizar_pagamentos_completo(
    buscar_todos: bool = Query(True, description="Buscar todos os pagamentos (ignora filtro de data)"),
    data_inicio: Optional[str] = Query(None, description="Data início (YYYY-MM-DD)"),
    data_fim: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    SINCRONIZAÇÃO UNIFICADA - Faz tudo em um único endpoint:
    1. Busca TODOS os pagamentos confirmados no Asaas
    2. Importa/atualiza cobranças no banco local
    3. Vincula customer_id aos condomínios pelo CNPJ
    4. Cria assinaturas automaticamente se não existirem
    5. Atualiza validade_ate baseado nos dias do plano
    """
    try:
        # Definir período
        if not buscar_todos:
            if not data_inicio:
                data_inicio = (date.today() - timedelta(days=90)).isoformat()
            if not data_fim:
                data_fim = (date.today() + timedelta(days=1)).isoformat()
        
        logger.info(f"🔄 Iniciando sincronização completa - buscar_todos: {buscar_todos}")
        
        resultados = {
            "total_processados": 0,
            "pagamentos_novos": 0,
            "pagamentos_atualizados": 0,
            "assinaturas_criadas": 0,
            "validades_atualizadas": 0,
            "condominios_vinculados": 0,
            "erros": [],
            "detalhes": []
        }
        
        # Status de pagamentos confirmados
        status_pagos = ["RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH", "DUNNING_RECEIVED"]
        
        async with httpx.AsyncClient(timeout=60) as client:
            for status in status_pagos:
                try:
                    offset = 0
                    limit = 100
                    
                    while True:
                        # Construir parâmetros
                        params = {
                            "status": status,
                            "limit": limit,
                            "offset": offset
                        }
                        
                        # Adicionar filtros de data se especificado
                        if not buscar_todos:
                            if data_inicio:
                                params["paymentDate[ge]"] = data_inicio
                            if data_fim:
                                params["paymentDate[le]"] = data_fim
                        
                        logger.info(f"📥 Buscando pagamentos {status} - offset: {offset}")
                        
                        response = await client.get(
                            f"{ASAAS_BASE_URL}/payments",
                            headers=get_asaas_headers(),
                            params=params
                        )
                        
                        if response.status_code != 200:
                            logger.error(f"Erro Asaas {response.status_code}: {response.text}")
                            resultados["erros"].append(f"Erro Asaas status {status}: {response.status_code}")
                            break
                        
                        data = response.json()
                        pagamentos = data.get("data", [])
                        
                        if not pagamentos:
                            break
                        
                        for pag in pagamentos:
                            resultado = await processar_pagamento_completo(db, client, pag)
                            resultados["total_processados"] += 1
                            
                            if resultado.get("pagamento_novo"):
                                resultados["pagamentos_novos"] += 1
                            if resultado.get("pagamento_atualizado"):
                                resultados["pagamentos_atualizados"] += 1
                            if resultado.get("assinatura_criada"):
                                resultados["assinaturas_criadas"] += 1
                            if resultado.get("validade_atualizada"):
                                resultados["validades_atualizadas"] += 1
                            if resultado.get("condominio_vinculado"):
                                resultados["condominios_vinculados"] += 1
                            if resultado.get("erro"):
                                resultados["erros"].append(resultado["erro"])
                            
                            # Guardar apenas primeiros 30 detalhes para não sobrecarregar
                            if len(resultados["detalhes"]) < 30:
                                resultados["detalhes"].append(resultado)
                        
                        # Commit a cada lote
                        db.commit()
                        
                        # Verificar se há mais páginas
                        if not data.get("hasMore"):
                            break
                        
                        offset += limit
                        
                except Exception as e:
                    resultados["erros"].append(f"Erro ao buscar status {status}: {str(e)}")
                    logger.error(f"Erro ao buscar pagamentos {status}: {e}")
        
        # Commit final
        db.commit()
        
        logger.info(f"✅ Sincronização concluída: {resultados['total_processados']} pagamentos, "
                   f"{resultados['pagamentos_novos']} novos, {resultados['validades_atualizadas']} validades atualizadas")
        
        return {
            "success": True,
            "message": f"Sincronização concluída! {resultados['total_processados']} pagamentos processados.",
            "total_sincronizados": resultados["total_processados"],
            "novos": resultados["pagamentos_novos"],
            "atualizados": resultados["pagamentos_atualizados"],
            "assinaturas_criadas": resultados["assinaturas_criadas"],
            "validades_atualizadas": resultados["validades_atualizadas"],
            "condominios_vinculados": resultados["condominios_vinculados"],
            "periodo": {
                "inicio": data_inicio if not buscar_todos else "todos",
                "fim": data_fim if not buscar_todos else "todos"
            },
            "erros": resultados["erros"] if resultados["erros"] else None,
            "detalhes": resultados["detalhes"]
        }
        
    except Exception as e:
        logger.error(f"Erro na sincronização: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


async def processar_pagamento_completo(db: Session, client: httpx.AsyncClient, pagamento: dict) -> dict:
    """
    Processa um pagamento do Asaas de forma COMPLETA:
    1. Importa/atualiza cobrança
    2. Vincula condomínio pelo CNPJ
    3. Cria/atualiza assinatura
    4. Atualiza validade
    """
    resultado = {
        "payment_id": pagamento.get("id"),
        "valor": pagamento.get("value"),
        "customer_id": pagamento.get("customer"),
        "descricao": pagamento.get("description"),
        "pagamento_novo": False,
        "pagamento_atualizado": False,
        "assinatura_criada": False,
        "validade_atualizada": False,
        "condominio_vinculado": False,
        "erro": None
    }
    
    try:
        asaas_payment_id = pagamento.get("id")
        customer_id = pagamento.get("customer")
        descricao = pagamento.get("description", "")
        valor = Decimal(str(pagamento.get("value", 0)))
        valor_pago = Decimal(str(pagamento.get("netValue") or pagamento.get("value", 0)))
        
        if not asaas_payment_id:
            resultado["erro"] = "Pagamento sem ID"
            return resultado
        
        # Mapear forma de pagamento
        billing_type_map = {
            "BOLETO": "boleto", "PIX": "pix", "CREDIT_CARD": "cartao",
            "DEBIT_CARD": "cartao", "TRANSFER": "transferencia",
            "DEPOSIT": "transferencia", "UNDEFINED": None
        }
        forma_pag = billing_type_map.get(pagamento.get("billingType"), None)
        
        # Extrair datas
        data_pagamento = None
        if pagamento.get("paymentDate"):
            try:
                data_pagamento = datetime.strptime(pagamento["paymentDate"], "%Y-%m-%d").date()
            except:
                data_pagamento = date.today()
        elif pagamento.get("confirmedDate"):
            try:
                data_pagamento = datetime.strptime(pagamento["confirmedDate"], "%Y-%m-%d").date()
            except:
                data_pagamento = date.today()
        
        data_vencimento = None
        if pagamento.get("dueDate"):
            try:
                data_vencimento = datetime.strptime(pagamento["dueDate"], "%Y-%m-%d").date()
            except:
                pass
        
        resultado["data_pagamento"] = data_pagamento.isoformat() if data_pagamento else None
        
        # ====================================================================
        # PASSO 1: Encontrar/vincular condomínio
        # ====================================================================
        
        id_condominio = None
        condominio_nome = None
        
        if customer_id:
            # Primeiro buscar direto pelo asaas_customer_id
            result = db.execute(
                text("SELECT id, nome FROM condominios WHERE asaas_customer_id = :customer_id LIMIT 1"),
                {"customer_id": customer_id}
            )
            cond = result.fetchone()
            
            if cond:
                id_condominio = cond[0]
                condominio_nome = cond[1]
            else:
                # Buscar customer no Asaas para pegar CNPJ
                try:
                    resp = await client.get(
                        f"{ASAAS_BASE_URL}/customers/{customer_id}",
                        headers=get_asaas_headers()
                    )
                    if resp.status_code == 200:
                        customer_data = resp.json()
                        cnpj = customer_data.get("cpfCnpj", "")
                        
                        if cnpj:
                            cnpj_limpo = ''.join(c for c in cnpj if c.isdigit())
                            result = db.execute(
                                text("""
                                    SELECT id, nome FROM condominios 
                                    WHERE REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = :cnpj
                                    LIMIT 1
                                """),
                                {"cnpj": cnpj_limpo}
                            )
                            cond = result.fetchone()
                            
                            if cond:
                                id_condominio = cond[0]
                                condominio_nome = cond[1]
                                
                                # Vincular asaas_customer_id ao condomínio
                                db.execute(
                                    text("UPDATE condominios SET asaas_customer_id = :customer_id WHERE id = :id"),
                                    {"customer_id": customer_id, "id": id_condominio}
                                )
                                resultado["condominio_vinculado"] = True
                                logger.info(f"✅ Condomínio vinculado: {condominio_nome} -> {customer_id}")
                except Exception as e:
                    logger.error(f"Erro ao buscar customer {customer_id}: {e}")
        
        # Fallback: se não encontrou condomínio, IGNORA — não cria cobrança avulsa
        if not id_condominio:
            logger.warning(f"⚠️ Customer {customer_id} não vinculado a nenhum condomínio — ignorando")
            return resultado
        
        resultado["condominio_id"] = id_condominio
        resultado["condominio_nome"] = condominio_nome
        
        # ====================================================================
        # PASSO 2: Importar/atualizar cobrança
        # ====================================================================
        
        # Verificar se cobrança já existe
        result = db.execute(
            text("SELECT id_cobranca FROM cobrancas WHERE asaas_payment_id = :payment_id"),
            {"payment_id": asaas_payment_id}
        )
        cobranca_existente = result.fetchone()
        
        if cobranca_existente:
            # Atualizar cobrança existente
            db.execute(
                text("""
                    UPDATE cobrancas SET 
                        status = 'pago',
                        valor_pago = :valor_pago,
                        data_pagamento = :data_pagamento,
                        forma_pagamento = :forma_pagamento,
                        data_atualizacao = NOW()
                    WHERE asaas_payment_id = :payment_id
                """),
                {
                    "valor_pago": float(valor_pago),
                    "data_pagamento": data_pagamento,
                    "forma_pagamento": forma_pag,
                    "payment_id": asaas_payment_id
                }
            )
            resultado["pagamento_atualizado"] = True
            resultado["id_cobranca"] = cobranca_existente[0]
        else:
            # Tentar encontrar cobrança pendente com valor próximo
            cobranca_match = None
            if customer_id:
                result = db.execute(
                    text("""
                        SELECT id_cobranca FROM cobrancas 
                        WHERE asaas_customer_id = :customer_id 
                        AND ABS(valor - :valor) < 1 
                        AND status = 'pendente'
                        AND asaas_payment_id IS NULL
                        LIMIT 1
                    """),
                    {"customer_id": customer_id, "valor": float(valor)}
                )
                cobranca_match = result.fetchone()
            
            if cobranca_match:
                db.execute(
                    text("""
                        UPDATE cobrancas SET 
                            status = 'pago',
                            valor_pago = :valor_pago,
                            data_pagamento = :data_pagamento,
                            forma_pagamento = :forma_pagamento,
                            asaas_payment_id = :payment_id,
                            data_atualizacao = NOW()
                        WHERE id_cobranca = :id_cobranca
                    """),
                    {
                        "valor_pago": float(valor_pago),
                        "data_pagamento": data_pagamento,
                        "forma_pagamento": forma_pag,
                        "payment_id": asaas_payment_id,
                        "id_cobranca": cobranca_match[0]
                    }
                )
                resultado["pagamento_atualizado"] = True
                resultado["id_cobranca"] = cobranca_match[0]
            else:
                # Criar nova cobrança
                db.execute(
                    text("""
                        INSERT INTO cobrancas (
                            id_condominio, valor, valor_pago, data_vencimento, data_pagamento,
                            status, forma_pagamento, descricao, asaas_payment_id, asaas_customer_id,
                            data_criacao
                        ) VALUES (
                            :id_condominio, :valor, :valor_pago, :data_vencimento, :data_pagamento,
                            'pago', :forma_pagamento, :descricao, :payment_id, :customer_id, NOW()
                        )
                    """),
                    {
                        "id_condominio": id_condominio,
                        "valor": float(valor),
                        "valor_pago": float(valor_pago),
                        "data_vencimento": data_vencimento,
                        "data_pagamento": data_pagamento,
                        "forma_pagamento": forma_pag,
                        "descricao": descricao or "Pagamento sincronizado do Asaas",
                        "payment_id": asaas_payment_id,
                        "customer_id": customer_id
                    }
                )
                resultado["pagamento_novo"] = True
        
        # ====================================================================
        # PASSO 3: Criar/atualizar assinatura e validade
        # ====================================================================
        
        # Extrair dias do plano
        dias_validade = extrair_dias_do_plano(descricao)
        resultado["dias_plano"] = dias_validade
        
        # Verificar se existe assinatura para este condomínio
        result = db.execute(
            text("SELECT id_assinatura, validade_ate FROM assinaturas WHERE id_condominio = :id_cond"),
            {"id_cond": id_condominio}
        )
        assinatura = result.fetchone()
        
        if not assinatura:
            # Criar assinatura
            nova_validade = calcular_nova_validade(None, dias_validade, data_pagamento)
            
            db.execute(
                text("""
                    INSERT INTO assinaturas (
                        id_condominio, tipo_plano, valor, ciclo, data_inicio, 
                        validade_ate, status, renovacao_automatica, data_criacao
                    ) VALUES (
                        :id_cond, :tipo_plano, :valor, 'MONTHLY', :data_inicio,
                        :validade_ate, 'ativa', 1, NOW()
                    )
                """),
                {
                    "id_cond": id_condominio,
                    "tipo_plano": descricao or f"Plano {dias_validade} dias",
                    "valor": float(valor),
                    "data_inicio": date.today().isoformat(),
                    "validade_ate": nova_validade.isoformat()
                }
            )
            
            resultado["assinatura_criada"] = True
            resultado["nova_validade"] = nova_validade.isoformat()
            
            # Buscar assinatura recém criada
            result = db.execute(
                text("SELECT id_assinatura, validade_ate FROM assinaturas WHERE id_condominio = :id_cond"),
                {"id_cond": id_condominio}
            )
            assinatura = result.fetchone()
            
            logger.info(f"✅ Assinatura criada - {condominio_nome}: validade até {nova_validade}")
        
        # Atualizar validade da assinatura
        if assinatura:
            validade_atual = assinatura[1]
            nova_validade = calcular_nova_validade(validade_atual, dias_validade, data_pagamento)
            
            # Só atualiza se a nova validade for maior que a atual
            if not validade_atual or nova_validade > validade_atual:
                db.execute(
                    text("""
                        UPDATE assinaturas 
                        SET validade_ate = :validade, 
                            tipo_plano = :tipo_plano,
                            valor = :valor,
                            data_renovacao = :data_renovacao,
                            data_atualizacao = NOW()
                        WHERE id_condominio = :id_cond
                    """),
                    {
                        "validade": nova_validade.isoformat(),
                        "tipo_plano": descricao or f"Plano {dias_validade} dias",
                        "valor": float(valor),
                        "data_renovacao": date.today().isoformat(),
                        "id_cond": id_condominio
                    }
                )
                
                # Atualizar também no condomínio
                db.execute(
                    text("UPDATE condominios SET validade_ate = :validade WHERE id = :id_cond"),
                    {"validade": nova_validade.isoformat(), "id_cond": id_condominio}
                )
                
                resultado["validade_atualizada"] = True
                resultado["validade_anterior"] = validade_atual.isoformat() if validade_atual else None
                resultado["nova_validade"] = nova_validade.isoformat()
                
                logger.info(f"✅ Validade atualizada - {condominio_nome}: {validade_atual} → {nova_validade}")
        
        return resultado
        
    except Exception as e:
        resultado["erro"] = str(e)
        logger.error(f"Erro ao processar pagamento {pagamento.get('id')}: {str(e)}")
        return resultado


# ================================================================================
#  ENDPOINT PARA LISTAR CONDOMÍNIOS COM VALIDADE
# ================================================================================

@router.get("/condominios-com-validade")
async def listar_condominios_com_validade(db: Session = Depends(get_db)):
    """
    Lista todos os condomínios com informações de validade da assinatura.
    """
    try:
        query = text("""
            SELECT 
                c.id,
                c.nome,
                c.cnpj,
                c.endereco,
                c.numero,
                c.bairro,
                c.cidade,
                c.estado,
                c.telefone,
                c.email,
                c.sindico,
                c.total_apartamentos,
                c.ativo,
                c.validade_ate as cond_validade,
                c.asaas_customer_id,
                a.validade_ate as assin_validade,
                a.tipo_plano,
                a.valor as valor_plano,
                a.status as status_assinatura,
                COALESCE(a.validade_ate, c.validade_ate) as validade_final,
                DATEDIFF(COALESCE(a.validade_ate, c.validade_ate), CURDATE()) as dias_restantes
            FROM condominios c
            LEFT JOIN assinaturas a ON a.id_condominio = c.id
            ORDER BY c.nome
        """)
        
        result = db.execute(query).fetchall()
        
        condominios = []
        for row in result:
            validade = row[19]  # validade_final
            dias = row[20]  # dias_restantes
            
            # Determinar status da validade
            if validade:
                if dias is not None:
                    if dias < 0:
                        status_validade = "vencida"
                    elif dias <= 7:
                        status_validade = "vence_em_breve"
                    elif dias <= 15:
                        status_validade = "atencao"
                    else:
                        status_validade = "ok"
                else:
                    status_validade = "indefinido"
            else:
                status_validade = "sem_assinatura"
            
            condominios.append({
                "id": row[0],
                "nome": row[1],
                "cnpj": row[2],
                "endereco": row[3],
                "numero": row[4],
                "bairro": row[5],
                "cidade": row[6],
                "estado": row[7],
                "telefone": row[8],
                "email": row[9],
                "sindico": row[10],
                "total_apartamentos": row[11],
                "ativo": bool(row[12]),
                "asaas_customer_id": row[14],
                "validade_ate": row[19].isoformat() if row[19] else None,
                "dias_restantes": dias,
                "status_validade": status_validade,
                "tipo_plano": row[16],
                "valor_plano": float(row[17]) if row[17] else None,
                "status_assinatura": row[18]
            })
        
        return {
            "items": condominios,
            "total": len(condominios)
        }
        
    except Exception as e:
        logger.error(f"Erro ao listar condomínios: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
#  ALIAS PARA COMPATIBILIDADE - /pagamentos/sincronizar
# ================================================================================

@router.post("/pagamentos/sincronizar")
async def sincronizar_pagamentos_alias(
    buscar_todos: bool = Query(True, description="Buscar todos os pagamentos"),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    ALIAS para /sync/pagamentos - Mantido para compatibilidade com frontend existente.
    """
    return await sincronizar_pagamentos_completo(
        buscar_todos=buscar_todos,
        data_inicio=data_inicio,
        data_fim=data_fim,
        db=db
    )
