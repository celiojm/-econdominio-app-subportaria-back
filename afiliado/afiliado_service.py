from sqlalchemy import text
# ================================================================================
# ARQUIVO: afiliado_service.py
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/afiliado_service.py
# ================================================================================
# DESCRIÇÃO: Serviço com lógica de negócio para o sistema de afiliados
# ================================================================================

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from datetime import datetime, timedelta
from decimal import Decimal
import uuid
import logging
from typing import Optional, Dict, List
import json

logger = logging.getLogger(__name__)


def gerar_codigo_afiliado() -> str:
    """
    Gera código único alfanumérico para o afiliado
    Formato: 8 caracteres (letras maiúsculas + números)
    Exemplo: A7K9M2X4
    """
    import random
    import string
    
    # Letras maiúsculas + números
    chars = string.ascii_uppercase + string.digits
    
    # Gerar código de 8 caracteres
    codigo = ''.join(random.choice(chars) for _ in range(8))
    
    return codigo



def calcular_percentual_padrao(tipo_comissao: str) -> Decimal:
    """Retorna o percentual padrão baseado no tipo de comissão"""
    if tipo_comissao == 'primeiro_mes':
        return Decimal('100.00')
    elif tipo_comissao == 'recorrente':
        return Decimal('20.00')
    return Decimal('100.00')


def verificar_autoindicacao(
    db: Session,
    afiliado_usuario_id: int,
    condominio_id: int
) -> bool:
    """
    Verifica se o afiliado tem vínculo operacional com o condomínio
    (síndico, operador ou usuário vinculado)
    """
    # Verificar se é operador do condomínio
    result = db.execute(text("""
        SELECT 1 FROM mobile_operadores 
        WHERE condominio_id = :condominio_id 
        AND id = :usuario_id
        LIMIT 1
        """), {"condominio_id": condominio_id, "usuario_id": afiliado_usuario_id}).fetchone()
    
    if result:
        return True
    
    # Verificar se é síndico cadastrado
    result = db.execute(text("""
        SELECT 1 FROM condominios c
        JOIN financeiro_usuarios fu ON fu.nome = c.sindico
        WHERE c.id = :condominio_id 
        AND fu.id = :usuario_id
        LIMIT 1
        """), {"condominio_id": condominio_id, "usuario_id": afiliado_usuario_id}).fetchone()
    
    return bool(result)


def registrar_log_afiliado(
    db: Session,
    afiliado_id: int,
    acao: str,
    descricao: str = None,
    ip_origem: str = None,
    user_agent: str = None,
    dados_extra: dict = None
):
    """Registra log de atividade do afiliado"""
    try:
        db.execute(text("""
            INSERT INTO afiliado_logs 
            (afiliado_id, acao, descricao, ip_origem, user_agent, dados_extra, data_log)
            VALUES (:afiliado_id, :acao, :descricao, :ip, :ua, :dados, NOW())
            """), {
                "afiliado_id": afiliado_id,
                "acao": acao,
                "descricao": descricao,
                "ip": ip_origem,
                "ua": user_agent,
                "dados": json.dumps(dados_extra) if dados_extra else None
            })
        db.commit()
    except Exception as e:
        logger.error(f"Erro ao registrar log: {e}")
        db.rollback()


