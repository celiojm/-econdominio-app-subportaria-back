# ==============================================================================
# ARQUIVO: gerar_folha.py
# PASTA: /home/ubuntu/backend/monitor/
# DESCRIÇÃO: Gera lançamentos mensais em contas_a_pagar para funcionários
#            ativos na tabela funcionarios. Idempotente — não duplica se já
#            existir lançamento para o mesmo funcionário/competência.
# USO MANUAL: python3 gerar_folha.py [MM/YYYY]
#             Sem argumento: usa o mês atual.
# CRONTAB sugerido: 0 6 1 * * (dia 1 de cada mês às 06h — gera pendente,
#             marcar como pago manualmente após efetuar o PIX)
# ==============================================================================

import os
import sys
import logging
from datetime import date, datetime
from calendar import monthrange

from dotenv import load_dotenv

for _env in [
    "/home/ubuntu/backend/.env.worker",
    "/home/ubuntu/backend/.env",
]:
    if os.path.exists(_env):
        load_dotenv(_env, override=False)
        break

from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("gerar_folha")

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


def parse_competencia(arg: str | None) -> tuple[int, int, str]:
    """Retorna (ano, mes, 'MM/YYYY')."""
    if arg:
        try:
            mes, ano = arg.split("/")
            return int(ano), int(mes), arg.strip()
        except Exception:
            logger.error("Formato inválido. Use MM/YYYY. Ex: 07/2026")
            sys.exit(1)
    hoje = date.today()
    return hoje.year, hoje.month, hoje.strftime("%m/%Y")


def main():
    ano, mes, competencia = parse_competencia(sys.argv[1] if len(sys.argv) > 1 else None)
    logger.info("Gerando folha para competência: %s", competencia)

    # Último dia do mês para data de vencimento
    ultimo_dia = monthrange(ano, mes)[1]

    with engine.connect() as conn:
        # Buscar funcionários ativos
        funcionarios = conn.execute(text("""
            SELECT id, nome, tipo, cargo, salario_base, dia_pagamento, conta_pagamento, observacoes
            FROM funcionarios
            WHERE ativo = 1
        """)).fetchall()

        logger.info("%s funcionário(s) ativo(s)", len(funcionarios))

        inseridos = 0
        pulados   = 0

        for f in funcionarios:
            id_func, nome, tipo, cargo, salario, dia_pgto, conta, obs = f

            # Data de vencimento = dia_pagamento do mês da competência
            dia = min(dia_pgto, ultimo_dia)
            data_venc = date(ano, mes, dia)
            descricao = f"{TIPO_LABEL.get(tipo, 'Pagamento')} — {nome}"

            # Verificar se já existe para esta competência + funcionário
            existe = conn.execute(text("""
                SELECT id FROM contas_a_pagar
                WHERE competencia = :comp
                  AND fornecedor   = :nome
                  AND categoria    = 'pessoal'
                LIMIT 1
            """), {"comp": competencia, "nome": nome}).fetchone()

            if existe:
                logger.info("Já existe lançamento para %s em %s — pulando", nome, competencia)
                pulados += 1
                continue

            conn.execute(text("""
                INSERT INTO contas_a_pagar
                  (descricao, fornecedor, categoria, valor, data_vencimento,
                   competencia, status, forma_pagamento, conta_pagamento,
                   observacoes, criado_por, origem)
                VALUES
                  (:desc, :nome, 'pessoal', :valor, :venc,
                   :comp, 'pendente', 'PIX', :conta,
                   :obs, 'gerar_folha.py', 'manual')
            """), {
                "desc":  descricao,
                "nome":  nome,
                "valor": salario,
                "venc":  data_venc,
                "comp":  competencia,
                "conta": conta,
                "obs":   obs,
            })

            logger.info("✅ Lançado: %s — R$ %.2f — vence %s", nome, salario, data_venc)
            inseridos += 1

        conn.commit()

    logger.info("Concluído: %s inseridos, %s já existiam.", inseridos, pulados)
    logger.info("")
    logger.info("⚠️  Lançamentos criados com status 'pendente'.")
    logger.info("   Após efetuar o PIX, marcar como pago:")
    logger.info("   UPDATE contas_a_pagar SET status='pago', valor_pago=valor,")
    logger.info("     data_pagamento='YYYY-MM-DD' WHERE competencia='%s' AND categoria='pessoal';", competencia)


if __name__ == "__main__":
    main()
