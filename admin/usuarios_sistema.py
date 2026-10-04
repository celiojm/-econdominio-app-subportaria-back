# ============================================================================
# ARQUIVO: usuarios_sistema.py
# PASTA: /home/visionlpr/app_subportaria_back/admin/
# DESCRIÇÃO: "Usuários do sistema" do admin — equipe com role admin_sistema em mobile_operadores.
#            Só o MASTER acessa: lista, cadastra (master ou colaborador), altera nome/telefone/nível,
#            redefine senha e ativa/desativa. Colaborador acessa tudo do master, menos Leads e esta tela.
#            O master não pode rebaixar nem desativar a si mesmo. Não apaga (o nome fica no Relatório de Log).
#            Rotas: GET/POST /painel/usuarios-sistema, PUT /painel/usuarios-sistema/{id}
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-04 data alteração: 2026-10-04
# ============================================================================
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import get_current_user
from .auth_service import auth_service
from .database import get_db_connection
from app.services.nivel_sistema import eh_master_sistema

router = APIRouter(tags=["Usuários do sistema"])
NIVEIS = ("master", "colaborador")


class NovoUsuario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str = Field(..., min_length=2, max_length=150)
    email: str = Field(..., min_length=5, max_length=255)
    telefone: Optional[str] = Field(None, max_length=20)
    senha: str = Field(..., min_length=8, max_length=100)
    nivel: str = "colaborador"


class AlteraUsuario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: Optional[str] = Field(None, min_length=2, max_length=150)
    telefone: Optional[str] = Field(None, max_length=20)
    nivel: Optional[str] = None
    ativo: Optional[bool] = None
    nova_senha: Optional[str] = Field(None, min_length=8, max_length=100)


def _master(current_user: dict = Depends(get_current_user)) -> dict:
    if not eh_master_sistema(current_user):
        raise HTTPException(status_code=403, detail="Usuários do sistema: acesso só do master")
    return current_user


def _nivel_ok(n: Optional[str]) -> str:
    if n not in NIVEIS:
        raise HTTPException(status_code=400, detail="Nível inválido (master ou colaborador)")
    return n


def _fone(t: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", t or "")
    return d or None


@router.get("/usuarios-sistema")
async def listar(current_user: dict = Depends(_master)):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, nome, email, telefone, COALESCE(nivel_sistema, 'master') AS nivel, ativo,
                       ultimo_login, criado_em
                  FROM mobile_operadores WHERE role = 'admin_sistema'
                 ORDER BY ativo DESC, nome""")
            itens = c.fetchall()
        for i in itens:
            i["ativo"] = bool(i["ativo"])
            for k in ("ultimo_login", "criado_em"):
                i[k] = i[k].isoformat() if i.get(k) else None
            i["voce"] = i["id"] == current_user.get("id")
        return {"usuarios": itens, "total": len(itens)}
    finally:
        conn.close()


@router.post("/usuarios-sistema")
async def cadastrar(dados: NovoUsuario, current_user: dict = Depends(_master)):
    email = dados.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="E-mail inválido")
    nivel = _nivel_ok(dados.nivel)
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id, nome, role FROM mobile_operadores WHERE LOWER(email) = %s", (email,))
            ja = c.fetchone()
            if ja:
                raise HTTPException(status_code=409, detail=f"E-mail já usado por {ja['nome']} ({ja['role']}). Use outro e-mail.")
            c.execute("""
                INSERT INTO mobile_operadores
                    (email, nome, telefone, senha_hash, condominio_id, role, nivel_sistema, nivel_id, ativo, criado_por)
                VALUES (%s, %s, %s, %s, %s, 'admin_sistema', %s, 1, 1, %s)""",
                (email, dados.nome.strip(), _fone(dados.telefone), auth_service.hash_senha(dados.senha),
                 current_user.get("condominio_id") or 1, nivel, current_user.get("id")))
            novo = c.lastrowid
        conn.commit()
        return {"success": True, "id": novo, "nivel": nivel}
    finally:
        conn.close()


@router.put("/usuarios-sistema/{user_id}")
async def alterar(user_id: int, dados: AlteraUsuario, current_user: dict = Depends(_master)):
    eu = user_id == current_user.get("id")
    if eu and ((dados.nivel is not None and dados.nivel != "master") or dados.ativo is False):
        raise HTTPException(status_code=400, detail="Você não pode rebaixar nem desativar o seu próprio usuário")
    sets, prm = [], []
    if dados.nome is not None:
        sets.append("nome = %s"); prm.append(dados.nome.strip())
    if dados.telefone is not None:
        sets.append("telefone = %s"); prm.append(_fone(dados.telefone))
    if dados.nivel is not None:
        sets.append("nivel_sistema = %s"); prm.append(_nivel_ok(dados.nivel))
    if dados.ativo is not None:
        sets.append("ativo = %s"); prm.append(1 if dados.ativo else 0)
    if dados.nova_senha:
        sets.append("senha_hash = %s"); prm.append(auth_service.hash_senha(dados.nova_senha))
        sets.append("login_falhos = 0"); sets.append("bloqueado_ate = NULL")
    if not sets:
        return {"success": True}
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id FROM mobile_operadores WHERE id = %s AND role = 'admin_sistema'", (user_id,))
            if not c.fetchone():
                raise HTTPException(status_code=404, detail="Usuário do sistema não encontrado")
            c.execute(f"UPDATE mobile_operadores SET {', '.join(sets)} WHERE id = %s", (*prm, user_id))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()
