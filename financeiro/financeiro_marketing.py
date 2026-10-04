# =============================================================
# Arquivo  : financeiro_marketing.py
# Caminho  : ~/desenvolvimento/financeiro/financeiro_marketing.py
# Criado   : 2026-03-29  |  v3 2026-03-29
# -------------------------------------------------------------
# Finalidade:
#   Rotas FastAPI do modulo de Marketing e Prospeccao.
#   Leads de condominios, prestadoras e sindicos profissionais,
#   historico de contatos e importacao em lote.
# -------------------------------------------------------------
# CORRECAO v3:
#   Adicionados validators Pydantic que convertem string vazia ''
#   para None nos campos Optional[int] e Optional[str].
#   O frontend manda campos nao preenchidos como '' (string vazia)
#   o que causava erro 422 de validacao no Pydantic.
# -------------------------------------------------------------
# Padrao do projeto: from app.database import get_db
#                    db: Session = Depends(get_db)
#                    db.execute(text(...))
# -------------------------------------------------------------
# Rotas (prefixo /api/financeiro em financeiro_rotas.py):
#   GET    /marketing/leads
#   GET    /marketing/leads/estatisticas
#   POST   /marketing/leads/importar
#   POST   /marketing/leads
#   GET    /marketing/leads/{lead_id}
#   PUT    /marketing/leads/{lead_id}
#   DELETE /marketing/leads/{lead_id}
#   POST   /marketing/leads/{lead_id}/contatos
# -------------------------------------------------------------
# Tabelas: marketing_leads, marketing_contatos
# =============================================================

import math
import logging
from app.services.protecao_financeiro import nome_usuario_atual  # 2026-10-04: colaborador logado
from datetime import datetime
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Marketing"])


# =============================================================================
# HELPERS DE VALIDACAO — convertem '' do frontend para None
# =============================================================================

def _to_int(v: Any) -> Optional[int]:
    """String vazia ou None viram None; demais valores tentam int."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_str(v: Any) -> Optional[str]:
    """String vazia vira None."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


# =============================================================================
# SCHEMAS
# =============================================================================

class LeadCreate(BaseModel):
    tipo_lead:            str           = "condominio"
    nome:                 str
    nome_fantasia:        Optional[str] = None
    cnpj_cpf:             Optional[str] = None
    responsavel:          Optional[str] = None
    cargo:                Optional[str] = None
    whatsapp:             Optional[str] = None
    email:                Optional[str] = None
    telefone:             Optional[str] = None
    cidade:               Optional[str] = None
    estado:               Optional[str] = None
    total_unidades:       Optional[int] = None
    num_condominios:      Optional[int] = None
    ramo_atividade:       Optional[str] = None
    origem:               str           = "manual"
    status:               str           = "novo"
    temperatura:          str           = "frio"
    observacoes:          Optional[str] = None
    data_proximo_contato: Optional[str] = None

    # Campos int: aceita '' vindo do frontend e converte para None
    @validator("total_unidades", "num_condominios", pre=True, always=True)
    def val_int(cls, v):
        return _to_int(v)

    # Campos str opcionais: '' vira None
    @validator(
        "nome_fantasia", "cnpj_cpf", "responsavel", "cargo",
        "whatsapp", "email", "telefone", "cidade", "estado",
        "ramo_atividade", "observacoes", "data_proximo_contato",
        pre=True, always=True
    )
    def val_str(cls, v):
        return _to_str(v)


class LeadUpdate(LeadCreate):
    nome: Optional[str] = None

    @validator("nome", pre=True, always=True)
    def val_nome(cls, v):
        return _to_str(v)


class ContatoCreate(BaseModel):
    tipo_contato:      str           = "whatsapp"
    canal_enviado:     Optional[str] = None
    mensagem_texto:    Optional[str] = None
    assunto:           Optional[str] = None
    descricao:         str
    resultado:         str           = "sem_resposta"
    observacoes:       Optional[str] = None
    pessoa_contactada: Optional[str] = None
    operador_nome:     Optional[str] = None
    data_agendamento:  Optional[str] = None

    @validator(
        "canal_enviado", "mensagem_texto", "assunto", "observacoes",
        "pessoa_contactada", "operador_nome", "data_agendamento",
        pre=True, always=True
    )
    def val_str_contato(cls, v):
        return _to_str(v)


class ImportarLeadsBody(BaseModel):
    leads: list[dict]


# =============================================================================
# HELPERS DE BANCO
# =============================================================================

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _row_to_dict(row, keys: list) -> dict:
    d = dict(zip(keys, row))
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _fetch_one(db: Session, sql: str, params: dict) -> Optional[dict]:
    result = db.execute(text(sql), params)
    keys = list(result.keys())
    row  = result.fetchone()
    return _row_to_dict(row, keys) if row else None


