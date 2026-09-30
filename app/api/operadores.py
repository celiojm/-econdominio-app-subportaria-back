from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
import unicodedata

from ..database import get_db
from .auth import get_current_user
from app.services.auth import get_password_hash

# MODELOS - Usando apenas as tabelas atuais
from ..models import Operador
from ..models import Condominio  # Apenas a tabela nova "condominios"

router = APIRouter(tags=["operadores"])


# -------------- helpers --------------

def is_master(user: dict) -> bool:
    """
    Determina se o usuário é master do sistema.
    Master = nome "admin" (case-insensitive) + nível 1
    """
    # Tenta várias possibilidades de campos que podem conter o nome
    username = (
        user.get("sub") or           # Campo principal do JWT
        user.get("username") or 
        user.get("Nome") or 
        user.get("nome") or 
        ""
    ).strip().lower()
    
    # Tenta várias possibilidades para o nível
    nivel = user.get("nivel") or user.get("Nivel_id") or 0
    
    is_admin_user = username == "admin"
    is_admin_level = nivel == 1
    
    # Log para debug (pode ser removido em produção)
    print(f"DEBUG is_master: username='{username}', nivel={nivel}, is_admin={is_admin_user and is_admin_level}")
    
    return is_admin_user and is_admin_level


def _safe_set(obj, field: str, value):
    """
    Define o atributo apenas se o campo existir no modelo e o valor não for None.
    """
    if hasattr(obj, field) and value is not None:
        setattr(obj, field, value)


def resolve_destino_condominio(db: Session, current_user: dict, data: Dict[str, Any]) -> (int, str):
    """
    Determina (Condomino_id, Nomedocondomino) do operador a ser criado/alterado.
    SIMPLIFICADO - usa apenas a tabela "condominios"
    """
    print(f"DEBUG resolve_destino_condominio - User: {current_user}")
    print(f"DEBUG resolve_destino_condominio - Data: {data}")
    print(f"DEBUG resolve_destino_condominio - Is master: {is_master(current_user)}")
    
    if is_master(current_user):
        # Master pode escolher qualquer condomínio
        
        # 1) ID direto do condomínio
        cond_id = data.get("Condomino_id") or data.get("condominio_id") or data.get("condominioId")
        if cond_id not in (None, "", "null", 0):
            try:
                cond_id = int(cond_id)
                condominio = db.query(Condominio).filter(Condominio.id == cond_id).first()
                if not condominio:
                    raise HTTPException(status_code=400, detail=f"Condomínio com ID {cond_id} não encontrado")
                
                print(f"DEBUG resolve_destino_condominio - Encontrado: {cond_id} - {condominio.nome}")
                return cond_id, condominio.nome
                
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="ID do condomínio inválido")

        # 2) Por nome do condomínio
        nome = data.get("condominio_nome") or data.get("condominioNome") or data.get("Nomedocondominio")
        if nome:
            condominio = db.query(Condominio).filter(Condominio.nome.ilike(f"%{nome}%")).first()
            if not condominio:
                raise HTTPException(status_code=400, detail=f"Condomínio '{nome}' não encontrado")
            
            print(f"DEBUG resolve_destino_condominio - Encontrado por nome: {condominio.id} - {condominio.nome}")
            return condominio.id, condominio.nome

    # Fallback: não-master ou master sem info -> usa o do token
    user_cond_id = current_user.get("condominio_id") or current_user.get("Condomino_id")
    
    if not user_cond_id:
        raise HTTPException(status_code=400, detail="Não foi possível determinar o condomínio de destino")
    
    # Buscar nome do condomínio
    condominio = db.query(Condominio).filter(Condominio.id == user_cond_id).first()
    user_cond_nome = condominio.nome if condominio else "Condomínio Desconhecido"
    
    print(f"DEBUG resolve_destino_condominio - Fallback para usuário: {user_cond_id}, nome: {user_cond_nome}")
    return user_cond_id, user_cond_nome


