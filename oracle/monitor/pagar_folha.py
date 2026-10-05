# ==============================================================================
# ARQUIVO: pagar_folha.py
# PASTA: /home/ubuntu/backend/monitor/
# DESCRIÇÃO: Consolida dois passos do processo mensal de folha:
#            1. Gera lançamento pendente (igual ao gerar_folha.py)
#            2. Marca como pago com data e valor informados
#
# USO: python3 pagar_folha.py [MM/YYYY] [YYYY-MM-DD] [--seco]
#      MM/YYYY    — competência (padrão: mês atual)
#      YYYY-MM-DD — data do pagamento (padrão: hoje)
#      --seco     — só mostra o que faria, sem alterar o banco
#
# EXEMPLOS:
#   python3 pagar_folha.py                      # mês atual, data hoje
#   python3 pagar_folha.py 08/2026              # agosto, data hoje
#   python3 pagar_folha.py 08/2026 2026-08-07   # agosto, pago dia 07
#   python3 pagar_folha.py 08/2026 --seco       # só simula
# ==============================================================================

import os
import sys
import logging
from datetime import date
from calendar import monthrange
from dotenv import load_dotenv
import requests
for _env in [
    "/home/ubuntu/backend/.env.worker",
    "/home/ubuntu/backend/.env",
]:
    if os.path.exists(_env):
        load_dotenv(_env, override=False)
        break
ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY", "")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://api.asaas.com/v3")

from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("pagar_folha")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurado no .env")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

TIPO_LABEL = {
    "clt":        "Salário CLT",
    "estagiario": "Bolsa Estágio",
    "pj":         "Pagamento PJ",
    "autonomo":   "Pagamento Autônomo",
}


def parse_args():
    args = [a for a in sys.argv[1:] if a != "--seco"]
    seco = "--seco" in sys.argv

    hoje = date.today()
    competencia = hoje.strftime("%m/%Y")
    data_pgto   = hoje.isoformat()

    if len(args) >= 1:
        try:
            mes, ano = args[0].split("/")
            competencia = f"{int(mes):02d}/{ano}"
        except Exception:
            logger.error("Formato de competência inválido. Use MM/YYYY")
            sys.exit(1)

    if len(args) >= 2:
        try:
            date.fromisoformat(args[1])
            data_pgto = args[1]
        except Exception:
            logger.error("Formato de data inválido. Use YYYY-MM-DD")
            sys.exit(1)

    return competencia, data_pgto, seco