def _fetch_all(db: Session, sql: str, params: dict) -> list:
    result = db.execute(text(sql), params)
    keys = list(result.keys())
    return [_row_to_dict(r, keys) for r in result.fetchall()]


def _atualizar_status_lead(db: Session, lead_id: int, resultado: str):
    progressao = {
        "interessado":     (("novo", "em_contato"),                "interessado", "morno"),
        "agendado":        (("novo", "em_contato", "interessado"), "proposta",    "quente"),
        "convertido":      (None,                                  "convertido",  "quente"),
        "nao_interessado": (None,                                  None,          "frio"),
    }
    prog = progressao.get(resultado)
    if not prog:
        return
    status_triggers, novo_status, nova_temp = prog

    row = db.execute(
        text("SELECT status, temperatura FROM marketing_leads WHERE id = :id"),
        {"id": lead_id}
    ).fetchone()
    if not row:
        return

    status_atual, temp_atual = row[0], row[1]
    campos, vals = [], {"id": lead_id}

    if nova_temp and temp_atual != nova_temp:
        campos.append("temperatura = :nova_temp")
        vals["nova_temp"] = nova_temp
    if novo_status and (status_triggers is None or status_atual in status_triggers):
        if status_atual != novo_status:
            campos.append("status = :novo_status")
            vals["novo_status"] = novo_status

    if campos:
        db.execute(
            text(f"UPDATE marketing_leads SET {', '.join(campos)}, updated_at = NOW() WHERE id = :id"),
            vals
        )


# =============================================================================
# GET /marketing/leads
# =============================================================================

