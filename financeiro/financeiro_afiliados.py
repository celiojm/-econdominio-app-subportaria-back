"""
Módulo de Administração de Afiliados - Financeiro
Gerenciamento de afiliados, comissões, saques e condomínios indicados
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime, timedelta
import logging

from app.database import get_db
from financeiro.auth_service import require_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/afiliados")


@router.get("/lista")
async def listar_afiliados(
    status: Optional[str] = None,
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Lista todos os afiliados com resumo de dados"""
    try:
        filtro_status = ""
        if status:
            filtro_status = f"AND a.ativo = {1 if status == 'ativo' else 0}"
        
        query = text(f"""
            SELECT 
                a.id,
                a.nome_completo,
                a.email,
                a.whatsapp,
                a.codigo_afiliado,
                a.tipo_comissao,
                a.percentual,
                a.ativo,
                a.is_admin,
                a.data_cadastro,
                COUNT(DISTINCT ac.id) as total_condominios,
                COALESCE(SUM(CASE WHEN acom.status = 'liberada' THEN acom.valor ELSE 0 END), 0) as saldo_disponivel,
                COALESCE(SUM(CASE WHEN acom.status = 'paga' THEN acom.valor ELSE 0 END), 0) as total_pago,
                COALESCE(SUM(CASE WHEN acom.status IN ('liberada', 'paga') THEN acom.valor ELSE 0 END), 0) as total_comissoes
            FROM afiliados a
            LEFT JOIN afiliado_condominios ac ON ac.afiliado_id = a.id
            LEFT JOIN afiliado_comissoes acom ON acom.afiliado_id = a.id
            WHERE 1=1 {filtro_status}
            GROUP BY a.id
            ORDER BY a.data_cadastro DESC
        """)
        
        result = db.execute(query)
        afiliados = []
        
        for row in result:
            afiliados.append({
                'id': row.id,
                'nome_completo': row.nome_completo,
                'email': row.email,
                'whatsapp': row.whatsapp,
                'codigo_afiliado': row.codigo_afiliado,
                'tipo_comissao': row.tipo_comissao,
                'percentual': float(row.percentual),
                'ativo': bool(row.ativo),
                'is_admin': bool(row.is_admin),
                'data_cadastro': row.data_cadastro.isoformat() if row.data_cadastro else None,
                'total_condominios': row.total_condominios or 0,
                'saldo_disponivel': float(row.saldo_disponivel or 0),
                'total_pago': float(row.total_pago or 0),
                'total_comissoes': float(row.total_comissoes or 0)
            })
        
        return {
            'afiliados': afiliados,
            'total': len(afiliados)
        }
    except Exception as e:
        logger.error(f"Erro ao listar afiliados: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{afiliado_id}/detalhes")
