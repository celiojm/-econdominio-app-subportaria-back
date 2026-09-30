# ============================================================================
# ARQUIVO: prospeccao_routes.py
# PASTA: /home/visionlpr/backend/financeiro/
# DESCRIÇÃO: Rotas de prospecção em lote via Meta API (envio direto, sem fila)
# VERSÃO: 1.1.0 - Fix import auth financeiro
# Criado: 2026-05-24   Alterado: 2026-05-24
# ============================================================================

import os
import logging
import requests
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db

router = APIRouter()
logger = logging.getLogger("app.prospeccao")

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_ID     = os.getenv("META_PHONE_NUMBER_ID", "1002371749631777")
META_API_VERSION  = os.getenv("META_API_VERSION", "v19.0")
PROSPECCAO_IMAGE_ID = "1896700254623648"

class EnvioLoteRequest(BaseModel):
    lead_ids: List[int]
    template_name: str = "econdominio_prospeccao"
    flow_json: Optional[dict] = None
    image_media_id: Optional[str] = None

def _enviar_template_prospeccao(telefone: str, template_name: str, image_media_id: str = None) -> dict:
    numero = telefone.strip().replace("+","").replace(" ","").replace("-","")
    if not numero.startswith("55"):
        numero = "55" + numero
    url = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "image",
                            "image": {"id": image_media_id or PROSPECCAO_IMAGE_ID}
                        }
                    ]
                }
            ]
        },
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        data = r.json()
        if r.status_code == 200 and "messages" in data:
            return {"success": True, "message_id": data["messages"][0].get("id")}
        erro = data.get("error", {}).get("message", str(data))
        logger.warning("Meta API erro para %s: %s", numero, erro)
        return {"success": False, "erro": erro}
    except Exception as e:
        logger.error("Excecao ao enviar para %s: %s", numero, str(e))
        return {"success": False, "erro": str(e)}

def _registrar_contato(db, lead_id, operador, obs):
    db.execute(text("""
        INSERT INTO marketing_contatos
            (lead_id, tipo_contato, canal_enviado, assunto, descricao,
             resultado, pessoa_contactada, created_at)
        VALUES
            (:lead_id, 'whatsapp', 'whatsapp', 'Prospeccao eCondominio',
             :obs, 'sem_resposta', :operador, NOW())
    """), {"lead_id": lead_id, "obs": obs, "operador": operador})

