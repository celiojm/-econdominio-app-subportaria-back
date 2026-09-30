# ================================================================================
# ALTERAÇÃO 2026-09-26: cobrança sempre BOLETO (boleto + PIX na mesma fatura), nunca só PIX
#  PATH: backend/financeiro/financeiro_cobrancas.py
#  DESCRIPTION: Gerenciamento de cobranças - Asaas + Banco Local
#  VERSÃO: 2.1 - CORRIGIDO: email fallback removido, usa sempre .env como remetente
# ================================================================================

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date, timedelta
from pydantic import BaseModel, Field
from typing import Optional, List
import httpx
import os
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# ================================================================================
#  CONFIGURAÇÃO ASAAS
# ================================================================================

ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "")
ASAAS_BASE_URL = os.getenv("ASAAS_URL_PRODUCTION", "https://api.asaas.com/v3")
ASAAS_SANDBOX = os.getenv("ASAAS_ENV", "production").lower() == "sandbox"

if ASAAS_SANDBOX:
    ASAAS_BASE_URL = os.getenv("ASAAS_URL_SANDBOX", "https://api-sandbox.asaas.com/v3")
    logger.info("🧪 Modo SANDBOX ativado")
else:
    logger.info(f"💳 Asaas PRODUCAO: {ASAAS_BASE_URL}")


def get_asaas_headers():
    """Retorna headers para API do Asaas"""
    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json"
    }


# ================================================================================
#  MODELOS PYDANTIC
# ================================================================================

class CriarCobrancaRequest(BaseModel):
    id_condominio: int
    valor: float = Field(..., gt=0, description="Valor da cobrança")
    data_vencimento: str = Field(..., description="Data de vencimento (YYYY-MM-DD)")
    descricao: Optional[str] = Field(None, description="Descrição da cobrança")
    forma_pagamento: str = Field("BOLETO", description="Ignorado: cobrança é sempre BOLETO (boleto + PIX)")

    class Config:
        json_schema_extra = {
            "example": {
                "id_condominio": 7,
                "valor": 59.00,
                "data_vencimento": "2026-01-15",
                "descricao": "Mensalidade Janeiro 2026",
                "forma_pagamento": "PIX"
            }
        }


class AtualizarCobrancaRequest(BaseModel):
    status: Optional[str] = None
    data_pagamento: Optional[str] = None
    valor_pago: Optional[float] = None
    forma_pagamento: Optional[str] = None


# ================================================================================
#  FUNÇÕES AUXILIARES
# ================================================================================

