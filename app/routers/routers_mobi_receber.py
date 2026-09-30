# ~/encomenda_v2/backend/app/routers/routers_mobi_receber.py - V2.4.1
# ALTERAÇÃO 2026-09-27: régua de cobrança — bloqueio de recebimento (flag BLOQUEIO_ASSINATURA_ENABLED)
# Router específico para funcionalidades mobile do recebimento de encomendas
# CORREÇÃO V2.4.1: Adiciona codigo_rastreio no nome do arquivo de upload

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import logging

from app.database import get_db
from app.services.assinatura_situacao import checar_bloqueio_recebimento
from app.config import settings
from app.services.whatsapp import whatsapp_service
from app.services.image_storage_service import image_storage_service
from app.api.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/moradores/busca-incremental")
async def busca_incremental_moradores(
    q: str,
    limit: int = 6,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Busca incremental de moradores - retorna resultados enquanto digita
    Otimizado para mobile com limite de 6 resultados
    """
    try:
        # Validar termo de busca
        termo = q.strip()
        if len(termo) < 2:
            return {
                "total": 0,
                "items": [],
                "termo": termo,
                "mensagem": "Digite pelo menos 2 caracteres"
            }

        # Normalizar termo para busca
        termo_like = f"%{termo}%"
        condominio_id = current_user["condominio_id"]

        # Verificar se é busca numérica (apartamento) ou textual (nome)
        eh_numero = termo.isdigit()

        if eh_numero:
            # Busca por apartamento
            query = text("""
                SELECT
                    id, nome, apartamento, bloco, telefone,
                    email, whats_confirmado, ativo
                FROM moradores
                WHERE condominio_id = :condominio_id
                AND ativo = 1
                AND apartamento LIKE :termo
                ORDER BY apartamento
                LIMIT :limit
            """)
        else:
            # Busca por nome - prioriza nomes que começam com o termo
            query = text("""
                (
                    SELECT
                        id, nome, apartamento, bloco, telefone,
                        email, whats_confirmado, ativo,
                        1 as prioridade
                    FROM moradores
                    WHERE condominio_id = :condominio_id
                    AND ativo = 1
                    AND LOWER(nome) LIKE LOWER(:termo_inicio)
                    LIMIT :limit
                )
                UNION ALL
                (
                    SELECT
                        id, nome, apartamento, bloco, telefone,
                        email, whats_confirmado, ativo,
                        2 as prioridade
                    FROM moradores
                    WHERE condominio_id = :condominio_id
                    AND ativo = 1
                    AND LOWER(nome) LIKE LOWER(:termo_contem)
                    AND LOWER(nome) NOT LIKE LOWER(:termo_inicio)
                    LIMIT :limit
                )
                ORDER BY prioridade, nome
                LIMIT :limit
            """)

            result = db.execute(query, {
                "condominio_id": condominio_id,
                "termo_inicio": f"{termo}%",
                "termo_contem": f"%{termo}%",
                "limit": limit
            })

        if eh_numero:
            result = db.execute(query, {
                "condominio_id": condominio_id,
                "termo": termo_like,
                "limit": limit
            })

        moradores = []
        for row in result:
            morador = {
                "id": row.id,
                "nome": row.nome,
                "apartamento": row.apartamento,
                "bloco": row.bloco if row.bloco else None,
                "telefone": row.telefone if row.telefone else None,
                "email": row.email if row.email else None,
                "whats_confirmado": row.whats_confirmado.isoformat() if row.whats_confirmado else None,
                "ativo": bool(row.ativo)
            }
            moradores.append(morador)

        return {
            "total": len(moradores),
            "items": moradores,
            "termo": termo,
            "tipo_busca": "apartamento" if eh_numero else "nome"
        }

    except Exception as e:
        logger.error(f"Erro na busca incremental: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro na busca: {str(e)}"
        )

@router.post("/encomenda/receber")
async def receber_encomenda_mobile(
    encomenda_data: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Endpoint otimizado para recebimento via mobile
    Mesma lógica do create_encomenda mas com respostas mais leves
    """
    try:
        logger.info(f"[MOBILE] Recebendo encomenda: {encomenda_data.get('nome_destinatario')}")

        # Validar dados obrigatórios
        required_fields = ["nome_destinatario", "apartamento"]
        for field in required_fields:
            if field not in encomenda_data or not encomenda_data[field]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Campo obrigatório ausente: {field}"
                )

        # Régua de cobrança: bloqueia só o RECEBIMENTO (flag BLOQUEIO_ASSINATURA_ENABLED)
        checar_bloqueio_recebimento(db, current_user["condominio_id"])

        # Processar imagem da etiqueta se existir
        img_etiqueta_filename = None
        img_etiqueta_server = None
        imagem_etiqueta_base64 = encomenda_data.get("imagem_etiqueta")

        if imagem_etiqueta_base64:
            try:
                logger.info("[MOBILE] Processando imagem da etiqueta...")
                # ✅ CORREÇÃO: Adicionar codigo_rastreio no upload
                resultado_upload = await image_storage_service.process_and_upload_etiqueta(
                    imagem_etiqueta_base64,
                    identificador=encomenda_data.get("nome_destinatario"),
                    codigo_rastreio=encomenda_data.get("codigo_rastreio"),
                )
                img_etiqueta_filename = resultado_upload["nome_personalizado"]
                img_etiqueta_server = resultado_upload["nome_servidor"]
                logger.info(f"[MOBILE] Imagem salva: {img_etiqueta_server}")
            except Exception as e:
                logger.error(f"[MOBILE] Erro ao salvar imagem: {str(e)}")

        # Verificar morador e status WhatsApp
        morador_id = encomenda_data.get("morador_id")
        whats_confirmado = None

        if morador_id:
            check_whats_query = text("""
                SELECT whats_confirmado FROM moradores WHERE id = :morador_id
            """)
            result = db.execute(check_whats_query, {"morador_id": morador_id}).fetchone()
            if result:
                whats_confirmado = result.whats_confirmado

        # Preparar dados OCR
        ocr_data_json = None
        ocr_data = encomenda_data.get("ocr_data")
        if ocr_data and isinstance(ocr_data, dict):
            ocr_data_json = json.dumps(ocr_data)

        # Inserir encomenda
        insert_query = text("""
            INSERT INTO encomendas (
                condominio_id, nome_destinatario, apartamento, bloco, codigo_rastreio,
                remetente, status, data_recebimento, observacoes,
                telefone_morador, img_etiqueta, img_etiqueta_server, ocr_data, morador_id
            ) VALUES (
                :condominio_id, :nome_destinatario, :apartamento, :bloco, :codigo_rastreio,
                :remetente, 'pendente', NOW(), :observacoes,
                :telefone_morador, :img_etiqueta, :img_etiqueta_server, :ocr_data, :morador_id
            )
        """)
        result_insert = db.execute(insert_query, {
            "condominio_id": current_user["condominio_id"],
            "nome_destinatario": encomenda_data.get("nome_destinatario"),
            "apartamento": encomenda_data.get("apartamento"),
            "bloco": encomenda_data.get("bloco"),
            "codigo_rastreio": encomenda_data.get("codigo_rastreio"),
            "remetente": encomenda_data.get("remetente"),
            "observacoes": encomenda_data.get("observacoes"),
            "telefone_morador": encomenda_data.get("telefone_morador"),
            "img_etiqueta": img_etiqueta_filename,
            "img_etiqueta_server": img_etiqueta_server,
            "ocr_data": ocr_data_json,
            "morador_id": morador_id
        })

        # Obter ID direto do resultado do INSERT (seguro mesmo com pool de conexões)
        encomenda_id = result_insert.lastrowid
        if not encomenda_id and hasattr(result_insert, "inserted_primary_key") and result_insert.inserted_primary_key:
            encomenda_id = result_insert.inserted_primary_key[0]

        db.commit()

        if not encomenda_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Encomenda registrada, mas não foi possível obter o ID com segurança"
            ) 


        # Inserir mapeamento de imagem
        if img_etiqueta_filename and img_etiqueta_server:
            try:
                mapeamento_query = text("""
                    INSERT INTO imagens_mapeamento
                    (encomenda_id, tipo, nome_personalizado, nome_servidor)
                    VALUES (:encomenda_id, 'etiqueta', :nome_personalizado, :nome_servidor)
                """)
                db.execute(mapeamento_query, {
                    "encomenda_id": encomenda_id,
                    "nome_personalizado": img_etiqueta_filename,
                    "nome_servidor": img_etiqueta_server
                })
                db.commit()
            except Exception as e:
                logger.error(f"[MOBILE] Erro ao inserir mapeamento: {str(e)}")

        logger.info(f"[MOBILE] Encomenda {encomenda_id} registrada com sucesso")

        # Verificar se o condomínio permite envio de WhatsApp aos moradores
        envio_whatsapp_ativo = True
        try:
            cond_envio = db.execute(
                text("SELECT envio_whatsapp FROM condominios WHERE id = :cid"),
                {"cid": current_user["condominio_id"]}
            ).fetchone()
            envio_whatsapp_ativo = bool(cond_envio and cond_envio.envio_whatsapp != 'N')
        except Exception as e:
            logger.error(f"[MOBILE] Erro ao verificar envio_whatsapp (condominio {current_user['condominio_id']}): {e}")
            envio_whatsapp_ativo = True

        # Enviar WhatsApp se configurado
        telefone = encomenda_data.get("telefone_morador")
        notificar = encomenda_data.get("notificar_whatsapp", True)

        whatsapp_enviado = False
        whatsapp_mensagem = None

        if telefone and notificar and settings.WHATSAPP_ENABLED and envio_whatsapp_ativo:

            try:
                needs_confirmation = whats_confirmado is None

                dados_notificacao = {
                    "nome_destinatario": encomenda_data.get("nome_destinatario"),
                    "apartamento": encomenda_data.get("apartamento"),
                    "bloco": encomenda_data.get("bloco"),
                    "codigo_rastreio": encomenda_data.get("codigo_rastreio"),
                    "remetente": encomenda_data.get("remetente"),
                    "telefone_morador": telefone,
                    "imagem_etiqueta": imagem_etiqueta_base64,
                    "nome_condominio": current_user.get("condominio_nome")
                }

                success, msg = whatsapp_service.send_package_notification(
                    dados_notificacao,
                    needs_confirmation=needs_confirmation
                )

                whatsapp_enviado = success
                whatsapp_mensagem = msg

                if success and needs_confirmation and morador_id:
                    try:
                        update_solicitacao_query = text("""
                            UPDATE moradores
                            SET observacoes = CONCAT(
                                IFNULL(observacoes, ''),
                                '\n[', DATE_FORMAT(NOW(), '%d/%m/%Y %H:%i'), '] ',
                                'Solicitação de confirmação WhatsApp enviada'
                            )
                            WHERE id = :morador_id
                        """)
                        db.execute(update_solicitacao_query, {"morador_id": morador_id})
                        db.commit()
                    except Exception as e:
                        logger.error(f"[MOBILE] Erro ao atualizar solicitação: {e}")

            except Exception as e:
                logger.error(f"[MOBILE] Erro ao enviar WhatsApp: {e}")

        # Resposta otimizada para mobile
        return {
            "success": True,
            "encomenda_id": encomenda_id,
            "status": "pendente",
            "whatsapp": {
                "enviado": whatsapp_enviado,
                "mensagem": whatsapp_mensagem,
                "precisa_confirmacao": whats_confirmado is None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[MOBILE] Erro ao receber encomenda: {str(e)}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao receber encomenda: {str(e)}"
        )

@router.get("/stats/rapidas")
async def get_stats_rapidas(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Estatísticas rápidas otimizadas para mobile"""
    try:
        condominio_id = current_user["condominio_id"]

        # Query única com todas as estatísticas
        query = text("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'pendente' THEN 1 ELSE 0 END) as pendentes,
                SUM(CASE WHEN status = 'entregue' THEN 1 ELSE 0 END) as entregues,
                SUM(CASE WHEN DATE(data_recebimento) = CURDATE() THEN 1 ELSE 0 END) as hoje
            FROM encomendas
            WHERE condominio_id = :condominio_id
        """)

        result = db.execute(query, {"condominio_id": condominio_id}).fetchone()

        return {
            "total": result.total or 0,
            "pendentes": result.pendentes or 0,
            "entregues": result.entregues or 0,
            "recebidas_hoje": result.hoje or 0
        }

    except Exception as e:
        logger.error(f"[MOBILE] Erro ao obter stats: {str(e)}")
        return {
            "total": 0,
            "pendentes": 0,
            "entregues": 0,
            "recebidas_hoje": 0
        }
