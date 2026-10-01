# ============================================================================
# ARQUIVO: whatsapp_entregas.py
# PASTA: app/services/ (dev2_back e app_subportaria_back)
# DESCRIÇÃO: Comprovante de entrega do WhatsApp. Grava cada mensagem enviada (worker) e cada
#            retorno da Meta (webhook: sent/delivered/read/failed) em `whatsapp_entregas`, e o
#            resumo do aviso de chegada (ENCOMENDA_RECEBIDA) nas colunas whatsapp_* de `encomendas`.
#            Status só avança (enviado → entregue → lido); "falhou" só se ainda não entregou.
#            Nunca levanta exceção para quem chama (envio e webhook não podem parar por causa disto).
#            Recebe a sessão de quem chama (o worker tem SessionLocal próprio).
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-01 data alteração: 2026-10-01
# ============================================================================
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

EVENTO_RESUMO = "ENCOMENDA_RECEBIDA"
_META_PARA_STATUS = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}

# atualiza o resumo da encomenda a partir da linha mais recente do aviso de chegada
_SQL_RESUMO = text("""
    UPDATE encomendas e
    JOIN (SELECT encomenda_id, status, enviado_em, entregue_em, lido_em,
                 CASE WHEN erro_codigo IS NULL THEN NULL
                      ELSE LEFT(CONCAT(erro_codigo, ' ', COALESCE(erro_titulo, '')), 255) END AS erro
          FROM whatsapp_entregas
          WHERE encomenda_id = :enc AND tipo_evento = :ev
          ORDER BY id DESC LIMIT 1) w ON w.encomenda_id = e.id
    SET e.whatsapp_status = w.status, e.whatsapp_enviado_em = w.enviado_em,
        e.whatsapp_entregue_em = w.entregue_em, e.whatsapp_lido_em = w.lido_em, e.whatsapp_erro = w.erro
    WHERE e.id = :enc
""")


def registrar_envio(db, msgs, provider, message_id, telefone):
    """Chamado pelo worker logo depois de marcar a fila como 'sent'. msgs = linhas da fila."""
    if not message_id:
        return
    try:
        for m in msgs:
            p = {"mid": str(message_id), "qid": m.get("id"), "enc": m.get("encomenda_id"),
                 "mor": m.get("morador_id"), "cond": m.get("condominio_id"), "tel": telefone,
                 "ev": m.get("tipo_evento"), "prov": provider}
            # o retorno da Meta pode chegar antes desta gravação: aproveita a linha "órfã" do webhook
            r = db.execute(text("""
                UPDATE whatsapp_entregas SET queue_id=:qid, encomenda_id=:enc, morador_id=:mor,
                       condominio_id=:cond, telefone=:tel, tipo_evento=:ev, provider=:prov,
                       enviado_em=COALESCE(enviado_em, NOW())
                WHERE message_id=:mid AND queue_id IS NULL LIMIT 1
            """), p)
            if r.rowcount == 0:
                db.execute(text("""
                    INSERT IGNORE INTO whatsapp_entregas
                        (message_id, queue_id, encomenda_id, morador_id, condominio_id, telefone,
                         tipo_evento, provider, status, enviado_em)
                    VALUES (:mid, :qid, :enc, :mor, :cond, :tel, :ev, :prov, 'enviado', NOW())
                """), p)
            if p["enc"] and p["ev"] == EVENTO_RESUMO:
                db.execute(_SQL_RESUMO, {"enc": p["enc"], "ev": EVENTO_RESUMO})
        db.commit()
    except Exception as e:
        logger.warning("whatsapp_entregas: falha ao registrar envio %s: %s", message_id, e)
        try:
            db.rollback()
        except Exception:
            pass


def registrar_status(db, statuses):
    """Chamado pelo webhook com a lista `statuses` da Meta."""
    for st in statuses or []:
        try:
            mid = st.get("id")
            novo = _META_PARA_STATUS.get(st.get("status"))
            if not mid or not novo:
                continue
            ts = int(st.get("timestamp") or 0) or None
            err = (st.get("errors") or [{}])[0]
            pricing = st.get("pricing") or {}
            p = {"mid": mid, "ts": ts, "cod": err.get("code"),
                 "tit": (err.get("title") or err.get("message") or "")[:255] or None,
                 "cob": (1 if pricing.get("billable") else 0) if pricing else None,
                 "cat": pricing.get("category") or None, "tel": st.get("recipient_id")}
            if novo == "enviado":
                sql = "enviado_em = COALESCE(enviado_em, FROM_UNIXTIME(:ts))"
            elif novo == "entregue":
                sql = ("entregue_em = IF(entregue_em IS NULL OR entregue_em > FROM_UNIXTIME(:ts), FROM_UNIXTIME(:ts), entregue_em), "
                       "status = IF(status IN ('enviado','falhou'), 'entregue', status)")
            elif novo == "lido":
                sql = ("lido_em = COALESCE(lido_em, FROM_UNIXTIME(:ts)), "
                       "entregue_em = COALESCE(entregue_em, FROM_UNIXTIME(:ts)), status = 'lido'")
            else:
                sql = ("falhou_em = COALESCE(falhou_em, FROM_UNIXTIME(:ts)), erro_codigo = :cod, erro_titulo = :tit, "
                       "status = IF(status = 'enviado', 'falhou', status)")
            r = db.execute(text(f"""
                UPDATE whatsapp_entregas SET {sql},
                       cobravel = COALESCE(:cob, cobravel), categoria = COALESCE(:cat, categoria)
                WHERE message_id = :mid
            """), p)
            if r.rowcount == 0:
                # retorno antes do registro do worker (ou mensagem enviada por outra rota): guarda órfã
                db.execute(text(f"""
                    INSERT IGNORE INTO whatsapp_entregas (message_id, telefone, provider, status, cobravel, categoria)
                    VALUES (:mid, :tel, 'meta', 'enviado', :cob, :cat)
                """), p)
                db.execute(text(f"UPDATE whatsapp_entregas SET {sql} WHERE message_id = :mid AND queue_id IS NULL"), p)
            for (enc,) in db.execute(text(
                    "SELECT DISTINCT encomenda_id FROM whatsapp_entregas WHERE message_id=:mid "
                    "AND encomenda_id IS NOT NULL AND tipo_evento=:ev"), {"mid": mid, "ev": EVENTO_RESUMO}).fetchall():
                db.execute(_SQL_RESUMO, {"enc": enc, "ev": EVENTO_RESUMO})
            db.commit()
        except Exception as e:
            logger.warning("whatsapp_entregas: falha ao registrar status %s: %s", st.get("id"), e)
            try:
                db.rollback()
            except Exception:
                pass
