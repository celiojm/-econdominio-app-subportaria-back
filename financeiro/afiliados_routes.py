# Cole o conteúdo do arquivo afiliados_routes_FINAL.py
# ================================================================================
# ARQUIVO: afiliados_routes.py (COMPATÍVEL COM FRONTEND)
# PASTA:   ~/backend/financeiro/
# DESCRIÇÃO: Endpoints para gerenciar afiliados no painel financeiro
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import datetime
from app.database import get_db
import logging
import traceback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/financeiro/afiliados", tags=["financeiro-afiliados"])


@router.get("/lista")
def listar_afiliados(
    db: Session = Depends(get_db),
    ativo: Optional[bool] = None,
    status: Optional[str] = None,  # Frontend envia 'ativo' ou 'inativo'
    busca: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0
):
    """Lista todos os afiliados para o painel financeiro"""
    try:
        # Converter status string para bool
        if status == 'ativo':
            ativo = True
        elif status == 'inativo':
            ativo = False

        query_str = """
            SELECT 
                a.id,
                a.nome_completo,
                a.email,
                a.whatsapp,
                a.cpf,
                a.conta_pix,
                a.tipo_pix,
                a.codigo_afiliado,
                a.tipo_comissao,
                a.percentual,
                a.ativo,
                a.data_cadastro,
                a.observacoes,
                COUNT(DISTINCT ac.id) as total_condominios
            FROM afiliados a
            LEFT JOIN afiliado_condominios ac ON a.id = ac.afiliado_id AND ac.ativo = 1
        """
        
        where_clauses = []
        params = {}
        
        if ativo is not None:
            where_clauses.append("a.ativo = :ativo")
            params["ativo"] = 1 if ativo else 0
        
        if busca:
            where_clauses.append("""
                (a.nome_completo LIKE :busca 
                 OR a.email LIKE :busca 
                 OR a.codigo_afiliado LIKE :busca
                 OR a.cpf LIKE :busca)
            """)
            params["busca"] = f"%{busca}%"
        
        if where_clauses:
            query_str += " WHERE " + " AND ".join(where_clauses)
        
        query_str += """
            GROUP BY a.id
            ORDER BY a.data_cadastro DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = limit
        params["offset"] = offset
        
        result = db.execute(text(query_str), params).fetchall()
        
        # Contar total
        count_query = "SELECT COUNT(*) FROM afiliados a"
        if where_clauses:
            count_query += " WHERE " + " AND ".join(where_clauses)
        
        count_params = {k: v for k, v in params.items() if k not in ["limit", "offset"]}
        total = db.execute(text(count_query), count_params).scalar()
        
        afiliados = []
        for row in result:
            afiliados.append({
                "id": row.id,
                "nome_completo": row.nome_completo,
                "email": row.email,
                "whatsapp": row.whatsapp,
                "cpf": row.cpf,
                "codigo_afiliado": row.codigo_afiliado,
                "tipo_comissao": row.tipo_comissao,
                "percentual": float(row.percentual) if row.percentual else 100.0,
                "ativo": bool(row.ativo),
                "data_cadastro": str(row.data_cadastro) if row.data_cadastro else None,
                "observacoes": row.observacoes,
                "pix": {
                    "chave": row.conta_pix,
                    "tipo": row.tipo_pix
                },
                # Campos que o frontend espera diretamente:
                "total_condominios": row.total_condominios or 0,
                "saldo_disponivel": 0.0,  # TODO: calcular quando tabela de comissões estiver pronta
                "total_pago": 0.0,  # TODO: calcular quando tabela de comissões estiver pronta
            })
        
        return {
            "success": True,
            "afiliados": afiliados, 
            "total": total,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"Erro ao listar afiliados: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erro ao listar afiliados: {str(e)}")


@router.get("/dashboard")
def dashboard_afiliados(db: Session = Depends(get_db)):
    """Dashboard com métricas gerais de afiliados"""
    try:
        # Total de afiliados
        total_afiliados = db.execute(text("SELECT COUNT(*) FROM afiliados")).scalar() or 0
        afiliados_ativos = db.execute(text("SELECT COUNT(*) FROM afiliados WHERE ativo = 1")).scalar() or 0
        
        # Total de indicações/condomínios
        total_condominios_indicados = db.execute(text("SELECT COUNT(*) FROM afiliado_condominios WHERE ativo = 1")).scalar() or 0
        
        # Indicações este mês
        indicacoes_mes = db.execute(text("""
            SELECT COUNT(*) FROM afiliado_condominios 
            WHERE MONTH(data_vinculo) = MONTH(NOW()) 
            AND YEAR(data_vinculo) = YEAR(NOW())
        """)).scalar() or 0
        
        # Top afiliados (por indicações)
        top_afiliados_query = text("""
            SELECT 
                a.id,
                a.nome_completo,
                a.codigo_afiliado,
                COUNT(ac.id) as total_indicacoes
            FROM afiliados a
            LEFT JOIN afiliado_condominios ac ON a.id = ac.afiliado_id AND ac.ativo = 1
            WHERE a.ativo = 1
            GROUP BY a.id
            ORDER BY total_indicacoes DESC
            LIMIT 5
        """)
        
        top_result = db.execute(top_afiliados_query).fetchall()
        top_afiliados = []
        for row in top_result:
            top_afiliados.append({
                "id": row.id,
                "nome": row.nome_completo,
                "codigo": row.codigo_afiliado,
                "indicacoes": row.total_indicacoes or 0,
                "comissoes": 0
            })
        
        # Indicações recentes
        recentes_query = text("""
            SELECT 
                ac.id,
                ac.data_vinculo,
                a.nome_completo as afiliado_nome,
                c.nome as condominio_nome,
                c.cidade,
                c.estado
            FROM afiliado_condominios ac
            JOIN afiliados a ON ac.afiliado_id = a.id
            JOIN condominios c ON ac.condominio_id = c.id
            ORDER BY ac.data_vinculo DESC
            LIMIT 10
        """)
        
        recentes_result = db.execute(recentes_query).fetchall()
        indicacoes_recentes = []
        for row in recentes_result:
            indicacoes_recentes.append({
                "id": row.id,
                "data": str(row.data_vinculo) if row.data_vinculo else None,
                "afiliado": row.afiliado_nome,
                "condominio": row.condominio_nome,
                "local": f"{row.cidade}/{row.estado}" if row.cidade else None
            })
        
        return {
            "success": True,
            # Campos que o frontend espera:
            "total_afiliados": total_afiliados,
            "afiliados_ativos": afiliados_ativos,
            "total_condominios_indicados": total_condominios_indicados,
            "comissoes_liberadas": 0.0,  # TODO: implementar
            "comissoes_pendentes": 0.0,  # TODO: implementar
            "comissoes_pagas": 0.0,  # TODO: implementar
            # Campos extras:
            "metricas": {
                "total_afiliados": total_afiliados,
                "afiliados_ativos": afiliados_ativos,
                "total_indicacoes": total_condominios_indicados,
                "indicacoes_mes": indicacoes_mes,
            },
            "top_afiliados": top_afiliados,
            "indicacoes_recentes": indicacoes_recentes
        }
        
    except Exception as e:
        logger.error(f"Erro ao gerar dashboard: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erro ao gerar dashboard: {str(e)}")


@router.get("/{afiliado_id}")
@router.get("/{afiliado_id}/detalhes")
def detalhe_afiliado(afiliado_id: int, db: Session = Depends(get_db)):
    """Retorna detalhes completos de um afiliado"""
    try:
        query = text("""
            SELECT 
                a.*,
                COUNT(DISTINCT ac.id) as total_condominios
            FROM afiliados a
            LEFT JOIN afiliado_condominios ac ON a.id = ac.afiliado_id AND ac.ativo = 1
            WHERE a.id = :id
            GROUP BY a.id
        """)
        
        row = db.execute(query, {"id": afiliado_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Afiliado não encontrado")
        
        # Buscar condomínios
        condominios_query = text("""
            SELECT 
                ac.id as vinculo_id,
                ac.data_vinculo,
                ac.ativo as vinculo_ativo,
                c.id as condominio_id,
                c.nome,
                c.cnpj,
                c.cidade,
                c.estado,
                c.total_apartamentos,
                c.assinatura_status
            FROM afiliado_condominios ac
            JOIN condominios c ON ac.condominio_id = c.id
            WHERE ac.afiliado_id = :afiliado_id
            ORDER BY ac.data_vinculo DESC
        """)
        
        condominios_result = db.execute(condominios_query, {"afiliado_id": afiliado_id}).fetchall()
        
        condominios = []
        for c in condominios_result:
            condominios.append({
                "vinculo_id": c.vinculo_id,
                "data_vinculo": str(c.data_vinculo) if c.data_vinculo else None,
                "ativo": bool(c.vinculo_ativo),
                "condominio_id": c.condominio_id,
                # Campos que o frontend espera:
                "condominio": c.nome,
                "cidade": c.cidade,
                "plano": c.assinatura_status or "trial",
                "comissao_bloqueada": False,
                "motivo_bloqueio": None,
                "total_comissao": 0.0
            })
        
        return {
            "success": True,
            "afiliado": {
                "id": row.id,
                "nome_completo": row.nome_completo,
                "email": row.email,
                "whatsapp": row.whatsapp,
                "cpf": row.cpf,
                "codigo_afiliado": row.codigo_afiliado,
                "tipo_comissao": row.tipo_comissao,
                "percentual": float(row.percentual) if row.percentual else 100.0,
                "ativo": bool(row.ativo),
                "data_cadastro": str(row.data_cadastro) if row.data_cadastro else None,
                "pix": {
                    "chave": row.conta_pix,
                    "tipo": row.tipo_pix
                },
                "total_condominios": row.total_condominios or 0
            },
            "condominios": condominios,
            "comissoes": [],  # TODO: implementar quando tabela estiver pronta
            "saques": [],  # TODO: implementar quando tabela estiver pronta
            "resumo_financeiro": {
                "pendente": 0.0,
                "liberada": 0.0,
                "paga": 0.0,
                "cancelada": 0.0
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar afiliado: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Erro ao buscar afiliado: {str(e)}")


@router.put("/{afiliado_id}/status")
def alterar_status_afiliado(afiliado_id: int, ativo: bool, db: Session = Depends(get_db)):
    """Ativa ou desativa um afiliado"""
    try:
        # Verificar se existe
        afiliado = db.execute(text("SELECT id FROM afiliados WHERE id = :id"), {"id": afiliado_id}).fetchone()
        if not afiliado:
            raise HTTPException(status_code=404, detail="Afiliado não encontrado")
        
        # Atualizar status
        db.execute(text("""
            UPDATE afiliados SET ativo = :ativo, data_atualizacao = NOW() WHERE id = :id
        """), {"ativo": 1 if ativo else 0, "id": afiliado_id})
        db.commit()
        
        return {"success": True, "message": f"Afiliado {'ativado' if ativo else 'desativado'} com sucesso"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao alterar status: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao alterar status: {str(e)}")
