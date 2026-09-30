# ============================================================================
# ARQUIVO: lotes.py
# PASTA: /home/visionlpr/backend/app/api/  (dev2: ~/dev2_back/app/api/)
# DESCRIÇÃO: CRUD e ações de lote de encomendas (feature Subportaria).
#            Abrir, fechar, transferir, conferir, notificar moradores e
#            listar lotes parados (alerta). Ver SUBPORTARIA_DEV2.md
#            seções 2, 6 e 8. NUNCA envia WhatsApp automaticamente — só
#            via POST /{lote_id}/notificar, disparado por clique humano
#            (seção 2.1 do doc, regra central da feature).
# VERSÃO: 0.1.0 - criação inicial, ainda não testado em produção
# data criação: 2026-07-28    data alteração: 2026-07-28
# ============================================================================

import asyncio
import logging
import os
from datetime import datetime, timedelta, time as dt_time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.services.whatsapp import whatsapp_service
from app.api.auth import get_current_user
from app.api.encomendas import _resolve_cond_id, _extract_operador_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Estados que ainda não foram notificados e continuam "no radar" de alerta
# (seção 8.1 do doc — 'conferido' entra na lista, só sai em notificado/cancelado)
STATUS_ATIVOS_ALERTA = ('aberto', 'fechado', 'em_transferencia', 'conferido')


# ============================================================
# HELPERS
# ============================================================
def _lote_to_dict(row, total_atual: Optional[int] = None) -> Dict[str, Any]:
    d = {
        "id_lote": row.id_lote,
        "condominio_id": row.condominio_id,
        "numero": row.numero,
        "status": row.status,
        "operador_abertura_id": row.operador_abertura_id,
        "operador_fechamento_id": row.operador_fechamento_id,
        "operador_origem_id": row.operador_origem_id,
        "operador_destino_id": row.operador_destino_id,
        "total_encomendas": row.total_encomendas,
        "data_abertura": row.data_abertura.isoformat() if row.data_abertura else None,
        "data_fechamento": row.data_fechamento.isoformat() if row.data_fechamento else None,
        "data_transferencia": row.data_transferencia.isoformat() if row.data_transferencia else None,
        "data_conferencia": row.data_conferencia.isoformat() if row.data_conferencia else None,
        "data_envio_msg": row.data_envio_msg.isoformat() if row.data_envio_msg else None,
        "alerta_nivel": row.alerta_nivel,
        "observacoes": row.observacoes,
    }
    if total_atual is not None:
        d["total_atual_lote"] = total_atual
    return d


def _is_master(current_user) -> bool:
    """
    Mesma semântica de 'master' já usada em encomendas.py: _resolve_cond_id
    retorna None quando o usuário é admin_sistema sem filtro (vê tudo).
    """
    return _resolve_cond_id(current_user, None) is None


def _checar_acesso_condominio(current_user, condominio_id: int):
    """Bloqueia acesso cruzado entre condomínios para quem não é master."""
    if _is_master(current_user):
        return
    user_cond_id = _resolve_cond_id(current_user, None)
    if user_cond_id != condominio_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Sem permissão para acessar lotes deste condomínio"
        )


def _log_evento(db: Session, lote_id: int, evento: str, operador_id: Optional[int], detalhe: Optional[str] = None):
    try:
        db.execute(
            text("""
                INSERT INTO lotes_eventos (lote_id, evento, operador_id, detalhe)
                VALUES (:lote_id, :evento, :operador_id, :detalhe)
            """),
            {"lote_id": lote_id, "evento": evento, "operador_id": operador_id, "detalhe": detalhe}
        )
    except Exception as e:
        # Auditoria não deve derrubar a operação principal
        logger.error(f"⚠️ Falha ao gravar lotes_eventos (lote {lote_id}, evento {evento}): {e}")


