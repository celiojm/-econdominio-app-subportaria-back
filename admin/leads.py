# ============================================================================
# ARQUIVO: leads.py
# PASTA: /home/visionlpr/app_subportaria_back/admin/
# DESCRIÇÃO: Rotas AUTENTICADAS de gestão de leads (funil /conheca do site +
#            WhatsApp direto) — listar/filtrar e alterar status/observação.
#            Tabela `leads` (AdmGeral), separada de `contato_condominios`.
#            Mesmo padrão de autenticação de admin/operadores.py.
# VERSÃO: 1.0.0
# data criação: 2026-09-15   data alteração: 2026-09-15
# ============================================================================

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, ConfigDict

from .database import get_db_connection
from .auth import get_current_user

router = APIRouter()

STATUS_VALIDOS = ("novo", "contatado", "em_negociacao", "convertido", "perdido")


class AtualizarLeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[str] = None
    observacao: Optional[str] = None


@router.get("/leads")
async def listar_leads(
    status: Optional[str] = Query(None),
    origem: Optional[str] = Query(None),
    utm_campaign: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "admin_sistema":
        raise HTTPException(status_code=403, detail="Apenas admin_sistema pode ver leads")

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if status and status not in STATUS_VALIDOS:
                raise HTTPException(status_code=400, detail="status inválido")

            where_parts = []
            args = []
            if status:
                where_parts.append("status = %s")
                args.append(status)
            if origem:
                where_parts.append("origem = %s")
                args.append(origem)
            if utm_campaign:
                where_parts.append("utm_campaign = %s")
                args.append(utm_campaign)
            if data_inicio:
                where_parts.append("DATE(criado_em) >= %s")
                args.append(data_inicio)
            if data_fim:
                where_parts.append("DATE(criado_em) <= %s")
                args.append(data_fim)

            where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

            cursor.execute(f"SELECT COUNT(*) AS total FROM leads {where_sql}", args)
            total = cursor.fetchone()["total"]

            offset = (page - 1) * limit
            cursor.execute(
                f"""
                SELECT id, nome, whatsapp, cnpj, razao_social, cidade, origem, status,
                       utm_source, utm_medium, utm_campaign, utm_content, ref,
                       total_contatos, ultimo_contato_em, observacao, criado_em,
                       -- 2026-10-02: cliente mandou mensagem e nenhuma PESSOA respondeu depois (robô não conta)
                       CASE WHEN whatsapp_chat_id IS NULL THEN 0 ELSE COALESCE(
                         (SELECT MAX(m.enviado_em) FROM leads_mensagens m WHERE m.chat_id = leads.whatsapp_chat_id AND m.from_me = 0)
                         > COALESCE((SELECT MAX(m.enviado_em) FROM leads_mensagens m WHERE m.chat_id = leads.whatsapp_chat_id
                                     AND m.from_me = 1 AND m.bot = 0), '1970-01-01'), 0) END AS aguardando_resposta
                FROM leads {where_sql}
                ORDER BY ultimo_contato_em DESC
                LIMIT %s OFFSET %s
                """,
                args + [limit, offset],
            )
            leads = cursor.fetchall()

            return {
                "data": [
                    {
                        "id": l["id"],
                        "nome": l["nome"],
                        "whatsapp": l["whatsapp"],
                        "cnpj": l["cnpj"],
                        "razao_social": l["razao_social"],
                        "cidade": l["cidade"],
                        "origem": l["origem"],
                        "status": l["status"],
                        "utm_source": l["utm_source"],
                        "utm_medium": l["utm_medium"],
                        "utm_campaign": l["utm_campaign"],
                        "utm_content": l["utm_content"],
                        "ref": l["ref"],
                        "total_contatos": l["total_contatos"],
                        "ultimo_contato_em": str(l["ultimo_contato_em"]) if l["ultimo_contato_em"] else None,
                        "observacao": l["observacao"],
                        "criado_em": str(l["criado_em"]) if l["criado_em"] else None,
                        "aguardando_resposta": bool(l.get("aguardando_resposta")),
                    }
                    for l in leads
                ],
                "total": total,
                "page": page,
                "limit": limit,
            }
    finally:
        conn.close()


@router.patch("/leads/{lead_id}")
async def atualizar_lead(
    lead_id: int,
    data: AtualizarLeadRequest,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "admin_sistema":
        raise HTTPException(status_code=403, detail="Apenas admin_sistema pode alterar leads")

    if data.status is not None and data.status not in STATUS_VALIDOS:
        raise HTTPException(status_code=400, detail="status inválido")

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, observacao FROM leads WHERE id = %s", (lead_id,))
            lead = cursor.fetchone()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")

            campos = []
            valores = []

            if data.status is not None:
                campos.append("status = %s")
                valores.append(data.status)

            if data.observacao:
                operador_nome = current_user.get("nome") or current_user.get("email", "")
                observacao_atual = lead.get("observacao") or ""
                nova_observacao = (
                    observacao_atual + f"\n[nota de {operador_nome}] {data.observacao}"
                ).strip()
                campos.append("observacao = %s")
                valores.append(nova_observacao)

            if not campos:
                return {"success": True, "message": "Nada a atualizar"}

            valores.append(lead_id)
            cursor.execute(f"UPDATE leads SET {', '.join(campos)} WHERE id = %s", valores)
            conn.commit()

        return {"success": True}
    finally:
        conn.close()


# ─── 2026-10-02: conversa do lead pelo painel + agendamento (só admin_sistema) ──────────
class _RespostaLead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mensagem: str


class _AgendaLead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_agendamento: str          # AAAA-MM-DDTHH:MM
    anotacao: Optional[str] = None


def _so_master(current_user):
    if current_user.get("role") != "admin_sistema":
        raise HTTPException(status_code=403, detail="Apenas admin_sistema")
    return current_user.get("nome") or current_user.get("username") or "admin"


@router.get("/leads/{lead_id}/conversa")
async def conversa_lead(lead_id: int, current_user: dict = Depends(get_current_user)):
    _so_master(current_user)
    from app.services.leads_whatsapp import conversa
    return conversa(lead_id)


@router.get("/leads/{lead_id}/midia/{msg_id}")
async def midia_lead(lead_id: int, msg_id: str, current_user: dict = Depends(get_current_user)):
    _so_master(current_user)
    from app.services.leads_whatsapp import midia
    return midia(lead_id, msg_id)


@router.post("/leads/{lead_id}/responder")
async def responder_lead(lead_id: int, dados: _RespostaLead, current_user: dict = Depends(get_current_user)):
    operador = _so_master(current_user)
    from app.services.leads_whatsapp import responder
    return responder(lead_id, dados.mensagem, operador)


@router.post("/leads/{lead_id}/agendar")
async def agendar_lead(lead_id: int, dados: _AgendaLead, current_user: dict = Depends(get_current_user)):
    operador = _so_master(current_user)
    from app.services.leads_whatsapp import agendar
    return agendar(lead_id, dados.data_agendamento, dados.anotacao, operador)


@router.post("/leads")
async def criar_lead_manual(dados: dict, current_user: dict = Depends(get_current_user)):
    """2026-10-02: cadastro manual de lead (só admin_sistema)."""
    operador = _so_master(current_user)
    from app.services.leads_whatsapp import criar_manual
    return criar_manual(dados, operador)
