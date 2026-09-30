# ================================================================================
# ARQUIVO: afiliado_routes.py
# PASTA:   ~/backend/afiliado/
# ================================================================================

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Optional, List
import logging
import re
from datetime import datetime
from sqlalchemy import text
from app.database import get_db
from .afiliado_models import (
    AfiliadoCreate, AfiliadoUpdate, AfiliadoResponse,
    DashboardAfiliadoResponse, CondominioIndicadoResponse,
    ComissaoResponse, ComissaoListResponse,
    SaqueCreate, SaqueResponse, LinkAfiliadoResponse
)
from .afiliado_service import (
    gerar_codigo_afiliado, calcular_percentual_padrao,
    calcular_saldo_disponivel, get_dashboard_data,
    processar_saque, aprovar_saque, confirmar_pagamento_saque,
    registrar_log_afiliado, liberar_comissoes_pendentes
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/afiliados", tags=["afiliados"])


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")


@router.post("/cadastro-publico", status_code=status.HTTP_201_CREATED)
def cadastro_publico_afiliado(data: dict, request: Request, db: Session = Depends(get_db)):
    """Cadastro público de afiliado - envia confirmação por WhatsApp"""
    try:
        import secrets
        import string
        from argon2 import PasswordHasher

        # Validar campos
        required = ['nome_completo', 'email', 'whatsapp', 'cpf']
        for field in required:
            if not data.get(field):
                raise HTTPException(status_code=400, detail=f"Campo obrigatório: {field}")

        # Verificar duplicidade
        existing = db.execute(text("""
            SELECT id FROM afiliados 
            WHERE email = :email OR whatsapp = :whatsapp
        """), {"email": data['email'], "whatsapp": data['whatsapp']}).fetchone()

        if existing:
            raise HTTPException(status_code=400, detail="Email ou WhatsApp já cadastrado")

        # Gerar códigos
        codigo_confirmacao = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(32))
        codigo_afiliado = gerar_codigo_afiliado()
        
        while db.execute(text("SELECT id FROM afiliados WHERE codigo_afiliado = :codigo"),
                        {"codigo": codigo_afiliado}).fetchone():
            codigo_afiliado = gerar_codigo_afiliado()

        # Gerar senha
        senha_temp = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
        ph = PasswordHasher()
        senha_hash = ph.hash(senha_temp)

        # 1. Criar em afiliado_usuarios
        user_result = db.execute(text("""
            INSERT INTO afiliado_usuarios (email, senha_hash, nome, ativo)
            VALUES (:email, :senha_hash, :nome, 0)
        """), {
            "email": data['email'],
            "senha_hash": senha_hash,
            "nome": data['nome_completo']
        })
        afiliado_usuario_id = user_result.lastrowid

        # 2. Criar em afiliados
        tipo_comissao = data.get('tipo_comissao', 'primeiro_mes')
        percentual = 100.00 if tipo_comissao == 'primeiro_mes' else 20.00

        db.execute(text("""
            INSERT INTO afiliados (
                afiliado_usuario_id, nome_completo, whatsapp, email, cpf,
                conta_pix, tipo_pix, codigo_afiliado, tipo_comissao, percentual,
                ativo, codigo_confirmacao, data_cadastro
            ) VALUES (
                :afiliado_usuario_id, :nome, :whatsapp, :email, :cpf,
                :conta_pix, :tipo_pix, :codigo, :tipo, :percentual,
                0, :confirmacao, NOW()
            )
        """), {
            "afiliado_usuario_id": afiliado_usuario_id,
            "nome": data['nome_completo'],
            "whatsapp": data['whatsapp'],
            "email": data['email'],
            "cpf": data['cpf'],
            "conta_pix": data.get('conta_pix', data['cpf']),
            "tipo_pix": data.get('tipo_pix', 'cpf'),
            "codigo": codigo_afiliado,
            "tipo": tipo_comissao,
            "percentual": percentual,
            "confirmacao": codigo_confirmacao
        })
        
        db.commit()

        # Enviar WhatsApp
        link = f"https://afiliado.econdominio.app.br/confirmar/{codigo_confirmacao}"
        mensagem = f"""🎉 *Bem-vindo ao Programa de Afiliados e-Condomínio!*

Olá *{data['nome_completo']}*!

Para ativar sua conta, clique no link:
{link}

Após confirmar, você receberá suas credenciais de acesso."""

        whatsapp = data['whatsapp']
        if not whatsapp.startswith('55'):
            whatsapp = '55' + re.sub(r'[^0-9]', '', whatsapp)

        try:
            from app.services.whatsapp import WhatsAppService
            WhatsAppService().send_text_message(whatsapp, mensagem)
        except Exception as e:
            logger.error(f"Erro WhatsApp: {e}")

        return {"message": "Cadastro realizado! Confirme através do WhatsApp.", "whatsapp": data['whatsapp']}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro no cadastro: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confirmar/{codigo}")