async def obter_detalhes_afiliado(
    afiliado_id: int,
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Obtém detalhes completos de um afiliado"""
    try:
        # Dados básicos
        query_afiliado = text("""
            SELECT * FROM afiliados WHERE id = :id
        """)
        afiliado = db.execute(query_afiliado, {'id': afiliado_id}).fetchone()
        
        if not afiliado:
            raise HTTPException(status_code=404, detail="Afiliado não encontrado")
        
        # Condomínios indicados
        query_condominios = text("""
            SELECT 
                ac.id,
                c.nome as condominio,
                c.cidade,
                assin.plano_id,
                p.nome as plano,
                assin.valor_mensalidade,
                assin.status,
                assin.data_inicio,
                ac.comissao_bloqueada,
                ac.motivo_bloqueio,
                COALESCE(SUM(acom.valor), 0) as total_comissao
            FROM afiliado_condominios ac
            LEFT JOIN condominios c ON c.id = ac.condominio_id
            LEFT JOIN assinaturas assin ON assin.condominio_id = c.id AND assin.ativa = 1
            LEFT JOIN planos p ON p.id = assin.plano_id
            LEFT JOIN afiliado_comissoes acom ON acom.afiliado_id = ac.afiliado_id 
                AND acom.condominio_id = ac.condominio_id
            WHERE ac.afiliado_id = :afiliado_id
            GROUP BY ac.id
            ORDER BY ac.data_indicacao DESC
        """)
        condominios = db.execute(query_condominios, {'afiliado_id': afiliado_id}).fetchall()
        
        # Comissões
        query_comissoes = text("""
            SELECT 
                ac.id,
                c.nome as condominio,
                ac.tipo,
                ac.valor,
                ac.status,
                ac.data_geracao,
                ac.data_liberacao,
                ac.data_pagamento,
                ac.referencia_mes
            FROM afiliado_comissoes ac
            LEFT JOIN condominios c ON c.id = ac.condominio_id
            WHERE ac.afiliado_id = :afiliado_id
            ORDER BY ac.data_geracao DESC
            LIMIT 50
        """)
        comissoes = db.execute(query_comissoes, {'afiliado_id': afiliado_id}).fetchall()
        
        # Saques
        query_saques = text("""
            SELECT * FROM afiliado_saques
            WHERE afiliado_id = :afiliado_id
            ORDER BY data_solicitacao DESC
            LIMIT 20
        """)
        saques = db.execute(query_saques, {'afiliado_id': afiliado_id}).fetchall()
        
        # Resumo financeiro
        query_resumo = text("""
            SELECT 
                COALESCE(SUM(CASE WHEN status = 'pendente' THEN valor ELSE 0 END), 0) as pendente,
                COALESCE(SUM(CASE WHEN status = 'liberada' THEN valor ELSE 0 END), 0) as liberada,
                COALESCE(SUM(CASE WHEN status = 'paga' THEN valor ELSE 0 END), 0) as paga,
                COALESCE(SUM(CASE WHEN status = 'cancelada' THEN valor ELSE 0 END), 0) as cancelada
            FROM afiliado_comissoes
            WHERE afiliado_id = :afiliado_id
        """)
        resumo = db.execute(query_resumo, {'afiliado_id': afiliado_id}).fetchone()
        
        return {
            'afiliado': dict(afiliado._mapping),
            'condominios': [dict(c._mapping) for c in condominios],
            'comissoes': [dict(c._mapping) for c in comissoes],
            'saques': [dict(s._mapping) for s in saques],
            'resumo_financeiro': dict(resumo._mapping) if resumo else {}
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter detalhes do afiliado: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{afiliado_id}/status")
async def alterar_status_afiliado(
    afiliado_id: int,
    ativo: bool,
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Ativa ou desativa um afiliado"""
    try:
        query = text("""
            UPDATE afiliados 
            SET ativo = :ativo
            WHERE id = :id
        """)
        db.execute(query, {'id': afiliado_id, 'ativo': ativo})
        db.commit()
        
        return {'success': True, 'message': f'Afiliado {"ativado" if ativo else "desativado"} com sucesso'}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao alterar status do afiliado: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{afiliado_id}/comissoes/{comissao_id}/liberar")
async def liberar_comissao(
    afiliado_id: int,
    comissao_id: int,
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Libera uma comissão pendente"""
    try:
        query = text("""
            UPDATE afiliado_comissoes
            SET status = 'liberada',
                data_liberacao = NOW()
            WHERE id = :comissao_id 
                AND afiliado_id = :afiliado_id
                AND status = 'pendente'
        """)
        result = db.execute(query, {
            'comissao_id': comissao_id,
            'afiliado_id': afiliado_id
        })
        db.commit()
        
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Comissão não encontrada ou já liberada")
        
        return {'success': True, 'message': 'Comissão liberada com sucesso'}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao liberar comissão: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{afiliado_id}/saques/{saque_id}/aprovar")
async def aprovar_saque(
    afiliado_id: int,
    saque_id: int,
    observacao: Optional[str] = None,
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Aprova um saque solicitado"""
    try:
        query = text("""
            UPDATE afiliado_saques
            SET status = 'aprovado',
                data_aprovacao = NOW(),
                observacao = :observacao
            WHERE id = :saque_id
                AND afiliado_id = :afiliado_id
                AND status = 'solicitado'
        """)
        result = db.execute(query, {
            'saque_id': saque_id,
            'afiliado_id': afiliado_id,
            'observacao': observacao
        })
        db.commit()
        
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Saque não encontrado ou já processado")
        
        return {'success': True, 'message': 'Saque aprovado com sucesso'}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao aprovar saque: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{afiliado_id}/saques/{saque_id}/pagar")
async def marcar_saque_pago(
    afiliado_id: int,
    saque_id: int,
    comprovante: Optional[str] = None,
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Marca um saque como pago"""
    try:
        query = text("""
            UPDATE afiliado_saques
            SET status = 'pago',
                data_pagamento = NOW(),
                comprovante = :comprovante
            WHERE id = :saque_id
                AND afiliado_id = :afiliado_id
                AND status = 'aprovado'
        """)
        result = db.execute(query, {
            'saque_id': saque_id,
            'afiliado_id': afiliado_id,
            'comprovante': comprovante
        })
        db.commit()
        
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Saque não encontrado ou não aprovado")
        
        return {'success': True, 'message': 'Saque marcado como pago'}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao marcar saque como pago: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
async def dashboard_afiliados(
    db = Depends(get_db),
    current_user = Depends(require_auth)
):
    """Dashboard com métricas gerais dos afiliados"""
    try:
        query = text("""
            SELECT 
                COUNT(DISTINCT a.id) as total_afiliados,
                COUNT(DISTINCT CASE WHEN a.ativo = 1 THEN a.id END) as afiliados_ativos,
                COUNT(DISTINCT ac.condominio_id) as total_condominios_indicados,
                COALESCE(SUM(CASE WHEN acom.status = 'pendente' THEN acom.valor ELSE 0 END), 0) as comissoes_pendentes,
                COALESCE(SUM(CASE WHEN acom.status = 'liberada' THEN acom.valor ELSE 0 END), 0) as comissoes_liberadas,
                COALESCE(SUM(CASE WHEN acom.status = 'paga' THEN acom.valor ELSE 0 END), 0) as comissoes_pagas,
                COUNT(DISTINCT CASE WHEN s.status = 'solicitado' THEN s.id END) as saques_pendentes,
                COALESCE(SUM(CASE WHEN s.status = 'solicitado' THEN s.valor ELSE 0 END), 0) as valor_saques_pendentes
            FROM afiliados a
            LEFT JOIN afiliado_condominios ac ON ac.afiliado_id = a.id
            LEFT JOIN afiliado_comissoes acom ON acom.afiliado_id = a.id
            LEFT JOIN afiliado_saques s ON s.afiliado_id = a.id
        """)
        
        result = db.execute(query).fetchone()
        
        return dict(result._mapping) if result else {}
    except Exception as e:
        logger.error(f"Erro ao obter dashboard de afiliados: {e}")
        raise HTTPException(status_code=500, detail=str(e))