def calcular_comissao_afiliado(
    db: Session,
    cobranca_id: int
) -> Optional[Dict]:
    """
    Calcula e registra comissão de afiliado após pagamento confirmado
    
    Regras:
    1. Só processa se cobrança está paga
    2. Evita duplicidade
    3. Verifica autoindicação
    4. Aplica tipo de comissão correto
    5. Inicia carência de 7 dias
    """
    
    # 1. Buscar cobrança
    cobranca = db.execute(text("""
        SELECT c.id_cobranca, c.valor, c.status, c.id_condominio, c.data_pagamento
        FROM cobrancas c
        WHERE c.id_cobranca = :id
        """), {"id": cobranca_id}).fetchone()
    
    if not cobranca or cobranca.status not in ['pago', 'RECEIVED', 'CONFIRMED']:
        logger.info(f"Cobrança {cobranca_id} não está paga")
        return None
    
    # 2. Verificar se já existe comissão
    ja_existe = db.execute(text("""
        SELECT id FROM afiliado_comissoes
        WHERE cobranca_id = :cid
        """), {"cid": cobranca_id}).fetchone()
    
    if ja_existe:
        logger.info(f"Comissão já existe para cobrança {cobranca_id}")
        return None
    
    # 3. Buscar afiliado do condomínio
    afiliado = db.execute(text("""
        SELECT 
            a.id AS afiliado_id,
            a.tipo_comissao,
            a.percentual,
            a.usuario_id
        FROM afiliado_condominios ac
        JOIN afiliados a ON a.id = ac.afiliado_id
        WHERE ac.condominio_id = :condominio_id
          AND a.ativo = 1
          AND ac.ativo = 1
        """), {"condominio_id": cobranca.id_condominio}).fetchone()
    
    if not afiliado:
        logger.info(f"Condomínio {cobranca.id_condominio} sem afiliado vinculado")
        return None
    
    # 4. Verificar autoindicação
    if verificar_autoindicacao(db, afiliado.usuario_id, cobranca.id_condominio):
        logger.info(f"Autoindicação detectada - afiliado {afiliado.afiliado_id}")
        
        # Registra comissão bloqueada para transparência
        db.execute(text("""
            INSERT INTO afiliado_comissoes
            (afiliado_id, condominio_id, cobranca_id, valor_comissao,
             status, tipo, motivo_bloqueio, data_criacao)
            VALUES
            (:afiliado_id, :condominio_id, :cobranca_id, 0,
             'cancelada', 'primeira', 'autoindicacao', NOW())
            """), {
                "afiliado_id": afiliado.afiliado_id,
                "condominio_id": cobranca.id_condominio,
                "cobranca_id": cobranca.id_cobranca
            })
        db.commit()
        
        registrar_log_afiliado(
            db,
            afiliado.afiliado_id,
            "comissao_bloqueada",
            "Autoindicação detectada - comissão não gerada",
            dados_extra={"condominio_id": cobranca.id_condominio, "cobranca_id": cobranca_id}
        )
        
        return {"motivo": "autoindicacao", "afiliado_id": afiliado.afiliado_id}
    
    # 5. Verificar tipo de comissão
    tipo = None
    if afiliado.tipo_comissao == 'primeiro_mes':
        # Verificar se já houve comissão antes
        ja_comissionou = db.execute(text("""
            SELECT 1 FROM afiliado_comissoes
            WHERE condominio_id = :condominio_id
              AND afiliado_id = :afiliado_id
              AND tipo = 'primeira'
              AND status != 'cancelada'
            """), {
                "condominio_id": cobranca.id_condominio,
                "afiliado_id": afiliado.afiliado_id
            }).fetchone()
        
        if ja_comissionou:
            logger.info(f"Primeira comissão já foi paga para condomínio {cobranca.id_condominio}")
            return None
        
        tipo = 'primeira'
    else:
        tipo = 'recorrente'
    
    # 6. Calcular valor da comissão
    valor_base = Decimal(str(cobranca.valor))
    percentual = Decimal(str(afiliado.percentual)) / Decimal('100')
    valor_comissao = (valor_base * percentual).quantize(Decimal('0.01'))
    
    # 7. Registrar comissão (status pendente - aguarda carência de 7 dias)
    db.execute(text("""
        INSERT INTO afiliado_comissoes
        (afiliado_id, condominio_id, cobranca_id, valor_comissao,
         status, tipo, data_criacao)
        VALUES
        (:afiliado_id, :condominio_id, :cobranca_id, :valor,
         'pendente', :tipo, NOW())
        """), {
            "afiliado_id": afiliado.afiliado_id,
            "condominio_id": cobranca.id_condominio,
            "cobranca_id": cobranca.id_cobranca,
            "valor": valor_comissao,
            "tipo": tipo
        })
    
    db.commit()
    
    registrar_log_afiliado(
        db,
        afiliado.afiliado_id,
        "comissao_gerada",
        f"Comissão de {tipo} gerada: R$ {valor_comissao}",
        dados_extra={
            "condominio_id": cobranca.id_condominio,
            "cobranca_id": cobranca_id,
            "valor": float(valor_comissao),
            "tipo": tipo
        }
    )
    
    logger.info(f"Comissão gerada: afiliado={afiliado.afiliado_id}, valor={valor_comissao}, tipo={tipo}")
    
    return {
        "afiliado_id": afiliado.afiliado_id,
        "valor": float(valor_comissao),
        "tipo": tipo,
        "status": "pendente"
    }


