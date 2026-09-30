#!/usr/bin/env python3
# ============================================================================
# ARQUIVO: monitor_encomendas_paradas.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Cron diário (16h) — para condomínios com condominios.
#            lembrete_encomenda_ativo=1, envia lembrete WhatsApp ao morador
#            quando a encomenda pendente passa de 24h/48h/72h sem retirada.
#            No máximo 1 lembrete por encomenda por execução (marca
#            encomendas.lembrete_Xh_enviado para nunca repetir o mesmo aviso).
#            Só enfileira na fila MySQL (whatsapp_message_queue) — o envio
#            real é feito depois pelo worker send_grouped_whatsapp.py.
# VERSÃO: 1.0.0
# CRON: 0 16 * * * flock -n /tmp/monitor_encomendas_paradas.lock /home/visionlpr/app_subportaria_back/venv/bin/python3 /home/visionlpr/app_subportaria_back/monitor_encomendas_paradas.py >> /home/visionlpr/app_subportaria_back/monitor_encomendas_paradas.log 2>&1
# data criação: 2026-09-09 data alteração: 2026-09-09
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
THRESHOLDS = [
    (72, "lembrete_72h_enviado"),
    (48, "lembrete_48h_enviado"),
    (24, "lembrete_24h_enviado"),
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

        for enc in encomendas:
            data_recebimento = enc["data_recebimento"]
            if not data_recebimento:
                continue
            horas_passadas = (datetime.now() - data_recebimento).total_seconds() / 3600

            for horas, coluna in THRESHOLDS:
                if enc[coluna] is not None:
                    continue
                if horas_passadas < horas:
                    continue

                encomenda_data = {
                    "id": enc["id"],
                    "condominio_id": enc["condominio_id"],
                    "nome_destinatario": enc["nome_destinatario"],
                    "apartamento": enc["apartamento"],
                    "bloco": enc["bloco"],
                    "telefone_morador": enc["telefone_morador"],
                    "morador_id": enc["morador_id"],
                }
                ok = send_lembrete_retirada(encomenda_data, horas)
                if ok:
                    marcar_enviado(db, enc["id"], coluna)
                    total_enviados += 1
                    logger.info(
                        "Lembrete %sh enfileirado | encomenda_id=%s | tel=%s",
                        horas, enc["id"], enc["telefone_morador"],
                    )
                else:
                    logger.warning(
                        "Falha ao enfileirar lembrete %sh | encomenda_id=%s",
                        horas, enc["id"],
                    )
                break  # so 1 lembrete por encomenda por execucao

        logger.info("Total de lembretes enfileirados: %s", total_enviados)
    finally:
        db.close()


if __name__ == "__main__":
    main()