@router.get("/api/financeiro/prospeccao/leads-disponiveis")
def leads_disponiveis(
    cidade: Optional[str] = None,
    estado: Optional[str] = None,
    tipo_lead: Optional[str] = None,
    nome: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if nome:
        filtros = ["bloquear_prospeccao = 0", "whatsapp IS NOT NULL AND whatsapp != ''"]
    else:
        filtros = [
            "bloquear_prospeccao = 0",
            "status = 'novo'",
            "whatsapp IS NOT NULL AND whatsapp != ''",
            "prospeccao_enviada_em IS NULL",
        ]
    params = {}
    if cidade:
        filtros.append("cidade LIKE :cidade")
        params["cidade"] = f"%{cidade}%"
    if estado:
        filtros.append("estado = :estado")
        params["estado"] = estado.upper()
    if tipo_lead:
        filtros.append("tipo_lead = :tipo_lead")
        params["tipo_lead"] = tipo_lead
    if nome:
        filtros.append("nome LIKE :nome")
        params["nome"] = f"%{nome}%"
    where = " AND ".join(filtros)
    rows = db.execute(text(f"""
        SELECT id, nome, tipo_lead, responsavel, whatsapp, email,
               cidade, estado, status, temperatura, created_at
        FROM marketing_leads WHERE {where}
        ORDER BY created_at DESC LIMIT 200
    """), params).fetchall()
    leads = [dict(r._mapping) for r in rows]
    for l in leads:
        for k, v in l.items():
            if hasattr(v, "isoformat"):
                l[k] = v.isoformat()
    return {"success": True, "total": len(leads), "data": leads}

@router.post("/api/financeiro/prospeccao/enviar-lote")
def enviar_lote(req: EnvioLoteRequest, db: Session = Depends(get_db)):
    if not req.lead_ids:
        raise HTTPException(status_code=400, detail="Nenhum lead selecionado")
    if not META_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="META_ACCESS_TOKEN nao configurado")
    enviados, pulados, erros = [], [], []
    for lead_id in req.lead_ids:
        row = db.execute(text(
            "SELECT id, nome, whatsapp, bloquear_prospeccao FROM marketing_leads WHERE id = :id"
        ), {"id": lead_id}).fetchone()
        if not row:
            pulados.append({"id": lead_id, "motivo": "Nao encontrado"})
            continue
        if row.bloquear_prospeccao:
            pulados.append({"id": lead_id, "nome": row.nome, "motivo": "Bloqueado"})
            continue
        if not row.whatsapp:
            pulados.append({"id": lead_id, "nome": row.nome, "motivo": "Sem WhatsApp"})
            continue
        resultado = _enviar_template_prospeccao(row.whatsapp, req.template_name, req.image_media_id)
        if resultado["success"]:
            db.execute(text("""
                UPDATE marketing_leads
                SET prospeccao_enviada_em = NOW(),
                    resposta_prospeccao = 'pendente',
                    status = 'em_contato',
                    updated_at = NOW()
                WHERE id = :id
            """), {"id": lead_id})
            _registrar_contato(db, lead_id, "sistema",
                f"Prospeccao enviada. Template: {req.template_name}. msg_id: {resultado.get('message_id','')}")
            enviados.append({"id": lead_id, "nome": row.nome, "whatsapp": row.whatsapp,
                             "message_id": resultado.get("message_id")})
        else:
            erros.append({"id": lead_id, "nome": row.nome, "whatsapp": row.whatsapp,
                           "erro": resultado.get("erro")})
    db.commit()
    return {"success": True, "enviados": len(enviados), "pulados": len(pulados),
            "erros": len(erros), "detalhe_enviados": enviados,
            "detalhe_pulados": pulados, "detalhe_erros": erros}

@router.get("/api/financeiro/prospeccao/interessados")
def interessados(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, nome, tipo_lead, responsavel, whatsapp, email,
               cidade, estado, status, temperatura,
               prospeccao_enviada_em, resposta_prospeccao, updated_at
        FROM marketing_leads
        WHERE status = 'interessado'
        ORDER BY updated_at DESC LIMIT 200
    """)).fetchall()
    leads = [dict(r._mapping) for r in rows]
    for l in leads:
        for k, v in l.items():
            if hasattr(v, "isoformat"):
                l[k] = v.isoformat()
    return {"success": True, "total": len(leads), "data": leads}

@router.get("/api/financeiro/prospeccao/estatisticas")
def estatisticas_prospeccao(db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT
            COUNT(*) AS total,
            SUM(prospeccao_enviada_em IS NOT NULL) AS enviadas,
            SUM(resposta_prospeccao = 'interesse') AS interessados,
            SUM(resposta_prospeccao = 'sem_interesse') AS sem_interesse,
            SUM(bloquear_prospeccao = 1) AS bloqueados,
            SUM(status = 'novo' AND bloquear_prospeccao = 0
                AND whatsapp IS NOT NULL AND whatsapp != ''
                AND prospeccao_enviada_em IS NULL) AS disponiveis
        FROM marketing_leads
    """)).fetchone()
    d = dict(row._mapping)
    return {"success": True, "data": {k: int(v or 0) for k, v in d.items()}}

# =============================================================================
# ENDPOINTS: Imagens de Prospecção
# =============================================================================

from fastapi import UploadFile, File, Form
import shutil