def _horas_uteis(inicio: datetime, fim: datetime, hora_ini: int = 7, hora_fim: int = 22) -> float:
    """
    Calcula horas decorridas entre `inicio` e `fim`, contando apenas o tempo
    dentro da janela [hora_ini, hora_fim) de cada dia — fora da janela o
    relógio do alerta "pausa" (seção 8.1 do SUBPORTARIA_DEV2.md).
    """
    if fim <= inicio:
        return 0.0
    total_segundos = 0.0
    dia_atual = inicio.date()
    ultimo_dia = fim.date()
    while dia_atual <= ultimo_dia:
        janela_ini = datetime.combine(dia_atual, dt_time(hour=hora_ini))
        janela_fim = datetime.combine(dia_atual, dt_time(hour=hora_fim))
        overlap_ini = max(inicio, janela_ini)
        overlap_fim = min(fim, janela_fim)
        if overlap_fim > overlap_ini:
            total_segundos += (overlap_fim - overlap_ini).total_seconds()
        dia_atual += timedelta(days=1)
    return total_segundos / 3600.0


# ============================================================
# GET /aberto/{condominio_id} — lote aberto atual (ou null)
# ============================================================

@router.get("/aberto/{condominio_id:int}")
async def get_lote_aberto(
    condominio_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _checar_acesso_condominio(current_user, condominio_id)

    cond = db.execute(
        text("SELECT usa_subportaria, envio_whatsapp FROM condominios WHERE id = :cid"),
        {"cid": condominio_id}
    ).fetchone()
    if not cond:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Condomínio não encontrado")
    lote = db.execute(
        text("""
            SELECT * FROM lotes_encomendas
            WHERE condominio_id = :cid AND status = 'aberto'
            ORDER BY id_lote DESC LIMIT 1
        """),
        {"cid": condominio_id}
    ).fetchone()

    total_atual = None
    if lote:
        total_row = db.execute(
            text("SELECT COUNT(*) AS n FROM encomendas WHERE lote_id = :id"),
            {"id": lote.id_lote}
        ).fetchone()
        total_atual = total_row.n

    return {
        "usa_subportaria": bool(cond.usa_subportaria),
        "envio_whatsapp": cond.envio_whatsapp or "S",
        "lote": _lote_to_dict(lote, total_atual) if lote else None,
    }

# ============================================================
# POST /abrir — abre novo lote (idempotente: se já houver um
# aberto, retorna o existente em vez de duplicar)
# ============================================================

@router.post("/abrir")
async def abrir_lote(
    data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    condominio_id = data.get("condominio_id")
    if not condominio_id:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="condominio_id é obrigatório")

    _checar_acesso_condominio(current_user, condominio_id)

    cond = db.execute(
        text("SELECT usa_subportaria FROM condominios WHERE id = :cid"),
        {"cid": condominio_id}
    ).fetchone()
    if not cond:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Condomínio não encontrado")
    if not cond.usa_subportaria:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Este condomínio não está configurado para usar subportaria"
        )

    lote_existente = db.execute(
        text("""
            SELECT * FROM lotes_encomendas
            WHERE condominio_id = :cid AND status = 'aberto'
            ORDER BY id_lote DESC LIMIT 1
        """),
        {"cid": condominio_id}
    ).fetchone()
    if lote_existente:
        total_row = db.execute(
            text("SELECT COUNT(*) AS n FROM encomendas WHERE lote_id = :id"),
            {"id": lote_existente.id_lote}
        ).fetchone()
        return {"message": "Já existe um lote aberto", "lote": _lote_to_dict(lote_existente, total_row.n)}

    operador_id = _extract_operador_id(current_user)

    prox_row = db.execute(
        text("""
            SELECT COALESCE(MAX(numero), 0) + 1 AS prox
            FROM lotes_encomendas WHERE condominio_id = :cid
            FOR UPDATE
        """),
        {"cid": condominio_id}
    ).fetchone()
    prox = prox_row.prox

    novo = db.execute(
        text("""
            INSERT INTO lotes_encomendas (condominio_id, numero, status, operador_abertura_id)
            VALUES (:cid, :numero, 'aberto', :operador_id)
        """),
        {"cid": condominio_id, "numero": prox, "operador_id": operador_id}
    )
    lote_id = novo.lastrowid
    _log_evento(db, lote_id, "abertura", operador_id)
    db.commit()
    lote = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    logger.info(f"📦 Lote {prox} aberto manualmente — condominio={condominio_id} lote_id={lote_id}")
    return {"message": "Lote aberto", "lote": _lote_to_dict(lote, 0)}

