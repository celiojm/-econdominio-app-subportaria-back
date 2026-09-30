# ================================================================================
#  PATH: backend/financeiro/financeiro_planos.py
#  DESCRIPTION: Endpoints para planos do módulo financeiro
#               Retorna planos baseados na quantidade de apartamentos
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Faixas de unidades disponíveis (em ordem crescente)
FAIXAS_UNIDADES = [50, 100, 150, 200, 300, 500]


def determinar_faixa(total_apartamentos: int) -> int:
    """
    Determina a faixa de plano baseado no total de apartamentos.
    Se o condomínio tem 210 aptos, entra na faixa de 300.
    """
    if not total_apartamentos or total_apartamentos <= 0:
        return 50  # Faixa mínima
    
    for faixa in FAIXAS_UNIDADES:
        if total_apartamentos <= faixa:
            return faixa
    
    # Se exceder todas as faixas, retorna a maior
    return 500


@router.get("/planos")
async def listar_planos(
    todos: bool = Query(False, description="Se True, retorna todos os planos"),
    unidades: Optional[int] = Query(None, description="Quantidade de unidades para filtrar planos"),
    id_condominio: Optional[int] = Query(None, description="ID do condomínio para buscar unidades"),
    db: Session = Depends(get_db)
):
    """
    Lista planos disponíveis.
    
    - Se `todos=True`: retorna todos os planos ativos
    - Se `unidades` ou `id_condominio` informado: retorna os 4 planos (30, 90, 180, 360 dias) da faixa
    """
    try:
        # Se pediu todos os planos (para admin)
        if todos:
            result = db.execute(text("SELECT * FROM planos WHERE ativo = 1 ORDER BY max_unidades, dias_validade"))
            planos = result.fetchall()
            
            return {
                "planos": [
                    {
                        "id_plano": p.id_plano,
                        "codigo": p.codigo,
                        "nome": p.nome,
                        "max_unidades": p.max_unidades,
                        "dias_validade": p.dias_validade,
                        "valor": float(p.valor),
                        "descricao": f"Até {p.max_unidades} unidades - {p.dias_validade} dias",
                        "ativo": p.ativo
                    }
                    for p in planos
                ],
                "total": len(planos)
            }
        
        # Determinar quantidade de unidades
        qtd_unidades = unidades
        
        # Se informou condomínio, buscar total_apartamentos
        if id_condominio and not unidades:
            from app.models.condominio import Condominio
            cond = db.query(Condominio).filter(Condominio.id == id_condominio).first()
            if cond:
                qtd_unidades = cond.total_apartamentos or 50
                logger.info(f"Condomínio {id_condominio} tem {qtd_unidades} apartamentos")
            else:
                qtd_unidades = 50
        
        if not qtd_unidades:
            qtd_unidades = 50
            
        # Determinar faixa
        faixa = determinar_faixa(qtd_unidades)
        logger.info(f"Faixa determinada: {faixa} unidades para {qtd_unidades} apartamentos")
        
        # Buscar planos da faixa (os 4 períodos: 30, 90, 180, 360 dias)
        result = db.execute(
            text("SELECT * FROM planos WHERE ativo = 1 AND max_unidades = :faixa ORDER BY dias_validade"),
            {"faixa": faixa}
        )
        planos = result.fetchall()
        
        # Formatar resposta com nomes amigáveis
        planos_formatados = []
        for p in planos:
            # Determinar nome amigável baseado nos dias
            if p.dias_validade == 30:
                nome_periodo = "Mensal"
                descricao_periodo = "1 mês"
            elif p.dias_validade == 90:
                nome_periodo = "Trimestral"
                descricao_periodo = "3 meses"
            elif p.dias_validade == 180:
                nome_periodo = "Semestral"
                descricao_periodo = "6 meses"
            elif p.dias_validade == 360:
                nome_periodo = "Anual"
                descricao_periodo = "12 meses"
            else:
                nome_periodo = f"{p.dias_validade} dias"
                descricao_periodo = f"{p.dias_validade} dias"
            
            planos_formatados.append({
                "id_plano": p.id_plano,
                "codigo": p.codigo,
                "nome": nome_periodo,
                "nome_completo": p.nome,
                "max_unidades": p.max_unidades,
                "dias_validade": p.dias_validade,
                "valor": float(p.valor),
                "descricao": f"Até {p.max_unidades} unidades",
                "descricao_periodo": descricao_periodo
            })
        
        return {
            "planos": planos_formatados,
            "faixa_unidades": faixa,
            "total_apartamentos": qtd_unidades,
            "total": len(planos_formatados)
        }
        
    except Exception as e:
        logger.error(f"Erro ao listar planos: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar planos: {str(e)}")