def validate_nivel_permission(current_user: dict, nivel_solicitado: int):
    """
    Valida se o usuário tem permissão para criar/editar operador com o nível solicitado.
    """
    if is_master(current_user):
        # Master pode criar qualquer nível, mas vamos limitar a níveis razoáveis
        if nivel_solicitado < 1 or nivel_solicitado > 5:
            raise HTTPException(status_code=400, detail="Nível de permissão inválido (1-5)")
        return True
    
    # Não-master: só pode criar síndico (2), zelador (3) ou operador (4)
    if nivel_solicitado not in [2, 3, 4]:
        raise HTTPException(
            status_code=403, 
            detail="Sem permissão para criar este nível de usuário. Permitido: Síndico (2), Zelador (3) ou Operador (4)"
        )
    return True


# -------------- rotas --------------

@router.get("/", response_model=List[Dict[str, Any]])
def listar_operadores(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Master: lista todos. Demais: apenas do seu condomínio.
    """
    print(f"DEBUG listar_operadores - Current user: {current_user}")
    print(f"DEBUG listar_operadores - Is master: {is_master(current_user)}")
    
    q = db.query(Operador)
    if not is_master(current_user):
        user_cond_id = current_user.get("condominio_id") or current_user.get("Condomino_id")
        q = q.filter(Operador.Condomino_id == user_cond_id)
        print(f"DEBUG listar_operadores - Filtrando por condomínio: {user_cond_id}")

    ops = q.order_by(Operador.Nome.asc()).all()
    print(f"DEBUG listar_operadores - Encontrados {len(ops)} operadores")
    
    retorno: List[Dict[str, Any]] = []
    for op in ops:
        retorno.append(
            {
                "id": op.id,
                "Nome": op.Nome,
                "Nivel_id": op.Nivel_id,
                "Condomino_id": op.Condomino_id,
                "Nomedocondomino": getattr(op, "Nomedocondomino", ""),
                "telefone": getattr(op, "telefone", None),
                "email": getattr(op, "email", None),
            }
        )
    return retorno


@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_operador(
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Cria operador. Simplificado para usar apenas tabela "condominios".
    """
    # DEBUG do payload
    print("=" * 50)
    print("DEBUG criar_operador payload:", data)
    print("DEBUG criar_operador current_user:", current_user)
    print("=" * 50)

    nome = (data.get("Nome") or data.get("nome") or "").strip()
    senha = data.get("Senha") or data.get("senha") or ""
    nivel = int(data.get("Nivel_id") or data.get("nivel") or 2)

    # Validações básicas
    if not nome:
        raise HTTPException(status_code=422, detail="Nome é obrigatório")
    if not senha:
        raise HTTPException(status_code=422, detail="Senha é obrigatória")
    if len(nome) < 2:
        raise HTTPException(status_code=422, detail="Nome deve ter pelo menos 2 caracteres")
    if len(senha) < 4:
        raise HTTPException(status_code=422, detail="Senha deve ter pelo menos 4 caracteres")

    # Validar permissão de nível
    validate_nivel_permission(current_user, nivel)

    # Resolver condomínio de destino
    try:
        cond_id, cond_nome = resolve_destino_condominio(db, current_user, data)
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR resolve_destino_condominio: {str(e)}")
        raise HTTPException(status_code=400, detail="Erro ao determinar condomínio de destino")
    
    if not cond_id:
        raise HTTPException(status_code=400, detail="Condomínio de destino é obrigatório")

    # Verificar se já existe operador com mesmo nome no mesmo condomínio
    operador_existente = (
        db.query(Operador)
        .filter(Operador.Nome.ilike(nome))
        .filter(Operador.Condomino_id == cond_id)
        .first()
    )
    if operador_existente:
        raise HTTPException(
            status_code=409, 
            detail=f"Já existe um operador com nome '{nome}' neste condomínio"
        )

    try:
        novo = Operador(
            Nome=nome,
            Senha=get_password_hash(senha),
            Nivel_id=nivel,
            Condomino_id=cond_id,
            Nomedocondomino=cond_nome or "",
        )

        # Campos opcionais
        _safe_set(novo, "telefone", data.get("telefone"))
        _safe_set(novo, "email", data.get("email"))

        db.add(novo)
        db.commit()
        db.refresh(novo)

        print(f"DEBUG criar_operador - Criado com sucesso: ID {novo.id}")

        return {
            "id": novo.id,
            "Nome": novo.Nome,
            "Nivel_id": novo.Nivel_id,
            "Condomino_id": novo.Condomino_id,
            "Nomedocondomino": getattr(novo, "Nomedocondomino", ""),
            "telefone": getattr(novo, "telefone", None),
            "email": getattr(novo, "email", None),
        }
    
    except Exception as e:
        db.rollback()
        print(f"DEBUG criar_operador - Erro: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro interno ao criar operador: {str(e)}")


@router.put("/{operador_id}")
def atualizar_operador(
    operador_id: int,
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Atualiza operador.
    """
    print("=" * 50)
    print(f"DEBUG atualizar_operador - ID: {operador_id}")
    print(f"DEBUG atualizar_operador - Payload: {data}")
    print(f"DEBUG atualizar_operador - Current user: {current_user}")
    print("=" * 50)

    op: Optional[Operador] = db.query(Operador).filter(Operador.id == operador_id).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operador não encontrado")

    # Verificar permissão
    user_cond_id = current_user.get("condominio_id") or current_user.get("Condomino_id")
    if not is_master(current_user) and op.Condomino_id != user_cond_id:
        raise HTTPException(status_code=403, detail="Sem permissão para editar este operador")

    try:
        # Mover de condomínio (apenas master)
        if is_master(current_user) and any(
            k in data for k in ["Condomino_id", "condominio_id", "condominioId"]
        ):
            try:
                cond_id, cond_nome = resolve_destino_condominio(db, current_user, data)
                if cond_id and cond_id != op.Condomino_id:
                    op.Condomino_id = cond_id
                    _safe_set(op, "Nomedocondomino", cond_nome)
                    print(f"DEBUG atualizar_operador - Movendo para condomínio: {cond_id} - {cond_nome}")
            except Exception as e:
                print(f"ERROR ao mover condomínio: {str(e)}")

        # Atualizar nome
        novo_nome = (data.get("Nome") or data.get("nome") or "").strip()
        if novo_nome and novo_nome != op.Nome:
            if len(novo_nome) < 2:
                raise HTTPException(status_code=422, detail="Nome deve ter pelo menos 2 caracteres")
            
            # Verificar duplicata
            nome_existente = (
                db.query(Operador)
                .filter(Operador.Nome.ilike(novo_nome))
                .filter(Operador.Condomino_id == op.Condomino_id)
                .filter(Operador.id != operador_id)
                .first()
            )
            if nome_existente:
                raise HTTPException(
                    status_code=409, 
                    detail=f"Já existe outro operador com nome '{novo_nome}' neste condomínio"
                )
            
            op.Nome = novo_nome

        # Atualizar senha (se fornecida)
        nova_senha = data.get("Senha") or data.get("senha")
        if nova_senha and nova_senha.strip():
            if len(nova_senha.strip()) < 4:
                raise HTTPException(status_code=422, detail="Senha deve ter pelo menos 4 caracteres")
            op.Senha = get_password_hash(nova_senha.strip())
            print("DEBUG atualizar_operador - Senha atualizada")

        # Atualizar nível
        novo_nivel = data.get("Nivel_id") or data.get("nivel")
        if novo_nivel is not None:
            novo_nivel = int(novo_nivel)
            validate_nivel_permission(current_user, novo_nivel)
            op.Nivel_id = novo_nivel

        # Campos opcionais
        _safe_set(op, "telefone", data.get("telefone"))
        _safe_set(op, "email", data.get("email"))

        db.commit()
        db.refresh(op)
        
        print(f"DEBUG atualizar_operador - Atualizado com sucesso: ID {op.id}")
        
        return {
            "id": op.id,
            "Nome": op.Nome,
            "Nivel_id": op.Nivel_id,
            "Condomino_id": op.Condomino_id,
            "Nomedocondomino": getattr(op, "Nomedocondomino", ""),
            "telefone": getattr(op, "telefone", None),
            "email": getattr(op, "email", None),
        }
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"DEBUG atualizar_operador - Erro: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro interno ao atualizar operador: {str(e)}")


@router.delete("/{operador_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_operador(
    operador_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Exclui operador.
    """
    print(f"DEBUG excluir_operador - ID: {operador_id}")
    print(f"DEBUG excluir_operador - Current user: {current_user}")
    
    op: Optional[Operador] = db.query(Operador).filter(Operador.id == operador_id).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operador não encontrado")

    # Não permitir que o usuário exclua a si mesmo
    current_user_name = (
        current_user.get("sub") or
        current_user.get("username") or 
        current_user.get("Nome") or 
        current_user.get("nome") or 
        ""
    ).strip().lower()
    
    if op.Nome.lower() == current_user_name:
        raise HTTPException(status_code=400, detail="Você não pode excluir sua própria conta")

    # Verificar permissão por condomínio
    user_cond_id = current_user.get("condominio_id") or current_user.get("Condomino_id")
    if not is_master(current_user) and op.Condomino_id != user_cond_id:
        raise HTTPException(status_code=403, detail="Sem permissão para excluir este operador")

    try:
        db.delete(op)
        db.commit()
        print(f"DEBUG excluir_operador - Excluído com sucesso: ID {operador_id}")
        return
    except Exception as e:
        db.rollback()
        print(f"DEBUG excluir_operador - Erro: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro interno ao excluir operador: {str(e)}")


@router.get("/permissoes", response_model=List[Dict[str, Any]])
def listar_permissoes(
    current_user: dict = Depends(get_current_user),
):
    """
    Lista todas as permissões disponíveis, filtradas por nível de acesso do usuário.
    """
    try:
        # Dados estáticos baseados na estrutura da tabela permissoes
        permissoes_estaticas = [
            {"nivel": 1, "permissao": "Autorizado Geral"},
            {"nivel": 2, "permissao": "Síndico"}, 
            {"nivel": 3, "permissao": "Zelador"},
            {"nivel": 4, "permissao": "Operador"},
            {"nivel": 5, "permissao": "Morador"}
        ]
        
        # Filtrar permissões baseado no usuário
        if is_master(current_user):
            # Master pode ver/criar até operador (nível 4)
            permissoes_filtradas = [p for p in permissoes_estaticas if p["nivel"] <= 4]
        else:
            # Não-master só pode criar síndico, zelador e operador
            permissoes_filtradas = [p for p in permissoes_estaticas if p["nivel"] in [2, 3, 4]]
        
        return permissoes_filtradas
        
    except Exception as e:
        print(f"DEBUG listar_permissoes - Erro: {str(e)}")
        # Fallback em caso de erro
        return [
            {"nivel": 2, "permissao": "Síndico"},
            {"nivel": 3, "permissao": "Zelador"},
            {"nivel": 4, "permissao": "Operador"}
        ]


@router.get("/me", response_model=Dict[str, Any])
def obter_operador_atual(
    current_user: dict = Depends(get_current_user),
):
    """
    Retorna informações do operador logado atualmente.
    """
    return {
        "username": current_user.get("sub") or current_user.get("username") or current_user.get("Nome"),
        "nivel": current_user.get("nivel") or current_user.get("Nivel_id"),
        "condominio_id": current_user.get("condominio_id") or current_user.get("Condomino_id"),
        "condominio_nome": current_user.get("condominio_nome") or current_user.get("Nomedocondomino"),
        "is_master": is_master(current_user)
    }