@router.get("/marketing/leads")
async def listar_leads(
    page:        int           = Query(1,  ge=1),
    limit:       int           = Query(15, ge=1, le=100),
    status:      Optional[str] = Query(None),
    tipo_lead:   Optional[str] = Query(None),
    temperatura: Optional[str] = Query(None),
    busca:       Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        conds  = ["1=1"]
        params: dict = {}

        if status:
            conds.append("l.status = :status")
            params["status"] = status
        if tipo_lead:
            conds.append("l.tipo_lead = :tipo_lead")
            params["tipo_lead"] = tipo_lead
        if temperatura:
            conds.append("l.temperatura = :temperatura")
            params["temperatura"] = temperatura
        if busca:
            conds.append(
                "(l.nome LIKE :busca OR l.responsavel LIKE :busca "
                "OR l.cidade LIKE :busca OR l.email LIKE :busca "
                "OR l.whatsapp LIKE :busca)"
            )
            params["busca"] = f"%{busca}%"

        where = " AND ".join(conds)

        total = db.execute(
            text(f"SELECT COUNT(*) FROM marketing_leads l WHERE {where}"),
            params
        ).scalar() or 0

        params["lim"] = limit
        params["off"] = (page - 1) * limit

        data = _fetch_all(db, f"""
            SELECT
                l.id, l.tipo_lead, l.nome, l.nome_fantasia, l.cnpj_cpf,
                l.responsavel, l.cargo, l.whatsapp, l.email, l.telefone,
                l.cidade, l.estado, l.total_unidades, l.num_condominios,
                l.ramo_atividade, l.origem, l.status, l.temperatura,
                l.observacoes, l.data_proximo_contato,
                l.created_at, l.updated_at,
                COALESCE(c.total_contatos, 0) AS total_contatos,
                c.ultimo_contato_em,
                c.ultimo_resultado
            FROM marketing_leads l
            LEFT JOIN (
                SELECT
                    lead_id,
                    COUNT(*)        AS total_contatos,
                    MAX(created_at) AS ultimo_contato_em,
                    (SELECT resultado FROM marketing_contatos mc2
                     WHERE mc2.lead_id = mc.lead_id
                     ORDER BY mc2.created_at DESC LIMIT 1) AS ultimo_resultado
                FROM marketing_contatos mc
                GROUP BY lead_id
            ) c ON c.lead_id = l.id
            WHERE {where}
            ORDER BY l.updated_at DESC
            LIMIT :lim OFFSET :off
        """, params)

        return {
            "success":     True,
            "data":        data,
            "total":       total,
            "total_pages": math.ceil(total / limit) if total > 0 else 1,
            "page":        page,
        }
    except Exception as e:
        logger.error(f"[marketing] listar_leads: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# GET /marketing/leads/estatisticas  — ANTES de /{lead_id}
# =============================================================================

@router.get("/marketing/leads/estatisticas")
async def estatisticas(db: Session = Depends(get_db)):
    try:
        por_status = {r[0]: r[1] for r in db.execute(
            text("SELECT status, COUNT(*) FROM marketing_leads GROUP BY status")
        ).fetchall()}
        por_temp = {r[0]: r[1] for r in db.execute(
            text("SELECT temperatura, COUNT(*) FROM marketing_leads GROUP BY temperatura")
        ).fetchall()}
        por_tipo = {r[0]: r[1] for r in db.execute(
            text("SELECT tipo_lead, COUNT(*) FROM marketing_leads GROUP BY tipo_lead")
        ).fetchall()}
        total_interacoes = db.execute(
            text("SELECT COUNT(*) FROM marketing_contatos")
        ).scalar() or 0

        return {
            "success": True,
            "data": {
                "total":            sum(por_status.values()),
                "total_interacoes": total_interacoes,
                "novos":            por_status.get("novo",        0),
                "em_contato":       por_status.get("em_contato",  0),
                "interessados":     por_status.get("interessado",  0),
                "proposta":         por_status.get("proposta",     0),
                "negociacao":       por_status.get("negociacao",   0),
                "convertidos":      por_status.get("convertido",   0),
                "descartados":      por_status.get("descartado",   0),
                "quentes":          por_temp.get("quente", 0),
                "mornos":           por_temp.get("morno",  0),
                "frios":            por_temp.get("frio",   0),
                "por_tipo": {
                    "condominio":           por_tipo.get("condominio",           0),
                    "prestadora":           por_tipo.get("prestadora",           0),
                    "sindico_profissional": por_tipo.get("sindico_profissional", 0),
                },
            }
        }
    except Exception as e:
        logger.error(f"[marketing] estatisticas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /marketing/leads/importar  — ANTES de /{lead_id}
# =============================================================================

@router.post("/marketing/leads/importar")
async def importar_leads(body: ImportarLeadsBody, db: Session = Depends(get_db)):
    importados, erros = 0, []
    for i, lead in enumerate(body.leads):
        nome = (lead.get("nome") or lead.get("razao_social") or "").strip()
        if not nome:
            erros.append({"linha": i + 2, "erro": "Nome vazio"})
            continue
        try:
            db.execute(text("""
                INSERT INTO marketing_leads
                    (tipo_lead, nome, responsavel, whatsapp, email,
                     cidade, estado, total_unidades, origem, status, temperatura)
                VALUES (:tipo_lead, :nome, :responsavel, :whatsapp, :email,
                        :cidade, :estado, :total_unidades, 'importacao', 'novo', 'frio')
            """), {
                "tipo_lead":      lead.get("tipo_lead", "condominio"),
                "nome":           nome,
                "responsavel":    lead.get("responsavel") or lead.get("sindico") or None,
                "whatsapp":       lead.get("whatsapp") or lead.get("telefone") or None,
                "email":          lead.get("email") or None,
                "cidade":         lead.get("cidade") or None,
                "estado":         (lead.get("estado") or lead.get("uf") or "")[:2].upper() or None,
                "total_unidades": _to_int(lead.get("unidades") or lead.get("apartamentos")),
            })
            importados += 1
        except Exception as e:
            erros.append({"linha": i + 2, "nome": nome, "erro": str(e)})

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "success":       True,
        "importados":    importados,
        "erros":         len(erros),
        "erros_detalhe": erros or None,
    }


# =============================================================================
# POST /marketing/leads
# =============================================================================

@router.post("/marketing/leads")
async def criar_lead(body: LeadCreate, db: Session = Depends(get_db)):
    try:
        result = db.execute(text("""
            INSERT INTO marketing_leads
                (tipo_lead, nome, nome_fantasia, cnpj_cpf,
                 responsavel, cargo, whatsapp, email, telefone,
                 cidade, estado, total_unidades, num_condominios,
                 ramo_atividade, origem, status, temperatura,
                 observacoes, data_proximo_contato)
            VALUES
                (:tipo_lead, :nome, :nome_fantasia, :cnpj_cpf,
                 :responsavel, :cargo, :whatsapp, :email, :telefone,
                 :cidade, :estado, :total_unidades, :num_condominios,
                 :ramo_atividade, :origem, :status, :temperatura,
                 :observacoes, :data_proximo_contato)
        """), {
            "tipo_lead":            body.tipo_lead,
            "nome":                 body.nome,
            "nome_fantasia":        body.nome_fantasia,
            "cnpj_cpf":             body.cnpj_cpf,
            "responsavel":          body.responsavel,
            "cargo":                body.cargo,
            "whatsapp":             body.whatsapp,
            "email":                body.email,
            "telefone":             body.telefone,
            "cidade":               body.cidade,
            "estado":               body.estado,
            "total_unidades":       body.total_unidades,
            "num_condominios":      body.num_condominios,
            "ramo_atividade":       body.ramo_atividade,
            "origem":               body.origem,
            "status":               body.status,
            "temperatura":          body.temperatura,
            "observacoes":          body.observacoes,
            "data_proximo_contato": _parse_dt(body.data_proximo_contato),
        })
        lead_id = result.lastrowid
        db.commit()

        lead = _fetch_one(db, "SELECT * FROM marketing_leads WHERE id = :id", {"id": lead_id})
        return {"success": True, "data": lead or {"id": lead_id}}

    except Exception as e:
        db.rollback()
        logger.error(f"[marketing] criar_lead: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# GET /marketing/leads/{lead_id}
# =============================================================================

@router.get("/marketing/leads/{lead_id}")
async def detalhe_lead(lead_id: int, db: Session = Depends(get_db)):
    try:
        lead = _fetch_one(db, "SELECT * FROM marketing_leads WHERE id = :id", {"id": lead_id})
        if not lead:
            raise HTTPException(status_code=404, detail="Lead nao encontrado")

        lead["contatos"] = _fetch_all(
            db,
            "SELECT * FROM marketing_contatos WHERE lead_id = :id ORDER BY created_at DESC",
            {"id": lead_id}
        )
        return {"success": True, "data": lead}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[marketing] detalhe_lead {lead_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# PUT /marketing/leads/{lead_id}
# =============================================================================

@router.put("/marketing/leads/{lead_id}")
async def editar_lead(lead_id: int, body: LeadUpdate, db: Session = Depends(get_db)):
    try:
        if not db.execute(
            text("SELECT id FROM marketing_leads WHERE id = :id"), {"id": lead_id}
        ).fetchone():
            raise HTTPException(status_code=404, detail="Lead nao encontrado")

        dados = {k: v for k, v in body.dict().items() if v is not None}
        if not dados:
            raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")
        if "data_proximo_contato" in dados:
            dados["data_proximo_contato"] = _parse_dt(dados["data_proximo_contato"])

        dados["lead_id"] = lead_id
        campos = [f"{k} = :{k}" for k in dados if k != "lead_id"]
        db.execute(
            text(f"UPDATE marketing_leads SET {', '.join(campos)}, updated_at = NOW() WHERE id = :lead_id"),
            dados
        )
        db.commit()

        lead = _fetch_one(db, "SELECT * FROM marketing_leads WHERE id = :id", {"id": lead_id})
        return {"success": True, "data": lead}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[marketing] editar_lead {lead_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# DELETE /marketing/leads/{lead_id}
# =============================================================================

@router.delete("/marketing/leads/{lead_id}")
async def excluir_lead(lead_id: int, db: Session = Depends(get_db)):
    try:
        if not db.execute(
            text("SELECT id FROM marketing_leads WHERE id = :id"), {"id": lead_id}
        ).fetchone():
            raise HTTPException(status_code=404, detail="Lead nao encontrado")

        db.execute(text("DELETE FROM marketing_leads WHERE id = :id"), {"id": lead_id})
        db.commit()
        return {"success": True, "message": "Lead excluido"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[marketing] excluir_lead {lead_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /marketing/leads/{lead_id}/contatos
# =============================================================================

@router.post("/marketing/leads/{lead_id}/contatos")
async def registrar_contato(lead_id: int, body: ContatoCreate, db: Session = Depends(get_db)):
    try:
        if not db.execute(
            text("SELECT id FROM marketing_leads WHERE id = :id"), {"id": lead_id}
        ).fetchone():
            raise HTTPException(status_code=404, detail="Lead nao encontrado")

        result = db.execute(text("""
            INSERT INTO marketing_contatos
                (lead_id, tipo_contato, canal_enviado, mensagem_texto,
                 assunto, descricao, resultado, observacoes,
                 pessoa_contactada, operador_nome, data_agendamento)
            VALUES
                (:lead_id, :tipo_contato, :canal_enviado, :mensagem_texto,
                 :assunto, :descricao, :resultado, :observacoes,
                 :pessoa_contactada, :operador_nome, :data_agendamento)
        """), {
            "lead_id":           lead_id,
            "tipo_contato":      body.tipo_contato,
            "canal_enviado":     body.canal_enviado,
            "mensagem_texto":    body.mensagem_texto,
            "assunto":           body.assunto,
            "descricao":         body.descricao,
            "resultado":         body.resultado,
            "observacoes":       body.observacoes,
            "pessoa_contactada": body.pessoa_contactada,
            "operador_nome":     nome_usuario_atual(body.operador_nome),
            "data_agendamento":  _parse_dt(body.data_agendamento),
        })
        contato_id = result.lastrowid

        _atualizar_status_lead(db, lead_id, body.resultado)
        db.execute(
            text("UPDATE marketing_leads SET updated_at = NOW() WHERE id = :id"),
            {"id": lead_id}
        )
        db.commit()

        contato = _fetch_one(db, "SELECT * FROM marketing_contatos WHERE id = :id", {"id": contato_id})
        return {"success": True, "data": contato}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[marketing] registrar_contato lead={lead_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
