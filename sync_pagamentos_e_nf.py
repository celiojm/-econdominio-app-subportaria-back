#!/usr/bin/env python3
# ============================================================================
# ARQUIVO: sync_pagamentos_e_nf.py
# PASTA: /home/visionlpr/app_subportaria_back/
# DESCRIÇÃO: Sincroniza pagamentos do Asaas e gera NFs — SEM envio de email
#            O email é enviado pelo sync_nf_aprovadas.py às 08:30
# VERSÃO: 1.4.0 - 2026-10-15: falhava todo dia desde 04/10 (chamava localhost:5000, porta do backend
#                 clássico, que não existe na Oracle). Agora: SYNC_BASE_URL (padrão localhost:5002),
#                 token interno de 10 min (FINANCEIRO_AUTH_ENFORCE exige login em /api/financeiro/*),
#                 banco pelo DB_NAME do .env, pasta/arquivos relativos ao script, e falha no sync
#                 não impede mais a etapa de notas fiscais.
# CRON: 0 7 * * * flock -n /tmp/sync_nf.lock /home/visionlpr/app_subportaria_back/.env.worker/bin/python3 /home/visionlpr/app_subportaria_back/sync_pagamentos_e_nf.py >> /home/visionlpr/app_subportaria_back/sync_pagamentos_e_nf.log 2>&1
# ============================================================================
import os
import sys
import logging
import httpx
from datetime import date, datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env.worker' if os.path.exists(os.path.join(BASE_DIR, '.env.worker')) else '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'sync_pagamentos_e_nf.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BASE_URL = os.getenv('SYNC_BASE_URL', 'http://localhost:5002')


def _token_interno() -> str:
    """Token curto aceito por /api/financeiro/* (mesma chave do painel financeiro)."""
    from jose import jwt
    chave = os.getenv('JWT_SECRET_KEY')
    if not chave:
        raise RuntimeError('JWT_SECRET_KEY ausente no .env')
    agora = datetime.now(timezone.utc)
    return jwt.encode({'sub': 'rotina_sync_asaas', 'nome': 'Rotina 07:00 (sync Asaas)', 'tipo': 'operador',
                       'iat': agora, 'exp': agora + timedelta(minutes=10)}, chave, algorithm='HS256')


def fmt_moeda(v):
    try:
        return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(v)


def _registrar_tentativa(conn, id_cobranca: int, erro_msg: str):
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE cobrancas SET nf_tentativas = COALESCE(nf_tentativas,0)+1, nf_erro = %s WHERE id_cobranca = %s",
            (erro_msg[:200], id_cobranca)
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"   Não foi possível registrar tentativa #{id_cobranca}: {e}")


def main():
    logger.info("=== Iniciando sync pagamentos + NF ===")

    import pymysql

    try:
        conn = pymysql.connect(
            host=os.getenv('DB_HOST', '10.3.1.3'),
            port=int(os.getenv('DB_PORT', 6033)),
            user=os.getenv('DB_USER', 'econdo'),
            password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'AdmGeral'),
            charset='utf8mb4',
            connect_timeout=10,
        )
        conn.cursor().execute(f"USE {os.getenv('DB_NAME', 'AdmGeral')}")
    except Exception as e:
        logger.error(f"❌ Erro ao conectar ao banco: {e}")
        return

    with httpx.Client(timeout=120, headers={'Authorization': f'Bearer {_token_interno()}'}) as client:

        # ── 1. Sincronizar pagamentos ──
        logger.info("1. Sincronizando pagamentos do Asaas...")
        try:
            r = client.post(f"{BASE_URL}/api/financeiro/sync/pagamentos?buscar_todos=false")
            if r.status_code == 200:
                data = r.json()
                logger.info(
                    f"   ✅ Sync OK: {data.get('total_sincronizados',0)} processados, "
                    f"{data.get('novos',0)} novos, "
                    f"{data.get('validades_atualizadas',0)} validades atualizadas"
                )
                if data.get('erros'):
                    logger.warning(f"   ⚠️ Erros: {data['erros']}")
            else:
                logger.error(f"   ❌ Sync falhou: {r.status_code} {r.text[:200]}")
        except Exception as e:
            logger.error(f"   ❌ Erro no sync: {e}")
        # 2026-10-15: falha no sync não impede a emissão das notas das cobranças já pagas

        # ── 2. Buscar cobranças pagas sem NF ──
        logger.info("2. Buscando cobranças pagas sem nota fiscal...")
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id_cobranca, descricao, valor, asaas_payment_id
                FROM cobrancas
                WHERE status IN ('pago','RECEIVED','CONFIRMED','RECEIVED_IN_CASH')
                  AND asaas_invoice_id IS NULL
                  AND asaas_payment_id IS NOT NULL
                  AND (nf_tentativas IS NULL OR nf_tentativas < 3)
                ORDER BY data_pagamento DESC
                LIMIT 50
            """)
            cobrancas = cursor.fetchall()
            logger.info(f"   {len(cobrancas)} cobranças pagas sem NF")
        except Exception as e:
            logger.error(f"   ❌ Erro ao buscar cobranças: {e}")
            conn.close()
            return

        # ── 3. Gerar NF ──
        if not cobrancas:
            logger.info("   Nenhuma cobrança pendente de NF.")
        else:
            logger.info("3. Gerando notas fiscais...")
            ok = 0
            erro = 0
            for id_cobranca, descricao, valor, payment_id in cobrancas:
                try:
                    r = client.post(f"{BASE_URL}/api/financeiro/cobrancas/{id_cobranca}/gerar-nf")
                    if r.status_code in (200, 201):
                        logger.info(f"   ✅ NF gerada: #{id_cobranca} ({(descricao or '')[:40]}) {fmt_moeda(valor)}")
                        ok += 1
                    else:
                        msg = r.text[:200]
                        logger.warning(f"   ⚠️ NF #{id_cobranca}: {r.status_code} {msg}")
                        erro += 1
                        _registrar_tentativa(conn, id_cobranca, msg)
                except Exception as e:
                    logger.error(f"   ❌ Erro NF #{id_cobranca}: {e}")
                    erro += 1
                    _registrar_tentativa(conn, id_cobranca, str(e))

            logger.info(f"   NF: {ok} geradas, {erro} erros")

    conn.close()
    logger.info("=== Sync concluído — emails serão enviados pelo sync_nf_aprovadas.py às 08:30 ===")


if __name__ == '__main__':
    main()
