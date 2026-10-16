# ============================================================================
# ARQUIVO: ver_encomenda.py
# PASTA: app/api/ (dev2_back e producao)
# DESCRIÇÃO: rota PÚBLICA do botão "Ver encomenda" da mensagem de chegada no WhatsApp
#            (template recebimento_encomenda_link_ver). GET /api/ver-encomenda/{token} → dados da
#            encomenda + caminho da foto da etiqueta. O token vem no link; no banco fica só o hash
#            sha256 (encomendas.token_visualizacao), gerado pelo send_grouped_whatsapp.py, com validade
#            (token_validade_ate). Código de retirada só enquanto a encomenda está pendente.
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-16 data alteração: 2026-10-16
# ============================================================================
import hashlib
import logging
import re

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter()

TOKEN_VALIDO = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
INVALIDO = "Link inválido ou expirado."


def _data(d):
    return d.strftime("%d/%m/%Y %H:%M") if d else None


@router.get("/{token}")
def ver_encomenda(token: str):
    if not TOKEN_VALIDO.match(token):
        raise HTTPException(status_code=404, detail=INVALIDO)
    db = SessionLocal()
    try:
        e = db.execute(text("""
            SELECT e.id, e.nome_destinatario, e.apartamento, e.bloco, e.codigo_rastreio, e.remetente,
                   e.status, e.data_recebimento, e.data_entrega, e.nome_retirou, e.local_armazenamento,
                   e.codigo_retirada, COALESCE(NULLIF(e.img_etiqueta_server, ''), NULLIF(e.img_etiqueta, '')) AS foto,
                   c.nome AS condominio, m.nome AS morador
              FROM encomendas e
              JOIN condominios c ON c.id = e.condominio_id
              LEFT JOIN moradores m ON m.id = e.morador_id
             WHERE e.token_visualizacao = :h AND e.token_validade_ate > NOW()
        """), {"h": hashlib.sha256(token.encode()).hexdigest()}).mappings().first()
    finally:
        db.close()
    if not e:
        raise HTTPException(status_code=404, detail=INVALIDO)

    logger.info("VER_ENCOMENDA aberta | encomenda_id=%s | status=%s", e["id"], e["status"])
    pendente = e["status"] == "pendente"
    unidade = " ".join(p for p in (
        f"Bloco {e['bloco']}" if (e["bloco"] or "").strip() else "",
        f"Apto {e['apartamento']}" if (e["apartamento"] or "").strip() else "",
    ) if p)
    return {
        "condominio": e["condominio"],
        "morador": e["morador"] or e["nome_destinatario"],
        "unidade": unidade,
        "codigo_rastreio": e["codigo_rastreio"],
        "remetente": e["remetente"],
        "status": e["status"],
        "recebida_em": _data(e["data_recebimento"]),
        "entregue_em": _data(e["data_entrega"]),
        "retirada_por": e["nome_retirou"] if e["status"] == "entregue" else None,
        "local_retirada": (e["local_armazenamento"] or "Portaria") if pendente else None,
        "codigo_retirada": e["codigo_retirada"] if pendente else None,
        "foto": f"/api/storage/image/{e['foto']}" if e["foto"] else None,
    }