def main():
    competencia, data_pgto, seco = parse_args()
    ano, mes = int(competencia[3:]), int(competencia[:2])
    ultimo_dia = monthrange(ano, mes)[1]

    modo = "🔵 SIMULAÇÃO" if seco else "✅ PRODUÇÃO"
    logger.info("=== pagar_folha.py | %s | Competência: %s | Pagamento: %s ===",
                modo, competencia, data_pgto)

    with engine.connect() as conn:
        funcionarios = conn.execute(text("""
            SELECT id, nome, tipo, cargo, salario_base, dia_pagamento, conta_pagamento, observacoes
            FROM funcionarios
            WHERE ativo = 1
        """)).fetchall()

        logger.info("%s funcionário(s) ativo(s)", len(funcionarios))

        for f in funcionarios:
            id_func, nome, tipo, cargo, salario, dia_pgto_func, conta, obs = f

            dia      = min(dia_pgto_func, ultimo_dia)
            data_venc = date(ano, mes, dia)
            descricao = f"{TIPO_LABEL.get(tipo, 'Pagamento')} — {nome}"

            # ── Passo 1: verificar/inserir lançamento ──
            existente = conn.execute(text("""
                SELECT id, status, valor_pago, data_pagamento
                FROM contas_a_pagar
                WHERE competencia = :comp
                  AND fornecedor   = :nome
                  AND categoria    = 'pessoal'
                LIMIT 1
            """), {"comp": competencia, "nome": nome}).fetchone()

            if existente:
                id_lanc, status_atual, vp_atual, dp_atual = existente
                logger.info("Lançamento já existe (id=%s) | status=%s | pago=%s | data=%s",
                            id_lanc, status_atual, vp_atual, dp_atual)
            else:
                logger.info("Inserindo lançamento pendente: %s — R$ %.2f", nome, salario)
                if not seco:
                    conn.execute(text("""
                        INSERT INTO contas_a_pagar
                          (descricao, fornecedor, categoria, valor, data_vencimento,
                           competencia, status, forma_pagamento, conta_pagamento,
                           observacoes, criado_por, origem)
                        VALUES
                          (:desc, :nome, 'pessoal', :valor, :venc,
                           :comp, 'pendente', 'PIX', :conta,
                           :obs, 'pagar_folha.py', 'manual')
                    """), {
                        "desc":  descricao,
                        "nome":  nome,
                        "valor": salario,
                        "venc":  data_venc,
                        "comp":  competencia,
                        "conta": conta,
                        "obs":   obs,
                    })
                    id_lanc = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                    logger.info("✅ Lançamento inserido (id=%s)", id_lanc)
                else:
                    logger.info("🔵 [seco] Inseriria lançamento para %s", nome)

            # ── Passo 2: verificar pagamento no Asaas ──
            chave_pix = f.chave_pix if hasattr(f, 'chave_pix') else None
            # rebuscar chave_pix pois não estava no SELECT inicial
            row_pix = conn.execute(text(
                "SELECT chave_pix FROM funcionarios WHERE id = :id"
            ), {"id": id_func}).fetchone()
            chave_pix = row_pix[0] if row_pix else None

            data_pgto_confirmada = data_pgto
            if chave_pix and ASAAS_API_KEY:
                logger.info("Verificando pagamento no Asaas para %s (chave: %s)...", nome, chave_pix)
                try:
                    r = requests.get(
                        f"{ASAAS_BASE_URL}/transfers",
                        headers={"access_token": ASAAS_API_KEY},
                        params={"limit": 50},
                        timeout=15,
                    )
                    if r.status_code == 200:
                        transfers = r.json().get("data", [])
                        encontrado = None
                        for t in transfers:
                            ba = t.get("bankAccount") or {}
                            pix_key = (ba.get("pixAddressKey") or "").lower()
                            owner   = (ba.get("ownerName") or "").lower()
                            t_val   = float(t.get("value") or 0)
                            t_date  = t.get("confirmedDate") or t.get("dateCreated") or ""
                            t_mes   = t_date[:7]  # YYYY-MM
                            comp_mes = f"{competencia[3:]}-{competencia[:2]}"  # MM/YYYY → YYYY-MM

                            if (
                                t.get("status") == "DONE"
                                and t_mes == comp_mes
                                and (
                                    pix_key == chave_pix.lower()
                                    or nome.lower() in owner
                                )
                            ):
                                encontrado = t
                                break

                        if encontrado:
                            data_pgto_confirmada = encontrado.get("confirmedDate", data_pgto)
                            logger.info("✅ Pagamento confirmado no Asaas: R$ %.2f em %s",
                                        float(encontrado.get("value", 0)), data_pgto_confirmada)
                        else:
                            logger.warning("⚠️  Pagamento NÃO encontrado no Asaas para %s em %s — verifique antes de continuar", nome, competencia)
                            if not seco:
                                logger.warning("   Abortando marcação como pago para %s", nome)
                                continue
                except Exception as e:
                    logger.warning("⚠️  Erro ao consultar Asaas: %s — prosseguindo com data informada", e)

            logger.info("Marcando como pago: %s — R$ %.2f — data %s", nome, salario, data_pgto_confirmada)
            if not seco:
                conn.execute(text("""
                    UPDATE contas_a_pagar
                    SET status         = 'pago',
                        valor_pago     = valor,
                        data_pagamento = :data_pgto
                    WHERE competencia = :comp
                      AND fornecedor   = :nome
                      AND categoria    = 'pessoal'
                      AND status      != 'pago'
                """), {"data_pgto": data_pgto_confirmada, "comp": competencia, "nome": nome})
                logger.info("✅ Marcado como pago")
            else:
                logger.info("🔵 [seco] Marcaria como pago em %s", data_pgto_confirmada)

        if not seco:
            conn.commit()

    logger.info("=== Concluído ===")
    if not seco:
        logger.info("")
        logger.info("Verificar resultado:")
        logger.info("  SELECT id, descricao, valor_pago, status, data_pagamento")
        logger.info("  FROM contas_a_pagar")
        logger.info("  WHERE competencia = '%s' AND categoria = 'pessoal';", competencia)


if __name__ == "__main__":
    main()
