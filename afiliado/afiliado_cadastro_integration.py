from sqlalchemy import text
# ================================================================================
# ARQUIVO: afiliado_cadastro_integration.py
# PASTA:   ~/backend/afiliado/
# CAMINHO: visionlpr@vps60688:~/backend/afiliado/afiliado_cadastro_integration.py
# ================================================================================
# DESCRIÇÃO: Integração com o cadastro de clientes para vincular afiliados
# ================================================================================

from sqlalchemy.orm import Session
import logging
from typing import Optional, Dict
from datetime import datetime

from .afiliado_service import registrar_log_afiliado, verificar_autoindicacao

logger = logging.getLogger(__name__)


def vincular_afiliado_ao_cadastro(
    db: Session,
    condominio_id: int,
    codigo_afiliado: str,
    ip_origem: str = None,
    user_agent: str = None
) -> Optional[Dict]:
    """
    Vincula um afiliado a um condomínio recém-cadastrado
    
    Esta função deve ser chamada durante o processo de cadastro de cliente,
    após o condomínio ser criado no banco de dados.
    
    Args:
        db: Sessão do banco de dados
        condominio_id: ID do condomínio recém-cadastrado
        codigo_afiliado: Código do afiliado (vem do parâmetro ?afiliado=XXX)
        ip_origem: IP do cliente que fez o cadastro
        user_agent: User-Agent do browser
        
    Returns:
        Dict com informações do vínculo ou None se falhou
    """
    try:
        # 1. Validar código do afiliado
        afiliado = db.execute(text("""
            SELECT id, usuario_id, ativo
            FROM afiliados
            WHERE codigo_afiliado = :codigo
            """), {"codigo": codigo_afiliado}).fetchone()
        
        if not afiliado:
            logger.warning(f"Código de afiliado inválido: {codigo_afiliado}")
            return None
        
        if not afiliado.ativo:
            logger.warning(f"Afiliado {afiliado.id} está inativo")
            return None
        
        # 2. Verificar se condomínio já está vinculado
        vinculo_existente = db.execute(text("""
            SELECT id FROM afiliado_condominios
            WHERE condominio_id = :condominio_id
            """), {"condominio_id": condominio_id}).fetchone()
        
        if vinculo_existente:
            logger.info(f"Condomínio {condominio_id} já possui afiliado vinculado")
            return None
        
        # 3. Verificar autoindicação
        eh_autoindicacao = verificar_autoindicacao(db, afiliado.usuario_id, condominio_id)
        
        # 4. Criar vínculo (mesmo se for autoindicação, para fins de registro)
        result = db.execute(text("""
            INSERT INTO afiliado_condominios
            (afiliado_id, condominio_id, data_vinculo, ip_origem, user_agent, ativo)
            VALUES
            (:afiliado_id, :condominio_id, NOW(), :ip, :ua, 1)
            """), {
                "afiliado_id": afiliado.id,
                "condominio_id": condominio_id,
                "ip": ip_origem,
                "ua": user_agent
            })
        
        db.commit()
        
        vinculo_id = result.lastrowid
        
        # 5. Registrar log
        descricao = "Condomínio vinculado ao afiliado"
        if eh_autoindicacao:
            descricao += " (AUTOINDICAÇÃO - sem comissão)"
        
        registrar_log_afiliado(
            db,
            afiliado.id,
            "vinculo_condominio",
            descricao,
            ip_origem,
            user_agent,
            {
                "condominio_id": condominio_id,
                "vinculo_id": vinculo_id,
                "autoindicacao": eh_autoindicacao
            }
        )
        
        logger.info(f"Condomínio {condominio_id} vinculado ao afiliado {afiliado.id}")
        
        return {
            "success": True,
            "afiliado_id": afiliado.id,
            "condominio_id": condominio_id,
            "vinculo_id": vinculo_id,
            "autoindicacao": eh_autoindicacao
        }
        
    except Exception as e:
        logger.error(f"Erro ao vincular afiliado: {e}")
        db.rollback()
        return None


def extrair_codigo_afiliado_da_url(url: str) -> Optional[str]:
    """
    Extrai o código do afiliado de uma URL ou query string
    
    Exemplo:
        /cadastro_cliente?afiliado=ABC123 -> ABC123
        ?afiliado=XYZ789&outro=param -> XYZ789
    """
    try:
        from urllib.parse import parse_qs, urlparse
        
        # Parse da URL
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        # Buscar parâmetro 'afiliado'
        codigo = params.get('afiliado', [None])[0]
        
        return codigo if codigo else None
        
    except Exception as e:
        logger.error(f"Erro ao extrair código de afiliado: {e}")
        return None


# ============================================================================
# INSTRUÇÕES DE INTEGRAÇÃO NO CADASTRO DE CLIENTES
# ============================================================================

"""
No arquivo que processa o cadastro de clientes (ex: cliente/router.py),
adicionar o seguinte após criar o condomínio:

```python
from afiliado.afiliado_cadastro_integration import vincular_afiliado_ao_cadastro

@router.post("/cadastro")
async def cadastrar_cliente(
    data: CadastroClienteRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        # ... código de cadastro existente ...
        
        # Criar condomínio
        condominio_id = criar_condominio(db, data)
        
        # ADICIONAR: Vincular afiliado se houver código
        codigo_afiliado = request.query_params.get('afiliado')
        if codigo_afiliado:
            ip_origem = request.client.host if request.client else None
            user_agent = request.headers.get('user-agent')
            
            vincular_afiliado_ao_cadastro(
                db,
                condominio_id,
                codigo_afiliado,
                ip_origem,
                user_agent
            )
        
        # ... resto do código ...
        
        return {"success": True, "condominio_id": condominio_id}
        
    except Exception as e:
        logger.error(f"Erro no cadastro: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

IMPORTANTE:
1. O código deve ser executado APÓS a criação do condomínio
2. O vínculo não deve impedir o cadastro mesmo se falhar
3. Use try/except para não quebrar o fluxo principal
"""