# ============================================================
# POST /{lote_id}/fechar
# ============================================================

@router.post("/{lote_id:int}/fechar")
async def fechar_lote(
    lote_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lote = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    if not lote:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Lote não encontrado")

    _checar_acesso_condominio(current_user, lote.condominio_id)

    if lote.status != "aberto":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Lote não pode ser fechado no status atual ({lote.status})"
        )

    total_row = db.execute(
        text("SELECT COUNT(*) AS n FROM encomendas WHERE lote_id = :id"),
        {"id": lote_id}
    ).fetchone()
    total = total_row.n

    operador_id = _extract_operador_id(current_user)

    db.execute(
        text("""
            UPDATE lotes_encomendas
            SET status = 'fechado', data_fechamento = NOW(),
                operador_fechamento_id = :operador_id, total_encomendas = :total
            WHERE id_lote = :id
        """),
        {"operador_id": operador_id, "total": total, "id": lote_id}
    )
    _log_evento(db, lote_id, "fechamento", operador_id, detalhe=f"total_encomendas={total}")
    db.commit()

    lote_atualizado = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    return {"message": "Lote fechado", "lote": _lote_to_dict(lote_atualizado)}


# ============================================================
# GET /condominio/{condominio_id} — lista lotes (filtro opcional)
# ============================================================