async def buscar_ou_criar_cliente_asaas(condominio: dict, db: Session) -> str:
    """
    Busca ou cria um cliente no Asaas.
    Retorna o customer_id do Asaas.

    IMPORTANTE: O email do cliente no Asaas é o email de CONTATO/COBRANÇA do
    condomínio (destinatário). O remetente dos emails é SEMPRE o .env
    (financeiro@econdominio.com.br), nunca o email do condomínio.

    Se o condomínio não tiver email cadastrado, cria o cliente sem email —
    o Asaas aceita clientes sem email.
    """
    try:
        # Verificar se já tem customer_id no banco
        result = db.execute(
            text("SELECT asaas_customer_id FROM condominios WHERE id = :id"),
            {"id": condominio["id"]}
        )
        row = result.fetchone()

        if row and row.asaas_customer_id:
            logger.info(f"✅ Cliente já existe no Asaas: {row.asaas_customer_id}")
            return row.asaas_customer_id

        # Criar cliente no Asaas
        logger.info(f"📝 Criando cliente no Asaas: {condominio['nome']}")

        # ✅ CORRIGIDO: Usa email do condomínio APENAS como destinatário (campo do cliente
        # no Asaas). NÃO gera email fictício — se não tiver, envia sem email no cadastro.
        # O remetente dos emails é sempre EMAIL_FROM_ADDRESS do .env.
        email_cliente = (
            condominio.get('cobranca_email') or
            condominio.get('email_financeiro') or
            condominio.get('email') or
            None  # ✅ Sem fallback fictício — Asaas aceita cliente sem email
        )

        if not email_cliente:
            logger.warning(
                f"⚠️ Condomínio {condominio['nome']} (id={condominio['id']}) "
                f"não possui email cadastrado. Cliente será criado no Asaas sem email."
            )

        # Telefone: limpar caracteres especiais
        telefone = condominio.get('cobranca_whats') or condominio.get('telefone') or ""
        telefone_limpo = ''.join(filter(str.isdigit, telefone))

        payload = {
            "name": condominio["nome"],
            "cpfCnpj": condominio.get("cnpj", "").replace(".", "").replace("/", "").replace("-", ""),
            "phone": telefone_limpo[:11] if telefone_limpo else None,
            "postalCode": condominio.get("cep", "").replace("-", ""),
            "address": condominio.get("endereco", ""),
            "addressNumber": condominio.get("numero", ""),
            "complement": condominio.get("complemento", ""),
            "province": condominio.get("bairro", ""),
            "externalReference": f"COND_{condominio['id']}"
        }

        # Só inclui email se existir — evita erro 550 de domínio inexistente
        if email_cliente:
            payload["email"] = email_cliente

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{ASAAS_BASE_URL}/customers",
                headers=get_asaas_headers(),
                json=payload
            )

            if response.status_code in [200, 201]:
                data = response.json()
                customer_id = data.get("id")

                # Salvar customer_id no banco
                db.execute(
                    text("UPDATE condominios SET asaas_customer_id = :customer_id WHERE id = :id"),
                    {"customer_id": customer_id, "id": condominio["id"]}
                )
                db.commit()

                logger.info(f"✅ Cliente criado no Asaas: {customer_id}")
                return customer_id
            else:
                logger.error(f"❌ Erro ao criar cliente no Asaas: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Erro ao criar cliente no Asaas: {response.text}"
                )

    except Exception as e:
        logger.error(f"❌ Erro ao buscar/criar cliente: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao processar cliente: {str(e)}")


def gerar_link_pagamento(asaas_payment_id: str) -> str:
    """Gera link de pagamento do Asaas"""
    if not asaas_payment_id:
        return None

    # Remover prefixo 'pay_'
    asaas_id = asaas_payment_id.replace('pay_', '')
    return f"https://www.asaas.com/i/{asaas_id}"


# ================================================================================
#  ROTAS DE COBRANÇAS
# ================================================================================

