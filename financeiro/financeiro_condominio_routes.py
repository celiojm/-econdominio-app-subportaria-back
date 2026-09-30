# ============================================================================
# ALTERAÇÃO 2026-09-27: régua de cobrança — GET situacao-assinatura / POST gerar-cobranca (painel)
# ARQUIVO: financeiro_condominio_routes.py
# PASTA: /home/visionlpr/desenvolvimento/financeiro/
# DESCRIÇÃO: Rotas do módulo financeiro para o painel admin do condomínio
# VERSÃO: 1.1.0 - Correção text() SQLAlchemy, prefixo /painel/financeiro
# ============================================================================

import os
import re
import logging
from datetime import datetime, date

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os as _os

_fin_engine = create_engine(
    _os.getenv("DATABASE_URL", ""),
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    pool_pre_ping=True,
)
_FinSession = sessionmaker(autocommit=False, autoflush=False, bind=_fin_engine)

def _get_fin_db():
    db = _FinSession()
    try:
        yield db
    finally:
        db.close()
from app.api.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY", "")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://api.asaas.com/v3")

DESCONTO_POR_CICLO = {
    "mensal":     {"desconto": 0.00, "meses": 1},
    "trimestral": {"desconto": 0.05, "meses": 3},
    "semestral":  {"desconto": 0.10, "meses": 6},
    "anual":      {"desconto": 0.15, "meses": 12},
}

class GerarPrimeiroBoletoRequest(BaseModel):
    periodo: str

def _asaas_headers() -> dict:
    return {"access_token": ASAAS_API_KEY, "Content-Type": "application/json"}

async def _asaas_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{ASAAS_BASE_URL}{path}", headers=_asaas_headers())
        r.raise_for_status()
        return r.json()

async def _asaas_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{ASAAS_BASE_URL}{path}", json=payload, headers=_asaas_headers())
        r.raise_for_status()
        return r.json()


def _resolver_numero_endereco(numero: str, endereco: str) -> str:
    """Numero do condominio costuma vir vazio no cadastro (endereco foi
    digitado tudo junto num campo so, com o numero no final do texto).
    Tenta extrair a sequencia de digitos no fim do endereco antes de cair
    pro fallback 'S/N' — a Asaas exige addressNumber preenchido pra liberar
    emissao de NFS-e, mesmo com os outros campos de endereco completos."""
    numero = (numero or "").strip()
    if numero:
        return numero
    m = re.search(r"(\d+)\s*$", (endereco or "").strip())
    return m.group(1) if m else "S/N"