@router.get("/condominio/{condominio_id:int}")
async def listar_lotes_condominio(
    condominio_id: int,
    status_filter: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _checar_acesso_condominio(current_user, condominio_id)

    query = "SELECT * FROM lotes_encomendas WHERE condominio_id = :cid"
    params: Dict[str, Any] = {"cid": condominio_id}
    if status_filter:
        query += " AND status = :status_filter"
        params["status_filter"] = status_filter
    query += " ORDER BY id_lote DESC LIMIT 200"

    lotes = db.execute(text(query), params).fetchall()
    return {"lotes": [_lote_to_dict(l) for l in lotes], "total": len(lotes)}


# ============================================================
# GET /operadores/{condominio_id} — operadores mobile do condomínio
# (destinatário na tela de transferência)
#
# DESVIO DO DOC: a seção 6 sugere GET /api/operadores/condominio/{cid},
# mas app/api/operadores.py já existente consulta a tabela LEGADA
# `operadores`, não `mobile_operadores` (quem realmente loga no app da
# portaria — ver claude.MD seção 12). Para não devolver a lista errada,
# este endpoint fica aqui, consultando mobile_operadores diretamente.
# ============================================================

@router.get("/operadores/{condominio_id:int}")
async def listar_operadores_mobile_condominio(
    condominio_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _checar_acesso_condominio(current_user, condominio_id)

    operadores = db.execute(
        text("""
            SELECT id, email, nome, telefone, role
            FROM mobile_operadores
            WHERE condominio_id = :cid AND ativo = 1
            ORDER BY nome ASC
        """),
        {"cid": condominio_id}
    ).fetchall()

    return {
        "operadores": [
            {"id": o.id, "email": o.email, "nome": o.nome, "telefone": o.telefone, "role": o.role}
            for o in operadores
        ],
        "total": len(operadores),
    }


# ============================================================
# GET /parados — lotes em alerta (seção 8 do doc)
# Cálculo ao vivo (não depende de alerta_nivel salvo, que só é
# atualizado pelo cron monitor_lotes_parados.py — item pendente).
# ============================================================

@router.get("/parados")
async def listar_lotes_parados(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_master = _is_master(current_user)
    cond_id_usuario = _resolve_cond_id(current_user, None) if not is_master else None

    horas_atencao = float(os.getenv("LOTE_ALERTA_HORAS", "2"))
    horas_critico = float(os.getenv("LOTE_ALERTA_HORAS_CRITICO", "4"))
    horas_escalonar = float(os.getenv("LOTE_ALERTA_HORAS_ESCALONAR", "8"))
    janela_inicio = int(os.getenv("LOTE_ALERTA_JANELA_INICIO", "7"))
    janela_fim = int(os.getenv("LOTE_ALERTA_JANELA_FIM", "22"))

    # STATUS_ATIVOS_ALERTA é constante fixa no código (não input do usuário) — seguro interpolar
    placeholders = ",".join(f"'{s}'" for s in STATUS_ATIVOS_ALERTA)
    filtro_cond_sql = ""
    params_query: Dict[str, Any] = {}
    if not is_master:
        filtro_cond_sql = "AND l.condominio_id = :cond_id"
        params_query["cond_id"] = cond_id_usuario

    lotes = db.execute(
        text(f"""
            SELECT l.id_lote, l.condominio_id, c.nome AS condominio, c.usa_subportaria,
                   l.numero, l.status, l.total_encomendas,
                   COALESCE(l.data_conferencia, l.data_transferencia, l.data_fechamento, l.data_abertura) AS ultima_mov
            FROM lotes_encomendas l
            JOIN condominios c ON c.id = l.condominio_id
            WHERE l.status IN ({placeholders})
              AND l.data_envio_msg IS NULL
              {filtro_cond_sql}
        """),
        params_query
    ).fetchall()

    agora = datetime.now()
    resultado = []
    for l in lotes:
        if not l.ultima_mov:
            continue
        horas = _horas_uteis(l.ultima_mov, agora, janela_inicio, janela_fim)
        if horas >= horas_escalonar:
            nivel = 3
        elif horas >= horas_critico:
            nivel = 2
        elif horas >= horas_atencao:
            nivel = 1
        else:
            continue
        resultado.append({
            "id_lote": l.id_lote,
            "condominio_id": l.condominio_id,
            "condominio": l.condominio,
            "usa_subportaria": bool(l.usa_subportaria),
            "numero": l.numero,
            "status": l.status,
            "total_encomendas": l.total_encomendas,
            "ultima_mov": l.ultima_mov.isoformat(),
            "horas_paradas_uteis": round(horas, 1),
            "alerta_nivel": nivel,
        })

    resultado.sort(key=lambda x: x["horas_paradas_uteis"], reverse=True)
    return {"lotes_parados": resultado, "total": len(resultado)}


# ============================================================
# GET /{lote_id} — detalhe + encomendas do lote
# (declarado depois das rotas estáticas acima, para não colidir
# com /parados, /condominio/{...}, /operadores/{...}, /aberto/{...})
# ============================================================

@router.get("/{lote_id:int}")
async def get_lote_detalhe(
    lote_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lote = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    if not lote:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Lote não encontrado")

    _checar_acesso_condominio(current_user, lote.condominio_id)

    encomendas = db.execute(
        text("""
            SELECT id, nome_destinatario, apartamento, bloco, codigo_rastreio,
                   status, data_recebimento, local_armazenamento
            FROM encomendas WHERE lote_id = :id ORDER BY id ASC
        """),
        {"id": lote_id}
    ).fetchall()

    cond_row = db.execute(
        text("SELECT nome FROM condominios WHERE id = :id"), {"id": lote.condominio_id}
    ).fetchone()
    nome_condominio = cond_row.nome if cond_row else None

    eventos = db.execute(
        text("""
            SELECT le.id, le.evento, le.operador_id, le.detalhe, le.criado_em,
                   mo.nome AS operador_nome
            FROM lotes_eventos le
            LEFT JOIN mobile_operadores mo ON mo.id = le.operador_id
            WHERE le.lote_id = :id
            ORDER BY le.criado_em ASC
        """),
        {"id": lote_id}
    ).fetchall()

    return {
        "lote": _lote_to_dict(lote),
        "condominio_nome": nome_condominio,
        "encomendas": [
            {
                "id": e.id,
                "nome_destinatario": e.nome_destinatario,
                "apartamento": e.apartamento,
                "bloco": e.bloco,
                "codigo_rastreio": e.codigo_rastreio,
                "status": e.status,
                "data_recebimento": e.data_recebimento.isoformat() if e.data_recebimento else None,
                "local_armazenamento": e.local_armazenamento,
            }
            for e in encomendas
        ],
        "eventos": [
            {
                "id": ev.id,
                "evento": ev.evento,
                "operador_id": ev.operador_id,
                "operador_nome": ev.operador_nome,
                "detalhe": ev.detalhe,
                "criado_em": ev.criado_em.isoformat() if ev.criado_em else None,
            }
            for ev in eventos
        ],
        "total_encomendas": len(encomendas),
    }


# ============================================================
# POST /{lote_id}/transferir
# ============================================================

@router.post("/{lote_id:int}/transferir")
async def transferir_lote(
    lote_id: int,
    data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    operador_destino_id = data.get("operador_destino_id")
    if not operador_destino_id:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="operador_destino_id é obrigatório")

    lote = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    if not lote:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Lote não encontrado")

    _checar_acesso_condominio(current_user, lote.condominio_id)

    if lote.status != "fechado":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Lote precisa estar 'fechado' para ser transferido (atual: {lote.status})"
        )

    destino = db.execute(
        text("SELECT id FROM mobile_operadores WHERE id = :id AND condominio_id = :cid AND ativo = 1"),
        {"id": operador_destino_id, "cid": lote.condominio_id}
    ).fetchone()
    if not destino:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Operador destino inválido ou não pertence a este condomínio"
        )

    operador_origem_id = _extract_operador_id(current_user)

    db.execute(
        text("""
            UPDATE lotes_encomendas
            SET status = 'em_transferencia', operador_origem_id = :origem,
                operador_destino_id = :destino, data_transferencia = NOW()
            WHERE id_lote = :id
        """),
        {"origem": operador_origem_id, "destino": operador_destino_id, "id": lote_id}
    )
    _log_evento(db, lote_id, "transferencia", operador_origem_id, detalhe=f"destino={operador_destino_id}")
    db.commit()

    lote_atualizado = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    return {"message": "Lote em transferência", "lote": _lote_to_dict(lote_atualizado)}


# ============================================================
# POST /{lote_id}/conferir
# ============================================================

@router.post("/{lote_id:int}/conferir")
async def conferir_lote(
    lote_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lote = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    if not lote:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Lote não encontrado")

    _checar_acesso_condominio(current_user, lote.condominio_id)

    if lote.status != "em_transferencia":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Lote precisa estar 'em_transferencia' para ser conferido (atual: {lote.status})"
        )

    operador_id = _extract_operador_id(current_user)

    # Regra de negócio (seção 2, item 5 do doc): quem recebe confere.
    # Master pode confirmar em nome de outro operador se necessário.
    if not _is_master(current_user) and lote.operador_destino_id and operador_id != lote.operador_destino_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Apenas o operador destino (quem recebeu o lote) pode confirmar a conferência"
        )

    db.execute(
        text("UPDATE lotes_encomendas SET status = 'conferido', data_conferencia = NOW() WHERE id_lote = :id"),
        {"id": lote_id}
    )
    _log_evento(db, lote_id, "conferencia", operador_id)
    db.commit()

    lote_atualizado = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    return {"message": "Lote conferido", "lote": _lote_to_dict(lote_atualizado)}


# ============================================================
# POST /{lote_id}/notificar — ÚNICO ponto do sistema que envia
# aviso de encomenda de lote ao morador (seção 2.1, regra central).
# Idempotente: bloqueia reenvio se data_envio_msg já preenchido.
# Envia individualmente por encomenda (nunca agrupado — regra do
# claude.MD: ENCOMENDA_RECEBIDA/CADASTRO_MORADOR sempre 1x1).
# ============================================================

@router.post("/{lote_id:int}/notificar")
async def notificar_lote(
    lote_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lote = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    if not lote:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Lote não encontrado")

    _checar_acesso_condominio(current_user, lote.condominio_id)

    if lote.status != "conferido":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Lote precisa estar 'conferido' para notificar (atual: {lote.status})"
        )
    if lote.data_envio_msg is not None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Mensagens deste lote já foram enviadas anteriormente (idempotência)"
        )

    cond = db.execute(text("SELECT nome FROM condominios WHERE id = :id"), {"id": lote.condominio_id}).fetchone()
    nome_condominio = cond.nome if cond else ""

    encomendas = db.execute(
        text("""
            SELECT e.id, e.nome_destinatario, e.apartamento, e.bloco, e.codigo_rastreio,
                   e.remetente, e.telefone_morador, e.morador_id, e.codigo_retirada,
                   e.local_armazenamento,
                   m.whats_confirmado, m.telefone AS telefone_cadastro
            FROM encomendas e
            LEFT JOIN moradores m ON e.morador_id = m.id
            WHERE e.lote_id = :lote_id
        """),
        {"lote_id": lote_id}
    ).fetchall()

    enfileiradas = 0
    puladas = 0
    erros = 0
    operador_id = _extract_operador_id(current_user)

    if not settings.WHATSAPP_ENABLED:
        logger.info(f"📦 Lote {lote_id}: WHATSAPP_ENABLED=false — nenhuma mensagem será enviada de fato")

    for enc in encomendas:
        telefone = (enc.telefone_morador or enc.telefone_cadastro or "").strip()
        if not telefone or not enc.morador_id:
            puladas += 1
            continue
        try:
            dados_notificacao = {
                "id": enc.id,
                "nome_destinatario": enc.nome_destinatario,
                "apartamento": enc.apartamento,
                "bloco": enc.bloco,
                "codigo_rastreio": enc.codigo_rastreio,
                "remetente": enc.remetente,
                "telefone_morador": telefone,
                "nome_condominio": nome_condominio,
                "whats_confirmado": enc.whats_confirmado,
                "morador_id": enc.morador_id,
                "condominio_id": lote.condominio_id,
                "codigo_retirada": enc.codigo_retirada,
                "local_armazenamento": enc.local_armazenamento,
            }
            needs_confirmation = enc.whats_confirmado is None
            if settings.WHATSAPP_ENABLED:
                success, msg = await asyncio.to_thread(
                    whatsapp_service.send_package_notification,
                    dados_notificacao,
                    needs_confirmation
                )
                if success:
                    enfileiradas += 1
                else:
                    erros += 1
                    logger.warning(f"⚠️ Lote {lote_id} encomenda {enc.id}: {msg}")
            else:
                puladas += 1
        except Exception as e:
            erros += 1
            logger.error(f"❌ Lote {lote_id} encomenda {enc.id}: erro ao notificar: {e}")

    db.execute(
        text("UPDATE lotes_encomendas SET status = 'notificado', data_envio_msg = NOW() WHERE id_lote = :id"),
        {"id": lote_id}
    )
    _log_evento(
        db, lote_id, "envio_msg", operador_id,
        detalhe=f"enfileiradas={enfileiradas} puladas={puladas} erros={erros} whatsapp_enabled={settings.WHATSAPP_ENABLED}"
    )
    db.commit()

    logger.info(f"📦 Lote {lote_id} notificado — enfileiradas={enfileiradas} puladas={puladas} erros={erros}")

    lote_atualizado = db.execute(text("SELECT * FROM lotes_encomendas WHERE id_lote = :id"), {"id": lote_id}).fetchone()
    return {
        "message": "Notificação processada",
        "lote": _lote_to_dict(lote_atualizado),
        "enfileiradas": enfileiradas,
        "puladas": puladas,
        "erros": erros,
        "whatsapp_enabled": settings.WHATSAPP_ENABLED,
    }
