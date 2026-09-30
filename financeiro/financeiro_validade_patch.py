"""
================================================================================
PATCH: financeiro_validade_patch.py
Adicionar ao final de: financeiro_condominios.py

1. Rota POST /condominios/:id/ajustar-validade
   - Recebe { dias: int, motivo: str } (dias pode ser negativo para reduzir)
   - Atualiza validade_ate no banco
   - Gera log em /home/visionlpr/logs/ajuste_validade.log
   - Retorna nova validade e dias restantes

Cole este conteúdo no FINAL do arquivo financeiro_condominios.py
================================================================================
"""

# ── Adicione estes imports no TOPO do financeiro_condominios.py caso não existam ──
# from datetime import date, timedelta, datetime
# import os

from datetime import date, timedelta, datetime
import os
from pydantic import BaseModel
from typing import Optional

LOG_VALIDADE = "/home/visionlpr/logs/ajuste_validade.log"

class AjusteValidadeRequest(BaseModel):
    dias: int                          # positivo = adiciona, negativo = reduz
    motivo: Optional[str] = ""
    operador: Optional[str] = "Admin"  # nome do usuário que fez a alteração


def _log_ajuste(condominio_id: int, nome: str, validade_antiga, validade_nova: date,
                dias: int, motivo: str, operador: str):
    """Grava log em arquivo txt."""
    try:
        os.makedirs(os.path.dirname(LOG_VALIDADE), exist_ok=True)
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sinal = f"+{dias}" if dias >= 0 else str(dias)
        linha = (
            f"[{agora}] "
            f"OPERADOR={operador} | "
            f"COND_ID={condominio_id} | "
            f"NOME={nome} | "
            f"AJUSTE={sinal} dias | "
            f"DE={validade_antiga} → PARA={validade_nova} | "
            f"MOTIVO={motivo or 'não informado'}\n"
        )
        with open(LOG_VALIDADE, "a", encoding="utf-8") as f:
            f.write(linha)
    except Exception as e:
        logger.warning(f"Falha ao gravar log de ajuste: {e}")


@router.post("/condominios/{id_condominio}/ajustar-validade")
async def ajustar_validade(
    id_condominio: int,
    body: AjusteValidadeRequest,
    db: Session = Depends(get_db)
):
    """
    Ajusta a validade de um condomínio adicionando ou removendo dias.
    Gera log em /home/visionlpr/logs/ajuste_validade.log
    """
    try:
        # 1. Busca condomínio
        result = db.execute(
            text("SELECT id, nome, validade_ate FROM condominios WHERE id = :id"),
            {"id": id_condominio}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        nome          = result[1] or f"Condomínio #{id_condominio}"
        validade_atual = result[2]  # date ou None

        # 2. Calcula nova validade
        base = validade_atual if validade_atual else date.today()
        # Se já venceu e está adicionando dias, parte de hoje
        if validade_atual and validade_atual < date.today() and body.dias > 0:
            base = date.today()

        nova_validade = base + timedelta(days=body.dias)

        # Garante que não fique no passado se for adição
        if body.dias > 0 and nova_validade < date.today():
            nova_validade = date.today() + timedelta(days=body.dias)

        # 3. Atualiza no banco
        db.execute(
            text("UPDATE condominios SET validade_ate = :val WHERE id = :id"),
            {"val": nova_validade, "id": id_condominio}
        )
        db.commit()

        # 4. Grava log
        _log_ajuste(
            condominio_id  = id_condominio,
            nome           = nome,
            validade_antiga= validade_atual,
            validade_nova  = nova_validade,
            dias           = body.dias,
            motivo         = body.motivo,
            operador       = body.operador,
        )

        dias_restantes = (nova_validade - date.today()).days

        logger.info(
            f"Validade ajustada: cond={id_condominio} | "
            f"{validade_atual} → {nova_validade} ({body.dias:+d} dias) | "
            f"op={body.operador}"
        )

        return {
            "success":        True,
            "condominio_id":  id_condominio,
            "nome":           nome,
            "validade_antiga": validade_atual.isoformat() if validade_atual else None,
            "validade_nova":  nova_validade.isoformat(),
            "dias_ajustados": body.dias,
            "dias_restantes": dias_restantes,
            "motivo":         body.motivo,
            "operador":       body.operador,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao ajustar validade cond={id_condominio}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao ajustar validade: {str(e)}")


@router.get("/condominios/logs/ajuste-validade")
async def listar_logs_ajuste(linhas: int = 50):
    """Retorna as últimas N linhas do log de ajuste de validade."""
    try:
        if not os.path.exists(LOG_VALIDADE):
            return {"success": True, "logs": [], "arquivo": LOG_VALIDADE}

        with open(LOG_VALIDADE, "r", encoding="utf-8") as f:
            todas = f.readlines()

        ultimas = todas[-linhas:] if len(todas) > linhas else todas
        return {
            "success": True,
            "total_linhas": len(todas),
            "exibindo": len(ultimas),
            "arquivo": LOG_VALIDADE,
            "logs": [l.strip() for l in reversed(ultimas)],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
