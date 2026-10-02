#!/usr/bin/env python3
# ============================================================================
# ARQUIVO: monitor_encomendas_paradas.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Cron diário (17h) — para condomínios com condominios.
#            lembrete_encomenda_ativo=1, envia lembrete WhatsApp ao morador
#            quando a encomenda pendente passa de 48h e de 72h sem retirada.
#            No máximo 2 lembretes por encomenda (48h e 72h; se já passou de 72h
#            sem nenhum, só o de 72h) e 1 mensagem por telefone por execução
#            (várias encomendas atrasadas do mesmo morador = 1 aviso).
#            Só enfileira na fila MySQL (whatsapp_message_queue) — o envio
#            real é feito depois pelo worker send_grouped_whatsapp.py.
# VERSÃO: 2.0.0 - sem 24h; só 48h/72h; 1 por telefone por rodada; 17h (2026-10-02)
#         1.0.0
# CRON: 0 17 * * * flock -n /tmp/monitor_encomendas_paradas.lock /home/visionlpr/app_subportaria_back/venv/bin/python3 /home/visionlpr/app_subportaria_back/monitor_encomendas_paradas.py >> /home/visionlpr/app_subportaria_back/monitor_encomendas_paradas.log 2>&1
# data criação: 2026-09-09 data alteração: 2026-10-02
# ============================================================================

import os
import logging
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.whatsapp import send_lembrete_retirada

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("monitor_encomendas_paradas")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL nao configurado no .env")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Do maior limiar pro menor: se a encomenda ja passou de 72h e nunca recebeu
# nenhum lembrete (ex: feature acabou de ser ativada, ou script ficou fora do
# ar), manda so o lembrete de 72h nesta execucao — nunca os 3 de uma vez.
# 2026-10-02: sem lembrete de 24h (moradores recebiam mensagens demais)
THRESHOLDS = [
    (72, "lembrete_72h_enviado"),
    (48, "lembrete_48h_enviado"),
]


def buscar_encomendas_pendentes(db):
    sql = text("""
        SELECT
            e.id, e.condominio_id, e.nome_destinatario, e.apartamento, e.bloco,
            e.telefone_morador, e.morador_id, e.data_recebimento,
            e.lembrete_24h_enviado, e.lembrete_48h_enviado, e.lembrete_72h_enviado
        FROM encomendas e
        JOIN condominios c ON c.id = e.condominio_id
        JOIN moradores m ON m.id = e.morador_id
        WHERE e.status = 'pendente'
          AND c.lembrete_encomenda_ativo = 1
          AND m.whats_confirmado IS NOT NULL
    """)
    return db.execute(sql).mappings().all()


def marcar_enviado(db, encomenda_id: int, coluna: str) -> None:
    db.execute(
        text(f"UPDATE encomendas SET {coluna} = NOW() WHERE id = :id"),
        {"id": encomenda_id},
    )
    db.commit()


def main() -> None:
    db = SessionLocal()
    total_enviados = 0
    try:
        encomendas = buscar_encomendas_pendentes(db)
        logger.info("Encomendas pendentes elegiveis: %s", len(encomendas))

        # 1) para cada encomenda, o lembrete devido (maior limiar ainda não enviado)
        por_telefone = {}   # telefone -> {"horas": maior limiar, "encs": [(enc, horas)]}
        for enc in encomendas:
            data_recebimento = enc["data_recebimento"]
            if not data_recebimento or not enc["telefone_morador"]:
                continue
            horas_passadas = (datetime.now() - data_recebimento).total_seconds() / 3600
            if enc["lembrete_72h_enviado"] is not None:
                continue   # já recebeu o último lembrete — nunca mais
            devido = None
            for horas, coluna in THRESHOLDS:
                if horas_passadas >= horas and enc[coluna] is None:
                    devido = horas
                    break
            if not devido:
                continue
            tel = "".join(ch for ch in str(enc["telefone_morador"]) if ch.isdigit())
            g = por_telefone.setdefault(tel, {"horas": 0, "encs": []})
            g["horas"] = max(g["horas"], devido)
            g["encs"].append((enc, devido))

        # 2) uma mensagem por telefone; marca todas as encomendas devidas desse telefone
        for tel, g in por_telefone.items():
            enc0 = g["encs"][0][0]
            encomenda_data = {
                "id": enc0["id"], "condominio_id": enc0["condominio_id"],
                "nome_destinatario": enc0["nome_destinatario"], "apartamento": enc0["apartamento"],
                "bloco": enc0["bloco"], "telefone_morador": enc0["telefone_morador"],
                "morador_id": enc0["morador_id"],
            }
            ok = send_lembrete_retirada(encomenda_data, g["horas"])
            if not ok:
                logger.warning("Falha ao enfileirar lembrete %sh | encomenda_id=%s", g["horas"], enc0["id"])
                continue
            for enc, devido in g["encs"]:
                marcar_enviado(db, enc["id"], "lembrete_48h_enviado" if devido == 48 else "lembrete_72h_enviado")
                if devido == 72 and enc["lembrete_48h_enviado"] is None:
                    marcar_enviado(db, enc["id"], "lembrete_48h_enviado")  # 72h cobre o de 48h
            total_enviados += 1
            logger.info("Lembrete %sh enfileirado | tel=...%s | encomendas=%s",
                        g["horas"], tel[-4:], [e["id"] for e, _ in g["encs"]])

        logger.info("Total de mensagens enfileiradas: %s (telefones)", total_enviados)
    finally:
        db.close()


if __name__ == "__main__":
    main()