def liberar_comissoes_pendentes(db: Session) -> int:
    """
    Libera comissões que já passaram da carência de 7 dias
    
    Deve ser executado diariamente via cron/scheduler
    """
    try:
        # Buscar comissões pendentes que já passaram da carência
        data_limite = datetime.now() - timedelta(days=7)
        
        result = db.execute(text("""
            UPDATE afiliado_comissoes ac
            JOIN cobrancas c ON c.id_cobranca = ac.cobranca_id
            SET ac.status = 'liberada',
                ac.data_liberacao = NOW()
            WHERE ac.status = 'pendente'
              AND c.status IN ('pago', 'RECEIVED', 'CONFIRMED')
              AND c.data_pagamento <= :data_limite
            """), {"data_limite": data_limite})
        
        db.commit()
        linhas_afetadas = result.rowcount
        
        logger.info(f"Liberadas {linhas_afetadas} comissões pendentes")
        return linhas_afetadas
        
    except Exception as e:
        logger.error(f"Erro ao liberar comissões: {e}")
        db.rollback()
        return 0


def calcular_saldo_disponivel(db: Session, afiliado_id: int) -> Decimal:
    """Calcula o saldo disponível para saque do afiliado"""
    result = db.execute(text("""
        SELECT COALESCE(SUM(valor_comissao), 0) as saldo
        FROM afiliado_comissoes
        WHERE afiliado_id = :afiliado_id
          AND status = 'liberada'
        """), {"afiliado_id": afiliado_id}).fetchone()
    
    return Decimal(str(result.saldo)) if result else Decimal('0.00')


def validar_saque(db: Session, afiliado_id: int, valor: Decimal) -> tuple:
    """
    Valida se o afiliado pode solicitar saque
    
    Retorna: (sucesso: bool, mensagem: str)
    """
    # Verificar saldo disponível
    saldo = calcular_saldo_disponivel(db, afiliado_id)
    
    if valor > saldo:
        return False, f"Saldo insuficiente. Disponível: R$ {saldo}"
    
    # Verificar valor mínimo
    if valor < Decimal('100.00'):
        return False, "Valor mínimo para saque é R$ 100,00"
    
    # Verificar se há saques pendentes
    saque_pendente = db.execute(text("""
        SELECT id FROM afiliado_saques
        WHERE afiliado_id = :afiliado_id
          AND status IN ('solicitado', 'em_analise', 'aprovado')
        LIMIT 1
        """), {"afiliado_id": afiliado_id}).fetchone()
    
    if saque_pendente:
        return False, "Você já possui um saque pendente de processamento"
    
    return True, "Validação OK"


def processar_saque(
    db: Session,
    afiliado_id: int,
    valor: Decimal,
    metodo_pagamento: str,
    dados_pagamento: str
) -> Optional[int]:
    """
    Processa solicitação de saque
    
    Retorna: ID do saque criado ou None se falhou
    """
    # Validar
    valido, mensagem = validar_saque(db, afiliado_id, valor)
    if not valido:
        raise ValueError(mensagem)
    
    try:
        # Criar solicitação de saque
        result = db.execute(text("""
            INSERT INTO afiliado_saques
            (afiliado_id, valor_solicitado, status, metodo_pagamento, dados_pagamento, data_solicitacao)
            VALUES
            (:afiliado_id, :valor, 'solicitado', :metodo, :dados, NOW())
            """), {
                "afiliado_id": afiliado_id,
                "valor": valor,
                "metodo": metodo_pagamento,
                "dados": dados_pagamento
            })
        
        db.commit()
        
        saque_id = result.lastrowid
        
        registrar_log_afiliado(
            db,
            afiliado_id,
            "saque_solicitado",
            f"Solicitação de saque de R$ {valor}",
            dados_extra={"saque_id": saque_id, "valor": float(valor)}
        )
        
        logger.info(f"Saque solicitado: afiliado={afiliado_id}, valor={valor}, id={saque_id}")
        
        return saque_id
        
    except Exception as e:
        logger.error(f"Erro ao processar saque: {e}")
        db.rollback()
        raise


def aprovar_saque(
    db: Session,
    saque_id: int,
    aprovado_por: int,
    observacoes: str = None
) -> bool:
    """Aprova uma solicitação de saque"""
    try:
        # Atualizar status do saque
        db.execute(text("""
            UPDATE afiliado_saques
            SET status = 'aprovado',
                data_aprovacao = NOW(),
                aprovado_por = :aprovador,
                observacoes = :obs
            WHERE id = :saque_id
              AND status = 'solicitado'
            """), {
                "saque_id": saque_id,
                "aprovador": aprovado_por,
                "obs": observacoes
            })
        
        db.commit()
        
        logger.info(f"Saque {saque_id} aprovado por {aprovado_por}")
        return True
        
    except Exception as e:
        logger.error(f"Erro ao aprovar saque: {e}")
        db.rollback()
        return False