def confirmar_cadastro(codigo: str, db: Session = Depends(get_db)):
    """Confirma cadastro e envia credenciais"""
    try:
        import secrets
        import string
        from argon2 import PasswordHasher

        # Buscar afiliado
        afiliado = db.execute(text("""
            SELECT a.*, au.senha_hash
            FROM afiliados a
            JOIN afiliado_usuarios au ON au.id = a.afiliado_usuario_id
            WHERE a.codigo_confirmacao = :codigo AND a.ativo = 0
        """), {"codigo": codigo}).fetchone()

        if not afiliado:
            raise HTTPException(status_code=404, detail="Link inválido ou já utilizado")

        # Gerar nova senha
        senha_nova = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
        ph = PasswordHasher()
        senha_hash = ph.hash(senha_nova)

        # Ativar afiliado_usuarios
        db.execute(text("""
            UPDATE afiliado_usuarios 
            SET ativo = 1, senha_hash = :senha_hash
            WHERE id = :id
        """), {"id": afiliado.afiliado_usuario_id, "senha_hash": senha_hash})

        # Ativar afiliado
        db.execute(text("""
            UPDATE afiliados
            SET ativo = 1, codigo_confirmacao = NULL
            WHERE id = :id
        """), {"id": afiliado.id})

        db.commit()

        # Enviar credenciais
        mensagem = f"""✅ *Cadastro Confirmado!*

Olá *{afiliado.nome_completo}*!

*Suas credenciais:*
📧 Email: {afiliado.email}
📱 WhatsApp: {afiliado.whatsapp}
🔑 Senha: {senha_nova}

*Acesse:*
https://afiliado.econdominio.app.br/login

*Seu link:*
https://admin.econdominio.com.br/cadastro_cliente?afiliado={afiliado.codigo_afiliado}"""

        whatsapp = afiliado.whatsapp
        if not whatsapp.startswith('55'):
            whatsapp = '55' + whatsapp

        try:
            from app.services.whatsapp import WhatsAppService
            WhatsAppService().send_text_message(whatsapp, mensagem)
        except Exception as e:
            logger.error(f"Erro WhatsApp: {e}")

        return {"message": "Cadastro confirmado! Credenciais enviadas por WhatsApp.", "redirect": "/login"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na confirmação: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/meu-perfil/{usuario_id}", response_model=AfiliadoResponse)
def obter_perfil_afiliado(usuario_id: int, db: Session = Depends(get_db)):
    """Obtém perfil do afiliado"""
    try:
        afiliado = db.execute(text("""
            SELECT
                id, afiliado_usuario_id, nome_completo, whatsapp, email, cpf,
                conta_pix, tipo_pix, codigo_afiliado, tipo_comissao,
                percentual, ativo, is_admin, data_cadastro
            FROM afiliados
            WHERE id = :usuario_id AND ativo = 1
        """), {"usuario_id": usuario_id}).fetchone()

        if not afiliado:
            raise HTTPException(status_code=404, detail="Perfil não encontrado")

        stats = db.execute(text("""
            SELECT
                COUNT(DISTINCT ac.condominio_id) as total_indicacoes,
                COALESCE(SUM(com.valor_comissao), 0) as total_comissoes
            FROM afiliados a
            LEFT JOIN afiliado_condominios ac ON ac.afiliado_id = a.id AND ac.ativo = 1
            LEFT JOIN afiliado_comissoes com ON com.afiliado_id = a.id AND com.status != 'cancelada'
            WHERE a.id = :afiliado_id
        """), {"afiliado_id": afiliado.id}).fetchone()

        saldo = calcular_saldo_disponivel(db, afiliado.id)
        link = f"https://admin.econdominio.com.br/cadastro_cliente?afiliado={afiliado.codigo_afiliado}"

        return {
            "id": afiliado.id,
            "usuario_id": afiliado.afiliado_usuario_id,
            "nome_completo": afiliado.nome_completo,
            "whatsapp": afiliado.whatsapp,
            "email": afiliado.email,
            "cpf": afiliado.cpf,
            "conta_pix": afiliado.conta_pix,
            "tipo_pix": afiliado.tipo_pix,
            "codigo_afiliado": afiliado.codigo_afiliado,
            "tipo_comissao": afiliado.tipo_comissao,
            "percentual": float(afiliado.percentual),
            "ativo": bool(afiliado.ativo),
            "is_admin": int(afiliado.is_admin),
            "data_cadastro": afiliado.data_cadastro.isoformat(),
            "link_afiliado": link,
            "total_indicacoes": stats.total_indicacoes or 0,
            "total_comissoes": float(stats.total_comissoes or 0),
            "saldo_disponivel": float(saldo)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter perfil: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Manter outras rotas existentes...