def _get_condominio(db: Session, condominio_id: int) -> dict:
    row = db.execute(
        text("""
        SELECT id, nome, cnpj, total_apartamentos,
               assinatura_status, validade_ate,
               periodo_bonificado, data_inicio_bonificado, data_fim_bonificado, bonificado,
               valor_mensal_base, valor_plano_final, plano_selecionado,
               forma_pagamento,
               cobranca_responsavel, cobranca_email, cobranca_whats,
               asaas_customer_id,
               cep, endereco, numero, complemento, bairro, cidade, estado
        FROM condominios WHERE id = :id
        """),
        {"id": condominio_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado.")
    return dict(row._mapping)


@router.get("/condominio")
async def get_condominio_financeiro(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(_get_fin_db),
):
    condominio_id = current_user.get("condominio_id") or current_user.get("id_condominio")
    if not condominio_id:
        raise HTTPException(status_code=403, detail="Sem condomínio associado.")
    cond = _get_condominio(db, condominio_id)
    bonificado = False
    if cond.get("bonificado") and cond.get("data_fim_bonificado"):
        fim = cond["data_fim_bonificado"]
        if isinstance(fim, str):
            fim = date.fromisoformat(fim)
        bonificado = fim >= date.today()
    cond["bonificado"] = bonificado
    return cond


@router.get("/cobrancas")
async def get_cobrancas(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(_get_fin_db),
):
    condominio_id = current_user.get("condominio_id") or current_user.get("id_condominio")
    if not condominio_id:
        raise HTTPException(status_code=403, detail="Sem condomínio associado.")
    rows = db.execute(
        text("""
        SELECT id_cobranca, valor, valor_pago, data_vencimento,
               data_pagamento, status, forma_pagamento,
               descricao, referencia,
               asaas_payment_id
        FROM cobrancas
        WHERE id_condominio = :cid
        ORDER BY data_vencimento DESC
        """),
        {"cid": condominio_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/gerar-primeiro-boleto", status_code=status.HTTP_201_CREATED)
async def gerar_primeiro_boleto(
    body: GerarPrimeiroBoletoRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(_get_fin_db),
):
    condominio_id = current_user.get("condominio_id") or current_user.get("id_condominio")
    if not condominio_id:
        raise HTTPException(status_code=403, detail="Sem condomínio associado.")
    if body.periodo not in DESCONTO_POR_CICLO:
        raise HTTPException(status_code=400, detail="Período inválido.")
    cond = _get_condominio(db, condominio_id)
    existente = db.execute(
        text("""
        SELECT c.id_cobranca FROM cobrancas c
        JOIN assinaturas a ON c.id_assinatura = a.id_assinatura
        WHERE a.id_condominio = :cid
          AND c.status NOT IN ('cancelada','CANCELLED')
        LIMIT 1
        """),
        {"cid": condominio_id},
    ).fetchone()
    if existente:
        raise HTTPException(status_code=400, detail="Já existe uma cobrança ativa para este condomínio.")
    valor_base = float(cond.get("valor_mensal_base") or 0)
    if valor_base <= 0:
        raise HTTPException(status_code=400, detail="Valor mensal base não configurado. Contate o administrador.")
    cfg = DESCONTO_POR_CICLO[body.periodo]
    valor_total = round(valor_base * cfg["meses"] * (1 - cfg["desconto"]), 2)
    descricao = f"eCondomínio — plano {body.periodo} ({cond['nome']})"
    due_date = date.today().replace(day=min(date.today().day + 5, 28))
    # ── Criar cliente no Asaas se ainda não existir ──────────────────────────
    if not cond.get("asaas_customer_id"):
        logger.info(f"Condomínio {condominio_id} sem asaas_customer_id — criando no Asaas...")
        try:
            cnpj_limpo = (cond.get("cnpj") or "").replace(".", "").replace("-", "").replace("/", "")
            customer_payload = {
                "name":                 cond["nome"],
                "cpfCnpj":              cnpj_limpo,
                "email":                cond.get("cobranca_email") or cond.get("email") or "",
                "mobilePhone":          cond.get("cobranca_whats") or "",
                "externalReference":    str(condominio_id),
                "notificationDisabled": True,
                "postalCode":           (cond.get("cep") or "").replace("-", ""),
                "address":              cond.get("endereco") or "",
                "addressNumber":        _resolver_numero_endereco(cond.get("numero"), cond.get("endereco")),
                "complement":           cond.get("complemento") or "",
                "province":             cond.get("bairro") or "",
            }
            customer_resp = await _asaas_post("/customers", customer_payload)
            novo_customer_id = customer_resp.get("id")
            if not novo_customer_id:
                raise Exception("Asaas não retornou ID do cliente")
            # Salvar no banco
            db.execute(
                text("UPDATE condominios SET asaas_customer_id = :cid WHERE id = :id"),
                {"cid": novo_customer_id, "id": condominio_id},
            )
            db.commit()
            # Recarregar condomínio com o novo ID
            cond = _get_condominio(db, condominio_id)
            logger.info(f"Cliente criado no Asaas: {novo_customer_id} para condomínio {condominio_id}")
            # ── Desabilitar notificações individuais via batch ────────────────
            try:
                notif_resp = await _asaas_get(f"/customers/{novo_customer_id}/notifications")
                notif_ids = [n["id"] for n in notif_resp.get("data", []) if not n.get("deleted", False)]
                if notif_ids:
                    await _asaas_post("/notifications/batch", {
                        "customer": novo_customer_id,
                        "notifications": [
                            {
                                "id": nid,
                                "enabled": False,
                                "emailEnabledForCustomer":     False,
                                "smsEnabledForCustomer":       False,
                                "whatsappEnabledForCustomer":  False,
                                "phoneCallEnabledForCustomer": False,
                                "emailEnabledForProvider":     False,
                                "smsEnabledForProvider":       False,
                            }
                            for nid in notif_ids
                        ]
                    })
                    logger.info(f"Notificações individuais desabilitadas para {novo_customer_id} ({len(notif_ids)} itens)")
            except Exception as e_notif:
                logger.warning(f"Não foi possível desabilitar notificações individuais: {e_notif}")
                # Não bloqueia o fluxo — notificationDisabled já está True
        except Exception as e:
            logger.error(f"Erro ao criar cliente no Asaas: {e}")
            raise HTTPException(status_code=400, detail="Erro ao criar cliente no Asaas. Tente novamente.")


    asaas_payload = {
        "customer":    cond["asaas_customer_id"],
        "billingType": "BOLETO",
        "value":       valor_total,
        "dueDate":     due_date.isoformat(),
        "description": descricao,
    }
    try:
        asaas_resp = await _asaas_post("/payments", asaas_payload)
    except Exception as e:
        logger.error(f"Erro Asaas ao gerar boleto: {e}")
        raise HTTPException(status_code=400, detail="Erro ao gerar boleto no Asaas.")
    asaas_payment_id = asaas_resp.get("id")
    assinatura = db.execute(
        text("SELECT id_assinatura FROM assinaturas WHERE id_condominio = :cid LIMIT 1"),
        {"cid": condominio_id},
    ).fetchone()
    if not assinatura:
        db.execute(
            text("""
            INSERT INTO assinaturas
              (id_condominio, tipo_plano, valor, ciclo, data_inicio, status,
               renovacao_automatica, asaas_customer_id, data_criacao, data_atualizacao)
            VALUES
              (:cid, :tipo, :valor, :ciclo, CURDATE(), 'ativa',
               1, :asaas_cid, NOW(), NOW())
            """),
            {
                "cid": condominio_id, "tipo": body.periodo,
                "valor": valor_total, "ciclo": body.periodo,
                "asaas_cid": cond["asaas_customer_id"],
            },
        )
        db.commit()
        assinatura = db.execute(
            text("SELECT id_assinatura FROM assinaturas WHERE id_condominio = :cid LIMIT 1"),
            {"cid": condominio_id},
        ).fetchone()
    id_assinatura = assinatura[0]
    db.execute(
        text("""
        INSERT INTO cobrancas
          (id_assinatura, id_condominio, valor, data_vencimento, status,
           forma_pagamento, descricao, asaas_payment_id, asaas_customer_id,
           data_criacao, data_atualizacao)
        VALUES
          (:id_ass, :cid, :valor, :venc, 'pendente',
           'boleto', :desc, :asaas_pid, :asaas_cid, NOW(), NOW())
        """),
        {
            "id_ass": id_assinatura, "cid": condominio_id,
            "valor": valor_total, "venc": due_date.isoformat(),
            "desc": descricao, "asaas_pid": asaas_payment_id,
            "asaas_cid": cond["asaas_customer_id"],
        },
    )
    db.commit()
    nova_cobranca = db.execute(
        text("SELECT * FROM cobrancas WHERE asaas_payment_id = :pid"),
        {"pid": asaas_payment_id},
    ).fetchone()
    cobranca_dict = dict(nova_cobranca._mapping)
    _enfileirar_notificacao_boleto(db, condominio_id, cond, cobranca_dict, valor_total, due_date, body.periodo)
    return cobranca_dict


def _enfileirar_notificacao_boleto(db, condominio_id, cond, cobranca, valor, vencimento, periodo):
    import json
    telefone = cond.get("cobranca_whats") or cond.get("telefone")
    if not telefone:
        return
    mensagem = (
        f"📄 *Boleto gerado — eCondomínio*\n\n"
        f"Condomínio: {cond['nome']}\n"
        f"Plano: {periodo.capitalize()}\n"
        f"Valor: R$ {valor:,.2f}\n"
        f"Vencimento: {vencimento.strftime('%d/%m/%Y')}\n\n"
        f"Acesse o painel para visualizar e baixar o boleto:\n"
        f"https://admin.econdominio.com.br"
    )
    try:
        db.execute(
            text("""
            INSERT INTO whatsapp_message_queue
              (telefone, tipo_evento, condominio_id, status, payload_json, tentativas, criado_em)
            VALUES
              (:tel, 'MENSAGEM_GENERICA', :cid, 'pending', :payload, 0, NOW())
            """),
            {"tel": telefone, "cid": condominio_id, "payload": json.dumps({"tipo": "texto_simples", "texto": mensagem})},
        )
        db.commit()
        logger.info(f"Notificação de boleto enfileirada para {telefone}")
    except Exception as e:
        logger.warning(f"Não foi possível enfileirar notificação WhatsApp: {e}")


@router.get("/boleto/{payment_id}/pdf")
async def baixar_boleto_pdf(payment_id: str, current_user: dict = Depends(get_current_user)):
    try:
        data = await _asaas_get(f"/payments/{payment_id}")
        slip_url = data.get("bankSlipUrl") or data.get("invoiceUrl")
        if not slip_url:
            raise HTTPException(status_code=404, detail="URL do boleto não disponível.")
        return {"url": slip_url}


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar boleto {payment_id}: {e}")
        raise HTTPException(status_code=502, detail="Erro ao obter boleto.")
@router.get("/nota-fiscal/{payment_id}")
async def baixar_nota_fiscal(payment_id: str, current_user: dict = Depends(get_current_user)):
    try:
        data = await _asaas_get(f"/invoices?payment={payment_id}")
        invoices = data.get("data") or []
        if not invoices:
            raise HTTPException(status_code=404, detail="Nota fiscal ainda não disponível.")
        nf_url = invoices[0].get("pdfUrl")
        if not nf_url:
            raise HTTPException(status_code=404, detail="URL da nota fiscal não encontrada.")
        return {"url": nf_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar NF {payment_id}: {e}")
        raise HTTPException(status_code=502, detail="Erro ao obter nota fiscal.")


@router.get("/situacao-assinatura")
async def situacao_assinatura_painel(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(_get_fin_db),
):
    """Mesmo retorno do /mobile/assinatura/verificar, para o condomínio do usuário logado no painel."""
    condominio_id = current_user.get("condominio_id") or current_user.get("id_condominio")
    if not condominio_id:
        return {"situacao": None}
    from mobile.assinatura_routes import verificar_assinatura
    return await verificar_assinatura(int(condominio_id), db)


@router.post("/gerar-cobranca")
async def gerar_cobranca_painel(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(_get_fin_db),
):
    """Boleto+PIX de 30 dias para o condomínio do usuário logado (mesmo fluxo do app mobile)."""
    condominio_id = current_user.get("condominio_id") or current_user.get("id_condominio")
    if not condominio_id:
        raise HTTPException(status_code=403, detail="Sem condomínio associado.")
    from mobile.assinatura_routes import GerarCobrancaRequest, gerar_cobranca
    return await gerar_cobranca(int(condominio_id), GerarCobrancaRequest(dias_validade=30), db, operador=None)