def confirmar_pagamento_saque(
    db: Session,
    saque_id: int,
    valor_pago: Decimal,
    comprovante: str = None
) -> bool:
    """Confirma o pagamento de um saque"""
    try:
        # Buscar saque
        saque = db.execute(text("""
            SELECT afiliado_id, valor_solicitado FROM afiliado_saques
            WHERE id = :saque_id
            """), {"saque_id": saque_id}).fetchone()
        
        if not saque:
            return False
        
        # Atualizar saque
        db.execute(text("""
            UPDATE afiliado_saques
            SET status = 'pago',
                valor_pago = :valor_pago,
                data_pagamento = NOW(),
                comprovante = :comprovante
            WHERE id = :saque_id
            """), {
                "saque_id": saque_id,
                "valor_pago": valor_pago,
                "comprovante": comprovante
            })
        
        # Marcar comissões como pagas
        db.execute(text("""
            UPDATE afiliado_comissoes
            SET status = 'paga',
                data_pagamento = NOW()
            WHERE afiliado_id = :afiliado_id
              AND status = 'liberada'
            LIMIT :quantidade
            """), {
                "afiliado_id": saque.afiliado_id,
                "quantidade": 1000  # Ajustar conforme necessário
            })
        
        db.commit()
        
        registrar_log_afiliado(
            db,
            saque.afiliado_id,
            "saque_pago",
            f"Saque pago: R$ {valor_pago}",
            dados_extra={"saque_id": saque_id, "valor": float(valor_pago)}
        )
        
        logger.info(f"Pagamento do saque {saque_id} confirmado")
        return True
        
    except Exception as e:
        logger.error(f"Erro ao confirmar pagamento: {e}")
        db.rollback()
        return False


def get_dashboard_data(db: Session, afiliado_id: int) -> Dict:
    """Retorna dados do dashboard do afiliado"""
    # Total de indicações
    total_indicacoes = db.execute(text("""
        SELECT COUNT(*) as total
        FROM afiliado_condominios
        WHERE afiliado_id = :afiliado_id
          AND ativo = 1
        """), {"afiliado_id": afiliado_id}).fetchone().total or 0
    
    # Indicações por status
    indicacoes_status = db.execute(text("""
        SELECT 
            c.assinatura_status,
            COUNT(*) as quantidade
        FROM afiliado_condominios ac
        JOIN condominios c ON c.id = ac.condominio_id
        WHERE ac.afiliado_id = :afiliado_id
          AND ac.ativo = 1
        GROUP BY c.assinatura_status
        """), {"afiliado_id": afiliado_id}).fetchall()
    
    indicacoes_ativas = sum(r.quantidade for r in indicacoes_status if r.assinatura_status == 'ativa')
    indicacoes_trial = sum(r.quantidade for r in indicacoes_status if r.assinatura_status == 'trial')
    indicacoes_canceladas = sum(r.quantidade for r in indicacoes_status if r.assinatura_status in ['cancelada', 'suspensa'])
    
    # Totais financeiros
    totais = db.execute(text("""
        SELECT 
            SUM(CASE WHEN status = 'paga' THEN valor_comissao ELSE 0 END) as total_pago,
            SUM(CASE WHEN status = 'liberada' THEN valor_comissao ELSE 0 END) as saldo_disponivel,
            SUM(CASE WHEN status = 'pendente' THEN valor_comissao ELSE 0 END) as total_pendente,
            SUM(valor_comissao) as total_ganho
        FROM afiliado_comissoes
        WHERE afiliado_id = :afiliado_id
          AND status != 'cancelada'
        """), {"afiliado_id": afiliado_id}).fetchone()
    
    # Comissões do mês atual
    comissoes_mes = db.execute(text("""
        SELECT COALESCE(SUM(valor_comissao), 0) as total
        FROM afiliado_comissoes
        WHERE afiliado_id = :afiliado_id
          AND MONTH(data_criacao) = MONTH(NOW())
          AND YEAR(data_criacao) = YEAR(NOW())
          AND status != 'cancelada'
        """), {"afiliado_id": afiliado_id}).fetchone().total or 0
    
    return {
        "total_indicacoes": total_indicacoes,
        "indicacoes_ativas": indicacoes_ativas,
        "indicacoes_trial": indicacoes_trial,
        "indicacoes_canceladas": indicacoes_canceladas,
        "total_ganho": float(totais.total_ganho or 0),
        "total_pago": float(totais.total_pago or 0),
        "total_pendente": float(totais.total_pendente or 0),
        "saldo_disponivel": float(totais.saldo_disponivel or 0),
        "comissoes_mes_atual": float(comissoes_mes)
    }