@router.get("/api/financeiro/prospeccao/imagens")
def listar_imagens(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, nome, media_id, filename, ativo, created_at
        FROM prospeccao_imagens
        WHERE ativo = 1
        ORDER BY id ASC
    """)).fetchall()
    imagens = [dict(r._mapping) for r in rows]
    for img in imagens:
        for k, v in img.items():
            if hasattr(v, "isoformat"):
                img[k] = v.isoformat()
    return {"success": True, "data": imagens}

@router.post("/api/financeiro/prospeccao/imagens/upload")
async def upload_imagem(
    nome: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    import os, requests as req_lib
    token    = os.getenv("META_ACCESS_TOKEN", "")
    phone_id = os.getenv("META_PHONE_NUMBER_ID", "1002371749631777")
    version  = os.getenv("META_API_VERSION", "v19.0")

    # Salva temporariamente
    tmp_path = f"/tmp/{file.filename}"
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Upload para Meta
    mime = "image/jpeg" if file.filename.lower().endswith(".jpg") or file.filename.lower().endswith(".jpeg") else "image/png"
    url  = f"https://graph.facebook.com/{version}/{phone_id}/media"
    with open(tmp_path, "rb") as f:
        resp = req_lib.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file":              (file.filename, f, mime),
                "type":              (None, mime),
                "messaging_product": (None, "whatsapp"),
            },
            timeout=60,
        )

    os.unlink(tmp_path)

    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Erro Meta: {resp.text}")

    media_id = resp.json().get("id")
    if not media_id:
        raise HTTPException(status_code=500, detail="media_id nao retornado")

    # Salva no banco
    db.execute(text("""
        INSERT INTO prospeccao_imagens (nome, media_id, filename)
        VALUES (:nome, :media_id, :filename)
    """), {"nome": nome, "media_id": media_id, "filename": file.filename})
    db.commit()

    return {"success": True, "media_id": media_id, "nome": nome, "filename": file.filename}

@router.delete("/api/financeiro/prospeccao/imagens/{img_id}")
def deletar_imagem(img_id: int, db: Session = Depends(get_db)):
    db.execute(text(
        "UPDATE prospeccao_imagens SET ativo = 0 WHERE id = :id"
    ), {"id": img_id})
    db.commit()
    return {"success": True}

# =============================================================================
# CHAT WHATSAPP — histórico e envio de mensagens para leads
# =============================================================================

class ChatMensagemRequest(BaseModel):
    texto: str

@router.get("/api/financeiro/prospeccao/chat/{lead_id}")
def chat_historico(lead_id: int, db: Session = Depends(get_db)):
    """Retorna histórico de mensagens trocadas com o lead."""
    # Busca whatsapp do lead
    lead = db.execute(text(
        "SELECT id, nome, whatsapp FROM marketing_leads WHERE id = :id"
    ), {"id": lead_id}).fetchone()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead nao encontrado")

    whatsapp = (lead[2] or "").replace("+","").replace("-","").replace(" ","")
    if not whatsapp.startswith("55"):
        whatsapp = "55" + whatsapp

    # Busca mensagens recebidas — usa os ultimos 8 digitos para garantir match
    # wa_id pode ser 554884046118, whatsapp pode ser 48984046118 ou 4884046118
    sufixo = whatsapp[-8:] if len(whatsapp) >= 8 else whatsapp
    recebidas = db.execute(text("""
        SELECT id, message_text, message_type, action_taken, created_at
        FROM whatsapp_inbound_messages
        WHERE REPLACE(REPLACE(REPLACE(wa_id,'+',''),'-',''),' ','') LIKE :wa
        ORDER BY created_at ASC
    """), {"wa": f"%{sufixo}"}).fetchall()

    # Busca mensagens enviadas (contatos registrados)
    enviadas = db.execute(text("""
        SELECT id, descricao, created_at
        FROM marketing_contatos
        WHERE lead_id = :lid AND tipo_contato = 'whatsapp'
        ORDER BY created_at DESC
        LIMIT 100
    """), {"lid": lead_id}).fetchall()

    msgs = []
    for r in recebidas:
        msgs.append({
            "id": f"r_{r[0]}",
            "direcao": "recebida",
            "texto": r[1] or "",
            "tipo": r[2] or "text",
            "action": r[3] or "",
            "created_at": r[4].isoformat() if r[4] else "",
        })
    for e in enviadas:
        msgs.append({
            "id": f"e_{e[0]}",
            "direcao": "enviada",
            "texto": e[1] or "",
            "tipo": "text",
            "action": "enviado_painel",
            "created_at": e[2].isoformat() if e[2] else "",
        })

    msgs.sort(key=lambda x: x["created_at"])

    return {
        "success": True,
        "lead": {"id": lead[0], "nome": lead[1], "whatsapp": lead[2]},
        "mensagens": msgs,
        "total": len(msgs),
    }


@router.post("/api/financeiro/prospeccao/chat/{lead_id}")
def chat_enviar(lead_id: int, req: ChatMensagemRequest, db: Session = Depends(get_db)):
    """Envia mensagem de texto para o lead via WhatsApp."""
    if not req.texto.strip():
        raise HTTPException(status_code=400, detail="Texto vazio")

    lead = db.execute(text(
        "SELECT id, nome, whatsapp FROM marketing_leads WHERE id = :id"
    ), {"id": lead_id}).fetchone()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead nao encontrado")
    if not lead[2]:
        raise HTTPException(status_code=400, detail="Lead sem WhatsApp")

    whatsapp = (lead[2] or "").replace("+","").replace("-","").replace(" ","")
    if not whatsapp.startswith("55"):
        whatsapp = "55" + whatsapp

    url = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": whatsapp,
        "type": "text",
        "text": {"body": req.texto.strip()}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        data = r.json()
        if r.status_code == 200 and "messages" in data:
            # Registra como contato
            db.execute(text("""
                INSERT INTO marketing_contatos
                (lead_id, tipo_contato, canal_enviado, assunto, descricao, resultado, created_at)
                VALUES (:lid, 'whatsapp', 'painel_chat', 'Mensagem via chat', :texto, 'sem_resposta', NOW())
            """), {"lid": lead_id, "texto": req.texto.strip()})
            db.commit()
            return {"success": True, "message_id": data["messages"][0].get("id")}
        erro = data.get("error", {}).get("message", str(data))
        raise HTTPException(status_code=400, detail=f"Meta API erro: {erro}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro ao enviar chat para lead %s: %s", lead_id, str(e))
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# ENDPOINT: Leads a responder — cliente enviou msg e painel ainda nao respondeu
# =============================================================================
@router.get("/api/financeiro/prospeccao/a-responder")
def leads_a_responder(db: Session = Depends(get_db)):
    """Lista leads onde o cliente enviou mensagem mais recente que a última resposta do painel."""
    rows = db.execute(text("""
        SELECT 
            ml.id, ml.nome, ml.tipo_lead, ml.whatsapp, ml.cidade, ml.estado,
            ml.status, ml.temperatura, ml.prospeccao_enviada_em, ml.updated_at,
            MAX(w.created_at) as ultima_msg_cliente,
            MAX(mc.created_at) as ultima_resposta_painel
        FROM marketing_leads ml
        INNER JOIN whatsapp_inbound_messages w
            ON REPLACE(REPLACE(REPLACE(w.wa_id,'+',''),'-',''),' ','') 
               LIKE CONCAT('%', RIGHT(REPLACE(REPLACE(ml.whatsapp,'-',''),' ',''), 8))
        LEFT JOIN marketing_contatos mc
            ON mc.lead_id = ml.id AND mc.tipo_contato = 'whatsapp'
        WHERE ml.status IN ('interessado', 'em_contato', 'proposta', 'negociacao')
          AND w.created_at >= COALESCE(ml.prospeccao_enviada_em, DATE_SUB(NOW(), INTERVAL 30 DAY))
        GROUP BY ml.id, ml.nome, ml.tipo_lead, ml.whatsapp, ml.cidade, ml.estado,
                 ml.status, ml.temperatura, ml.prospeccao_enviada_em, ml.updated_at
        HAVING ultima_msg_cliente > COALESCE(ultima_resposta_painel, '2000-01-01')
        ORDER BY ultima_msg_cliente DESC
        LIMIT 100
    """)).fetchall()

    leads = []
    for r in rows:
        d = dict(r._mapping)
        for k, v in d.items():
            if hasattr(v, 'isoformat'):
                d[k] = v.isoformat()
        leads.append(d)

    return {"success": True, "total": len(leads), "data": leads}
