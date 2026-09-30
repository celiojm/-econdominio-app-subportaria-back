# ================================================================================
# ARQUIVO: indicacao_routes.py (COMPATÍVEL COM FRONTEND)
# PASTA:   ~/backend/afiliado/
# DESCRIÇÃO: Endpoint para registrar indicações de afiliados
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
import logging
import traceback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/afiliados", tags=["afiliados-indicacao"])


def get_client_ip(request: Request) -> str:
    """Obtém IP do cliente, considerando proxy reverso"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")[:500]


@router.post("/registrar-indicacao")
def registrar_indicacao(data: dict, request: Request, db: Session = Depends(get_db)):
    """
    Registra vínculo entre afiliado e condomínio quando há indicação
    """
    try:
        codigo_afiliado = data.get('codigo_afiliado')
        condominio_id = data.get('condominio_id')

        logger.info(f"Registrando indicação: código={codigo_afiliado}, condominio_id={condominio_id}")

        if not codigo_afiliado or not condominio_id:
            raise HTTPException(
                status_code=400, 
                detail="Código do afiliado e ID do condomínio são obrigatórios"
            )

        # Buscar afiliado pelo código
        afiliado = db.execute(text("""
            SELECT id, nome_completo FROM afiliados
            WHERE codigo_afiliado = :codigo AND ativo = 1
        """), {"codigo": codigo_afiliado}).fetchone()

        if not afiliado:
            logger.warning(f"Código de afiliado inválido ou inativo: {codigo_afiliado}")
            raise HTTPException(status_code=404, detail="Código de afiliado inválido")

        # Verificar se condomínio existe
        condominio = db.execute(text("""
            SELECT id, nome FROM condominios WHERE id = :id
        """), {"id": condominio_id}).fetchone()

        if not condominio:
            logger.warning(f"Condomínio não encontrado: ID={condominio_id}")
            raise HTTPException(status_code=404, detail="Condomínio não encontrado")

        # Verificar se já existe vínculo para este condomínio
        vinculo_existente = db.execute(text("""
            SELECT id, afiliado_id FROM afiliado_condominios
            WHERE condominio_id = :condominio_id
        """), {"condominio_id": condominio_id}).fetchone()

        if vinculo_existente:
            logger.info(f"Condomínio {condominio_id} já possui vínculo (ID={vinculo_existente.id})")
            return {
                "success": True,
                "message": "Condomínio já vinculado a um afiliado",
                "vinculo_existente": True,
                "vinculo_id": vinculo_existente.id
            }

        # Criar vínculo
        ip = get_client_ip(request)
        user_agent = get_user_agent(request)

        result = db.execute(text("""
            INSERT INTO afiliado_condominios (
                afiliado_id, condominio_id, data_vinculo,
                ip_origem, user_agent, ativo
            ) VALUES (
                :afiliado_id, :condominio_id, NOW(),
                :ip, :user_agent, 1
            )
        """), {
            "afiliado_id": afiliado.id,
            "condominio_id": condominio_id,
            "ip": ip,
            "user_agent": user_agent
        })

        db.commit()
        vinculo_id = result.lastrowid

        logger.info(f"✅ Vínculo criado com sucesso: ID={vinculo_id}")

        # Tentar registrar log (opcional)
        try:
            db.execute(text("""
                INSERT INTO afiliado_logs (
                    afiliado_id, acao, descricao, ip_origem, user_agent
                ) VALUES (
                    :afiliado_id, 'indicacao_registrada',
                    :descricao, :ip, :user_agent
                )
            """), {
                "afiliado_id": afiliado.id,
                "descricao": f"Indicou condomínio: {condominio.nome} (ID: {condominio_id})",
                "ip": ip,
                "user_agent": user_agent
            })
            db.commit()
        except Exception as log_error:
            logger.warning(f"Não foi possível registrar log: {log_error}")

        return {
            "success": True,
            "message": "Indicação registrada com sucesso",
            "vinculo_id": vinculo_id,
            "afiliado": {
                "id": afiliado.id,
                "nome": afiliado.nome_completo,
                "codigo": codigo_afiliado
            },
            "condominio": {
                "id": condominio_id,
                "nome": condominio.nome
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao registrar indicação: {e}")
        logger.error(traceback.format_exc())
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Erro ao processar indicação: {str(e)}"
        )


@router.get("/validar-codigo/{codigo}")
def validar_codigo_afiliado(codigo: str, db: Session = Depends(get_db)):
    """Valida se um código de afiliado existe e está ativo"""
    try:
        afiliado = db.execute(text("""
            SELECT id, nome_completo, codigo_afiliado
            FROM afiliados
            WHERE codigo_afiliado = :codigo AND ativo = 1
        """), {"codigo": codigo}).fetchone()

        if not afiliado:
            return {
                "valido": False,
                "message": "Código inválido"
            }

        return {
            "valido": True,
            "afiliado": {
                "id": afiliado.id,
                "nome": afiliado.nome_completo,
                "codigo": afiliado.codigo_afiliado
            }
        }
    except Exception as e:
        logger.error(f"Erro ao validar código: {e}")
        raise HTTPException(status_code=500, detail="Erro ao validar código")


@router.get("/{afiliado_id}/condominios")
def listar_condominios_afiliado(afiliado_id: int, db: Session = Depends(get_db)):
    """
    Lista condomínios indicados por um afiliado específico.
    IMPORTANTE: Retorna ARRAY direto para compatibilidade com frontend!
    """
    try:
        # Verificar se afiliado existe
        afiliado = db.execute(text("""
            SELECT id, nome_completo, codigo_afiliado, tipo_comissao, percentual
            FROM afiliados WHERE id = :id
        """), {"id": afiliado_id}).fetchone()

        if not afiliado:
            # Retorna array vazio se não encontrar (frontend espera array)
            return []

        # Buscar condomínios vinculados
        result = db.execute(text("""
            SELECT 
                ac.id as vinculo_id,
                ac.condominio_id,
                c.nome,
                c.cnpj,
                c.cidade,
                c.estado,
                c.total_apartamentos,
                c.assinatura_status,
                c.data_cadastro as data_cadastro_condominio,
                ac.data_vinculo,
                ac.ativo as vinculo_ativo
            FROM afiliado_condominios ac
            JOIN condominios c ON ac.condominio_id = c.id
            WHERE ac.afiliado_id = :afiliado_id
            ORDER BY ac.data_vinculo DESC
        """), {"afiliado_id": afiliado_id}).fetchall()

        # Formatar no formato esperado pelo frontend
        condominios = []
        for row in result:
            # Determinar status baseado em assinatura_status
            status_map = {
                'ativa': 'ativo',
                'trial': 'trial',
                'inadimplente': 'inadimplente',
                'cancelada': 'cancelado',
                'suspensa': 'inadimplente'
            }
            status = status_map.get(row.assinatura_status, 'trial')
            
            # Simular se pagamento está ok (baseado no status)
            pagamento_ok = status == 'ativo'
            
            condominios.append({
                # Campos esperados pelo frontend MeusCondominios.jsx
                "nome": row.nome,
                "cidade": row.cidade,
                "plano": row.assinatura_status or "trial",
                "status": status,
                "pagamento_ok": pagamento_ok,
                "comissao": 0.0,  # TODO: calcular comissão real
                "comissao_bloqueada": False,
                "data_cadastro": str(row.data_cadastro_condominio)[:10] if row.data_cadastro_condominio else None,
                "primeiro_pagamento": None,  # TODO: implementar
                "tipo_comissao": afiliado.tipo_comissao,
                "historico_cobrancas": [],  # TODO: implementar
                "historico_comissoes": [],  # TODO: implementar
                # Campos extras
                "vinculo_id": row.vinculo_id,
                "condominio_id": row.condominio_id,
                "cnpj": row.cnpj,
                "estado": row.estado,
                "total_apartamentos": row.total_apartamentos,
                "data_vinculo": str(row.data_vinculo) if row.data_vinculo else None,
                "ativo": bool(row.vinculo_ativo)
            })

        # IMPORTANTE: Frontend espera ARRAY diretamente, não objeto!
        return condominios

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao listar condomínios do afiliado: {e}")
        logger.error(traceback.format_exc())
        # Retorna array vazio em caso de erro
        return []