@router.get("/planos/{id_plano}")
async def obter_plano(
    id_plano: int,
    db: Session = Depends(get_db)
):
    """Obtém detalhes de um plano específico."""
    try:
        result = db.execute(
            text("SELECT * FROM planos WHERE id_plano = :id"),
            {"id": id_plano}
        )
        plano = result.fetchone()
        
        if not plano:
            raise HTTPException(status_code=404, detail="Plano não encontrado")
        
        return {
            "id_plano": plano.id_plano,
            "codigo": plano.codigo,
            "nome": plano.nome,
            "max_unidades": plano.max_unidades,
            "dias_validade": plano.dias_validade,
            "valor": float(plano.valor),
            "ativo": plano.ativo
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter plano: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================================
# ENDPOINTS DE CRUD (ADMIN)
# ================================================================================

from pydantic import BaseModel
from typing import Optional

class PlanoUpdate(BaseModel):
    codigo: Optional[str] = None
    nome: Optional[str] = None
    max_unidades: Optional[int] = None
    dias_validade: Optional[int] = None
    valor: Optional[float] = None
    ativo: Optional[int] = None

class PlanoCreate(BaseModel):
    codigo: str
    nome: str
    max_unidades: int
    dias_validade: int
    valor: float
    ativo: int = 1


@router.put("/planos/{id_plano}")
async def atualizar_plano(
    id_plano: int,
    dados: PlanoUpdate,
    db: Session = Depends(get_db)
):
    """Atualiza um plano existente."""
    try:
        # Verificar se plano existe
        result = db.execute(
            text("SELECT * FROM planos WHERE id_plano = :id"),
            {"id": id_plano}
        )
        plano = result.fetchone()
        
        if not plano:
            raise HTTPException(status_code=404, detail="Plano não encontrado")
        
        # Montar query de update dinamicamente
        campos = []
        params = {"id": id_plano}
        
        if dados.codigo is not None:
            campos.append("codigo = :codigo")
            params["codigo"] = dados.codigo
        if dados.nome is not None:
            campos.append("nome = :nome")
            params["nome"] = dados.nome
        if dados.max_unidades is not None:
            campos.append("max_unidades = :max_unidades")
            params["max_unidades"] = dados.max_unidades
        if dados.dias_validade is not None:
            campos.append("dias_validade = :dias_validade")
            params["dias_validade"] = dados.dias_validade
        if dados.valor is not None:
            campos.append("valor = :valor")
            params["valor"] = dados.valor
        if dados.ativo is not None:
            campos.append("ativo = :ativo")
            params["ativo"] = dados.ativo
        
        if not campos:
            raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")
        
        query = f"UPDATE planos SET {', '.join(campos)} WHERE id_plano = :id"
        db.execute(text(query), params)
        db.commit()
        
        logger.info(f"Plano {id_plano} atualizado com sucesso")
        
        return {"success": True, "message": "Plano atualizado com sucesso"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao atualizar plano: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/planos")
async def criar_plano(
    dados: PlanoCreate,
    db: Session = Depends(get_db)
):
    """Cria um novo plano."""
    try:
        # Verificar se código já existe
        result = db.execute(
            text("SELECT id_plano FROM planos WHERE codigo = :codigo"),
            {"codigo": dados.codigo}
        )
        if result.fetchone():
            raise HTTPException(status_code=400, detail="Código de plano já existe")
        
        # Inserir novo plano
        db.execute(
            text("""
                INSERT INTO planos (codigo, nome, max_unidades, dias_validade, valor, ativo)
                VALUES (:codigo, :nome, :max_unidades, :dias_validade, :valor, :ativo)
            """),
            {
                "codigo": dados.codigo,
                "nome": dados.nome,
                "max_unidades": dados.max_unidades,
                "dias_validade": dados.dias_validade,
                "valor": dados.valor,
                "ativo": dados.ativo
            }
        )
        db.commit()
        
        logger.info(f"Plano {dados.codigo} criado com sucesso")
        
        return {"success": True, "message": "Plano criado com sucesso"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao criar plano: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/planos/{id_plano}")
async def excluir_plano(
    id_plano: int,
    db: Session = Depends(get_db)
):
    """Exclui um plano (se não houver assinaturas vinculadas)."""
    try:
        # Verificar se plano existe
        result = db.execute(
            text("SELECT * FROM planos WHERE id_plano = :id"),
            {"id": id_plano}
        )
        plano = result.fetchone()
        
        if not plano:
            raise HTTPException(status_code=404, detail="Plano não encontrado")
        
        # Verificar se há assinaturas vinculadas
        result = db.execute(
            text("SELECT COUNT(*) as total FROM assinaturas WHERE id_plano = :id"),
            {"id": id_plano}
        )
        count = result.fetchone()
        
        if count and count.total > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Não é possível excluir. Existem {count.total} assinaturas vinculadas a este plano."
            )
        
        # Excluir plano
        db.execute(
            text("DELETE FROM planos WHERE id_plano = :id"),
            {"id": id_plano}
        )
        db.commit()
        
        logger.info(f"Plano {id_plano} excluído com sucesso")
        
        return {"success": True, "message": "Plano excluído com sucesso"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao excluir plano: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