@router.post("/cobrancas")
async def criar_cobranca(
    dados: CriarCobrancaRequest,
    db: Session = Depends(get_db)
):
    """
    Cria uma nova cobrança no Asaas E salva no banco local.
    Após criar, envia email automático para o contato de cobrança do condomínio.
    O remetente é SEMPRE financeiro@econdominio.com.br (via .env).
    """
    try:
        # Buscar condomínio
        from app.models.condominio import Condominio
        condominio = db.query(Condominio).filter(Condominio.id == dados.id_condominio).first()

        if not condominio:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        logger.info(f"📝 Criando cobrança para {condominio.nome}")

        # Converter condomínio para dict
        cond_dict = {
            'id': condominio.id,
            'nome': condominio.nome,
            'cnpj': condominio.cnpj,
            'email': getattr(condominio, 'email', ''),
            'email_financeiro': getattr(condominio, 'email_financeiro', None),
            'cobranca_email': getattr(condominio, 'cobranca_email', None),
            'telefone': getattr(condominio, 'telefone', ''),
            'cobranca_whats': getattr(condominio, 'cobranca_whats', None),
            'cep': getattr(condominio, 'cep', ''),
            'endereco': getattr(condominio, 'endereco', ''),
            'numero': getattr(condominio, 'numero', ''),
            'complemento': getattr(condominio, 'complemento', ''),
            'bairro': getattr(condominio, 'bairro', ''),
        }

        # Buscar ou criar cliente no Asaas
        customer_id = await buscar_ou_criar_cliente_asaas(cond_dict, db)

        # Mapear forma de pagamento
        billing_type_map = {
            "BOLETO": "BOLETO",
            "PIX": "PIX",
            "CREDIT_CARD": "CREDIT_CARD",
            "UNDEFINED": "UNDEFINED"
        }
        billing_type = "BOLETO"  # regra: sempre boleto + PIX, nunca só PIX

        # Preparar payload para Asaas
        payload = {
            "customer": customer_id,
            "billingType": billing_type,
            "value": dados.valor,
            "dueDate": dados.data_vencimento,
            "description": dados.descricao or f"Cobrança - {condominio.nome}",
            "externalReference": f"COND_{dados.id_condominio}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        }

        logger.info(f"🚀 Enviando cobrança para Asaas...")

        # Criar cobrança no Asaas
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{ASAAS_BASE_URL}/payments",
                headers=get_asaas_headers(),
                json=payload
            )

            if response.status_code in [200, 201]:
                data = response.json()

                logger.info(f"✅ Cobrança criada no Asaas: {data.get('id')}")

                # ================================================================
                # SALVAR COBRANÇA NO BANCO LOCAL
                # ================================================================

                asaas_payment_id = data.get("id")
                invoice_url = data.get("invoiceUrl")
                bank_slip_url = data.get("bankSlipUrl")

                id_cobranca_local = None
                try:
                    logger.info(f"💾 Salvando cobrança no banco local...")

                    sql_insert = text("""
                        INSERT INTO cobrancas (
                            id_condominio,
                            valor,
                            data_vencimento,
                            status,
                            descricao,
                            asaas_payment_id,
                            asaas_customer_id,
                            forma_pagamento,
                            data_criacao,
                            data_atualizacao
                        ) VALUES (
                            :id_condominio,
                            :valor,
                            :data_vencimento,
                            'pendente',
                            :descricao,
                            :asaas_payment_id,
                            :customer_id,
                            :forma_pagamento,
                            NOW(),
                            NOW()
                        )
                    """)

                    result_insert = db.execute(sql_insert, {
                        "id_condominio": dados.id_condominio,
                        "valor": dados.valor,
                        "data_vencimento": dados.data_vencimento,
                        "descricao": dados.descricao or f"Cobrança - {condominio.nome}",
                        "asaas_payment_id": asaas_payment_id,
                        "customer_id": customer_id,
                        "forma_pagamento": billing_type.lower()
                    })

                    db.commit()
                    id_cobranca_local = result_insert.lastrowid

                    logger.info(f"✅ Cobrança salva no banco! ID local: {id_cobranca_local}, ID Asaas: {asaas_payment_id}")

                except Exception as e:
                    logger.error(f"❌ Erro ao salvar cobrança no banco: {e}")
                    # NÃO fazer rollback — a cobrança JÁ FOI criada no Asaas
                    import traceback
                    traceback.print_exc()

                # ================================================================
                # ENVIAR EMAIL AUTOMÁTICO (remetente sempre do .env)
                # ================================================================

                link_pagamento = gerar_link_pagamento(asaas_payment_id)

                # Destinatário: email de cobrança do condomínio
                email_destino = (
                    cond_dict.get('cobranca_email') or
                    cond_dict.get('email_financeiro') or
                    cond_dict.get('email') or
                    None
                )

                email_enviado = False
                if email_destino and link_pagamento:
                    try:
                        from .financeiro_email import enviar_email_nova_cobranca

                        vencimento_fmt = datetime.strptime(
                            dados.data_vencimento, "%Y-%m-%d"
                        ).strftime("%d/%m/%Y")

                        resultado_email = enviar_email_nova_cobranca(
                            email_destino=email_destino,       # ← TO: email do condomínio
                            nome_condominio=condominio.nome,
                            valor=dados.valor,
                            vencimento=vencimento_fmt,
                            descricao=dados.descricao or f"Cobrança - {condominio.nome}",
                            link_pagamento=link_pagamento
                            # from_email vem do EMAIL_CONFIG (.env): financeiro@econdominio.com.br
                        )

                        if resultado_email.get("success"):
                            email_enviado = True
                            logger.info(f"📧 Email enviado para {email_destino}")
                        else:
                            logger.warning(f"⚠️ Email não enviado: {resultado_email.get('message')}")

                    except Exception as e:
                        logger.error(f"❌ Erro ao enviar email automático: {e}")
                else:
                    if not email_destino:
                        logger.warning(
                            f"⚠️ Cobrança criada mas email NÃO enviado: "
                            f"condomínio {condominio.nome} sem email de cobrança cadastrado."
                        )

                # ================================================================
                # RETORNO
                # ================================================================

                return {
                    "success": True,
                    "message": "Cobrança criada com sucesso",
                    "email_enviado": email_enviado,
                    "email_destino": email_destino,
                    "cobranca": {
                        "id": asaas_payment_id,
                        "id_local": id_cobranca_local,
                        "customer": customer_id,
                        "value": data.get("value"),
                        "dueDate": data.get("dueDate"),
                        "status": data.get("status"),
                        "billingType": data.get("billingType"),
                        "invoiceUrl": invoice_url,
                        "bankSlipUrl": bank_slip_url,
                        "linkPagamento": link_pagamento,
                        "description": data.get("description")
                    }
                }
            else:
                logger.error(f"❌ Erro ao criar cobrança no Asaas: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Erro ao criar cobrança no Asaas: {response.text}"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao criar cobrança: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao criar cobrança: {str(e)}")


@router.get("/cobrancas")
async def listar_cobrancas(
    id_condominio: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Lista cobranças do banco local
    """
    try:
        where_clauses = []
        params = {"limit": limit, "offset": offset}

        if id_condominio:
            where_clauses.append("c.id_condominio = :id_condominio")
            params["id_condominio"] = id_condominio

        if status:
            where_clauses.append("c.status = :status")
            params["status"] = status

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        sql = text(f"""
            SELECT
                c.id_cobranca,
                c.asaas_payment_id,
                c.id_condominio,
                c.valor,
                c.valor_pago,
                c.data_vencimento,
                c.data_pagamento,
                c.status,
                c.forma_pagamento,
                c.descricao,
                c.data_criacao,
                cond.nome as nome_condominio
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE {where_sql}
            ORDER BY c.data_criacao DESC
            LIMIT :limit OFFSET :offset
        """)

        result = db.execute(sql, params)
        cobrancas = result.fetchall()

        lista = []
        for c in cobrancas:
            lista.append({
                "id_cobranca": c.id_cobranca,
                "asaas_payment_id": c.asaas_payment_id,
                "id_condominio": c.id_condominio,
                "nome_condominio": c.nome_condominio,
                "valor": float(c.valor) if c.valor else 0,
                "valor_pago": float(c.valor_pago) if c.valor_pago else None,
                "data_vencimento": c.data_vencimento.strftime("%Y-%m-%d") if c.data_vencimento else None,
                "data_pagamento": c.data_pagamento.strftime("%Y-%m-%d") if c.data_pagamento else None,
                "status": c.status,
                "forma_pagamento": c.forma_pagamento,
                "descricao": c.descricao,
                "link_pagamento": gerar_link_pagamento(c.asaas_payment_id),
                "data_criacao": c.data_criacao.strftime("%Y-%m-%d %H:%M:%S") if c.data_criacao else None
            })

        count_sql = text(f"""
            SELECT COUNT(*) as total
            FROM cobrancas c
            WHERE {where_sql}
        """)

        total = db.execute(count_sql, params).fetchone().total

        return {
            "success": True,
            "total": total,
            "limit": limit,
            "offset": offset,
            "cobrancas": lista
        }

    except Exception as e:
        logger.error(f"❌ Erro ao listar cobranças: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cobrancas/{id_cobranca}")
async def buscar_cobranca(id_cobranca: str, db: Session = Depends(get_db)):
    """
    Busca uma cobrança específica (por ID local ou ID Asaas)
    """
    try:
        sql = text("""
            SELECT
                c.*,
                cond.nome as nome_condominio,
                cond.cobranca_email,
                cond.email_financeiro,
                cond.email,
                cond.cobranca_whats,
                cond.telefone
            FROM cobrancas c
            JOIN condominios cond ON cond.id = c.id_condominio
            WHERE c.asaas_payment_id = :id OR c.id_cobranca = :id_int
            LIMIT 1
        """)

        result = db.execute(sql, {
            "id": id_cobranca,
            "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
        })

        cobranca = result.fetchone()

        if not cobranca:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada")

        return {
            "success": True,
            "cobranca": {
                "id_cobranca": cobranca.id_cobranca,
                "asaas_payment_id": cobranca.asaas_payment_id,
                "id_condominio": cobranca.id_condominio,
                "nome_condominio": cobranca.nome_condominio,
                "valor": float(cobranca.valor),
                "valor_pago": float(cobranca.valor_pago) if cobranca.valor_pago else None,
                "data_vencimento": cobranca.data_vencimento.strftime("%Y-%m-%d") if cobranca.data_vencimento else None,
                "data_pagamento": cobranca.data_pagamento.strftime("%Y-%m-%d") if cobranca.data_pagamento else None,
                "status": cobranca.status,
                "forma_pagamento": cobranca.forma_pagamento,
                "descricao": cobranca.descricao,
                "link_pagamento": gerar_link_pagamento(cobranca.asaas_payment_id),
                "email": cobranca.cobranca_email or cobranca.email_financeiro or cobranca.email,
                "whatsapp": cobranca.cobranca_whats or cobranca.telefone
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao buscar cobrança: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/cobrancas/{id_cobranca}")
async def atualizar_cobranca(
    id_cobranca: str,
    dados: AtualizarCobrancaRequest,
    db: Session = Depends(get_db)
):
    """
    Atualiza uma cobrança no banco local
    """
    try:
        updates = []
        params = {}

        if dados.status:
            updates.append("status = :status")
            params["status"] = dados.status

        if dados.data_pagamento:
            updates.append("data_pagamento = :data_pagamento")
            params["data_pagamento"] = dados.data_pagamento

        if dados.valor_pago is not None:
            updates.append("valor_pago = :valor_pago")
            params["valor_pago"] = dados.valor_pago

        if dados.forma_pagamento:
            updates.append("forma_pagamento = :forma_pagamento")
            params["forma_pagamento"] = dados.forma_pagamento

        if not updates:
            raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

        updates.append("data_atualizacao = NOW()")

        params["id"] = id_cobranca
        params["id_int"] = int(id_cobranca) if id_cobranca.isdigit() else 0

        sql = text(f"""
            UPDATE cobrancas
            SET {", ".join(updates)}
            WHERE asaas_payment_id = :id OR id_cobranca = :id_int
        """)

        result = db.execute(sql, params)
        db.commit()

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada")

        logger.info(f"✅ Cobrança atualizada: {id_cobranca}")

        return {
            "success": True,
            "message": "Cobrança atualizada com sucesso"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao atualizar cobrança: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cobrancas/condominio/{id_condominio}/historico")
async def historico_cobrancas(id_condominio: int, db: Session = Depends(get_db)):
    """
    Retorna histórico de cobranças de um condomínio
    """
    try:
        sql = text("""
            SELECT
                c.id_cobranca,
                c.asaas_payment_id,
                c.valor,
                c.valor_pago,
                c.data_vencimento,
                c.data_pagamento,
                c.status,
                c.forma_pagamento,
                c.descricao,
                c.data_criacao,
                c.asaas_invoice_id,
                c.invoice_status,
                c.invoice_pdf_url
            FROM cobrancas c

            WHERE c.id_condominio = :id_condominio
            ORDER BY c.data_criacao DESC
            LIMIT 50
        """)

        result = db.execute(sql, {"id_condominio": id_condominio})
        cobrancas = result.fetchall()

        stats = {
            "total": 0,
            "pagas": 0,
            "pendentes": 0,
            "vencidas": 0,
            "valor_total": 0,
            "valor_recebido": 0
        }

        lista = []
        hoje = date.today()

        for c in cobrancas:
            stats["total"] += 1
            stats["valor_total"] += float(c.valor) if c.valor else 0

            if c.status in ['pago', 'RECEIVED', 'CONFIRMED']:
                stats["pagas"] += 1
                stats["valor_recebido"] += float(c.valor_pago or c.valor or 0)
            elif c.data_vencimento and c.data_vencimento < hoje and c.status == 'pendente':
                stats["vencidas"] += 1
            else:
                stats["pendentes"] += 1

            lista.append({
                "id_cobranca": c.id_cobranca,
                "asaas_payment_id": c.asaas_payment_id,
                "valor": float(c.valor) if c.valor else 0,
                "valor_pago": float(c.valor_pago) if c.valor_pago else None,
                "data_vencimento": c.data_vencimento.strftime("%Y-%m-%d") if c.data_vencimento else None,
                "data_pagamento": c.data_pagamento.strftime("%Y-%m-%d") if c.data_pagamento else None,
                "status": c.status,
                "forma_pagamento": c.forma_pagamento,
                "descricao": c.descricao,
                "asaas_invoice_id": c.asaas_invoice_id,
                "invoice_status": c.invoice_status,
                "invoice_pdf_url": c.invoice_pdf_url,
                "link_pagamento": gerar_link_pagamento(c.asaas_payment_id),
            })

        return {
            "success": True,
            "id_condominio": id_condominio,
            "estatisticas": stats,
            "cobrancas": lista
        }

    except Exception as e:
        logger.error(f"❌ Erro ao buscar histórico: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
#  ROTA DE STATUS
# ================================================================================

@router.get("/cobrancas/status")
async def status_cobrancas():
    """Retorna status do sistema de cobranças"""
    return {
        "status": "ok",
        "asaas_base_url": ASAAS_BASE_URL,
        "sandbox": ASAAS_SANDBOX,
        "api_key_configured": bool(ASAAS_API_KEY),
        "timestamp": datetime.now().isoformat()
    }


# ================================================================================
#  CANCELAMENTO E ESTORNO
# ================================================================================

@router.delete("/cobrancas/{id_cobranca}")
async def cancelar_cobranca(id_cobranca: str, db: Session = Depends(get_db)):
    """
    Cancela uma cobrança PENDENTE no Asaas e atualiza status no banco
    """
    try:
        result = db.execute(text("""
            SELECT id_cobranca, asaas_payment_id, status, valor, id_condominio
            FROM cobrancas
            WHERE asaas_payment_id = :id OR id_cobranca = :id_int
            LIMIT 1
        """), {
            "id": id_cobranca,
            "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
        })

        cobranca = result.fetchone()

        if not cobranca:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada")

        status_pagos = ['RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH', 'pago']
        if cobranca.status.upper() in [s.upper() for s in status_pagos]:
            raise HTTPException(
                status_code=400,
                detail="Cobrança já está paga. Use a rota de estorno."
            )

        asaas_id = cobranca.asaas_payment_id

        if not asaas_id:
            raise HTTPException(status_code=400, detail="Cobrança sem ID do Asaas")

        logger.info(f"🗑️ Cancelando cobrança {asaas_id} no Asaas...")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.delete(
                f"{ASAAS_BASE_URL}/payments/{asaas_id}",
                headers=get_asaas_headers()
            )

            if response.status_code in [200, 204]:
                logger.info(f"✅ Cobrança cancelada no Asaas: {asaas_id}")

                db.execute(text("""
                    UPDATE cobrancas
                    SET status = 'cancelada', data_atualizacao = NOW()
                    WHERE asaas_payment_id = :id OR id_cobranca = :id_int
                """), {
                    "id": id_cobranca,
                    "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
                })
                db.commit()

                return {
                    "success": True,
                    "message": "Cobrança cancelada com sucesso",
                    "id_cobranca": cobranca.id_cobranca,
                    "asaas_payment_id": asaas_id
                }
            elif response.status_code == 404:
                logger.warning(f"⚠️ Cobrança não encontrada no Asaas, atualizando apenas banco")
                db.execute(text("""
                    UPDATE cobrancas
                    SET status = 'cancelada', data_atualizacao = NOW()
                    WHERE asaas_payment_id = :id OR id_cobranca = :id_int
                """), {
                    "id": id_cobranca,
                    "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
                })
                db.commit()

                return {
                    "success": True,
                    "message": "Cobrança cancelada (não encontrada no Asaas)",
                    "id_cobranca": cobranca.id_cobranca
                }
            else:
                logger.error(f"❌ Erro ao cancelar no Asaas: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Erro ao cancelar no Asaas: {response.text}"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao cancelar cobrança: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao cancelar: {str(e)}")


@router.post("/cobrancas/{id_cobranca}/estornar")
async def estornar_cobranca(id_cobranca: str, db: Session = Depends(get_db)):
    """
    Estorna uma cobrança PAGA no Asaas e atualiza status no banco
    """
    try:
        result = db.execute(text("""
            SELECT id_cobranca, asaas_payment_id, status, valor, id_condominio
            FROM cobrancas
            WHERE asaas_payment_id = :id OR id_cobranca = :id_int
            LIMIT 1
        """), {
            "id": id_cobranca,
            "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
        })

        cobranca = result.fetchone()

        if not cobranca:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada")

        status_pagos = ['RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH', 'pago']
        if cobranca.status.upper() not in [s.upper() for s in status_pagos]:
            raise HTTPException(
                status_code=400,
                detail="Apenas cobranças pagas podem ser estornadas. Use cancelamento."
            )

        asaas_id = cobranca.asaas_payment_id

        if not asaas_id:
            raise HTTPException(status_code=400, detail="Cobrança sem ID do Asaas")

        logger.info(f"💸 Estornando cobrança {asaas_id} no Asaas...")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{ASAAS_BASE_URL}/payments/{asaas_id}/refund",
                headers=get_asaas_headers(),
                json={"value": float(cobranca.valor)}
            )

            if response.status_code in [200, 201]:
                data = response.json()
                logger.info(f"✅ Cobrança estornada no Asaas: {asaas_id}")

                db.execute(text("""
                    UPDATE cobrancas
                    SET status = 'estornada',
                        data_atualizacao = NOW(),
                        observacoes = CONCAT(COALESCE(observacoes, ''), ' | Estornado em ', NOW())
                    WHERE asaas_payment_id = :id OR id_cobranca = :id_int
                """), {
                    "id": id_cobranca,
                    "id_int": int(id_cobranca) if id_cobranca.isdigit() else 0
                })
                db.commit()

                return {
                    "success": True,
                    "message": "Cobrança estornada com sucesso",
                    "id_cobranca": cobranca.id_cobranca,
                    "asaas_payment_id": asaas_id,
                    "valor_estornado": float(cobranca.valor),
                    "refund_id": data.get("id")
                }
            else:
                logger.error(f"❌ Erro ao estornar no Asaas: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Erro ao estornar no Asaas: {response.text}"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao estornar cobrança: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao estornar: {str(e)}")
