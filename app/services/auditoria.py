# ============================================================================
# ARQUIVO: auditoria.py
# PASTA: app/services/
# DESCRIÇÃO: Log de auditoria da plataforma. Middleware ASGI que, depois de uma
#            requisição de escrita BEM-SUCEDIDA (2xx) numa das rotas mapeadas em
#            REGRAS, grava um evento em mobile_audit_logs com quem fez (nome/papel),
#            de onde (admin/app/financeiro, IP), o registro afetado (nome/apto) e os
#            campos enviados. Senhas nunca são gravadas (só "senha alterada").
#            Falha ao registrar NUNCA afeta a requisição.
#            Recebimento/entrega de encomenda ficam de fora (volume; têm registro próprio).
#            2026-10-04: TODA escrita 2xx da EQUIPE (admin_sistema master/colaborador e usuários do
#            financeiro) também é registrada ("Ação da equipe": método + caminho + campos), mesmo fora de
#            REGRAS — ex.: emitir boleto, NF, validade. Papel mostra master/colaborador.
# VERSÃO: 1.2.1 - usuário do financeiro: id nos detalhes (o FK de usuario_id recusava e o evento se perdia) (2026-10-05)
#         1.2.0 - registro de toda ação da equipe; papel master/colaborador (2026-10-04)
#         1.1.0 - eventos de unidades (2026-09-30)
#         1.0.0 - criação (2026-09-30)
# data criação: 2026-09-30 data alteração: 2026-09-30
# ============================================================================
import json
import logging
import os
import re
from typing import Optional

from jose import jwt, JWTError
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# (método, regex do caminho, ação, entidade, tabela p/ buscar nome — None = sem registro único)
REGRAS = [
    ("POST",   r"^/api/moradores/?$",                            "morador_criado",       "morador",    "moradores"),
    ("PUT",    r"^/api/moradores/(\d+)$",                        "morador_alterado",     "morador",    "moradores"),
    ("DELETE", r"^/api/moradores/(\d+)$",                        "morador_removido",     "morador",    "moradores"),
    ("PATCH",  r"^/api/moradores/(\d+)/reativar$",               "morador_reativado",    "morador",    "moradores"),
    ("POST",   r"^/api/configuracoes/.*importar-moradores$",     "moradores_importados", "morador",    None),
    ("POST",   r"^/painel/moradores/confirmar-importacao$",      "moradores_importados", "morador",    None),
    ("POST",   r"^/api/operadores/?$",                           "operador_criado",      "operador",   "operadores"),
    ("PUT",    r"^/api/operadores/(\d+)$",                       "operador_alterado",    "operador",   "operadores"),
    ("DELETE", r"^/api/operadores/(\d+)$",                       "operador_removido",    "operador",   "operadores"),
    ("POST",   r"^/painel/operadores/?$",                        "operador_criado",      "operador",   "mobile_operadores"),
    ("PUT",    r"^/painel/operadores/(\d+)$",                    "operador_alterado",    "operador",   "mobile_operadores"),
    ("DELETE", r"^/painel/operadores/(\d+)$",                    "operador_removido",    "operador",   "mobile_operadores"),
    ("POST",   r"^/painel/operadores/(\d+)/desbloquear$",        "operador_desbloqueado","operador",   "mobile_operadores"),
    ("POST",   r"^(/api)?/mobile/auth/operadores/?$",            "operador_criado",      "operador",   "mobile_operadores"),
    ("PUT",    r"^(/api)?/mobile/auth/operadores/(\d+)$",        "operador_alterado",    "operador",   "mobile_operadores"),
    ("DELETE", r"^(/api)?/mobile/auth/operadores/(\d+)$",        "operador_removido",    "operador",   "mobile_operadores"),
    ("PUT",    r"^/api/encomendas/(\d+)$",                       "encomenda_editada",    "encomenda",  "encomendas"),
    ("PUT",    r"^/api/encomendas/(\d+)/cancelar$",              "encomenda_cancelada",  "encomenda",  "encomendas"),
    ("DELETE", r"^/api/encomendas/(\d+)$",                       "encomenda_excluida",   "encomenda",  "encomendas"),
    ("POST",   r"^/api/condominios/?$",                          "condominio_criado",    "condominio", "condominios"),
    ("PUT",    r"^/api/condominios/(\d+)$",                      "condominio_alterado",  "condominio", "condominios"),
    ("DELETE", r"^/api/condominios/(\d+)$",                      "condominio_removido",  "condominio", "condominios"),
    ("PUT",    r"^/painel/condominios/(\d+)$",                   "condominio_alterado",  "condominio", "condominios"),
    ("POST",   r"^/painel/condominios/sindico-novo$",            "sindico_cadastrado",   "operador",   None),
    ("POST",   r"^/api/unidades/?$",                             "unidade_criada",       "unidade",    "unidades"),
    ("PUT",    r"^/api/unidades/(\d+)$",                         "unidade_alterada",     "unidade",    "unidades"),
    ("POST",   r"^/api/unidades/(\d+)/mesclar$",                 "unidade_mesclada",     "unidade",    "unidades"),
    ("DELETE", r"^/api/unidades/(\d+)$",                         "unidade_excluida",     "unidade",    "unidades"),
]
_REGRAS = [(m, re.compile(rx), a, e, t) for m, rx, a, e, t in REGRAS]

ROTULOS = {
    "morador_criado": "Morador cadastrado", "morador_alterado": "Morador alterado",
    "morador_removido": "Morador removido", "morador_reativado": "Morador reativado",
    "moradores_importados": "Moradores importados", "operador_criado": "Operador cadastrado",
    "operador_alterado": "Operador alterado", "operador_removido": "Operador removido",
    "operador_desbloqueado": "Operador desbloqueado", "encomenda_editada": "Encomenda editada",
    "encomenda_cancelada": "Encomenda cancelada", "encomenda_excluida": "Encomenda excluída",
    "condominio_criado": "Condomínio cadastrado", "condominio_alterado": "Condomínio alterado",
    "condominio_removido": "Condomínio removido", "sindico_cadastrado": "Síndico cadastrado",
    "unidade_criada": "Unidade cadastrada", "unidade_alterada": "Unidade alterada",
    "unidade_mesclada": "Unidades mescladas", "unidade_excluida": "Unidade excluída",
    "acao_equipe": "Ação da equipe",
}
# 2026-10-04: escritas da equipe que NÃO são registradas (login/sessão, robôs, chamadas automáticas)
# 2026-10-04: nome amigável das ações da equipe (primeira que casar; senão método + caminho)
_ACOES_EQUIPE = [(re.compile(rx, re.I), txt) for rx, txt in [
    (r"contratos-licenca", "Gerou contrato de licença"),  # 2026-10-05
    (r"gerar-cobranca|/cobrancas/?$|gerar-boleto", "Gerou cobrança/boleto"),
    (r"reenviar", "Reenviou cobrança/nota"),
    (r"gerar-nf|emitir-nf|autorizar-nf", "Emitiu/autorizou nota fiscal"),
    (r"cancelar-nf", "Cancelou nota fiscal"),
    (r"enviar-nf", "Enviou nota fiscal"),
    (r"ajustar-validade|validade", "Ajustou validade"),
    (r"/cobrancas/\d+$", "Alterou/cancelou cobrança"),
    (r"contatos-registro|/contatos", "Registrou contato"),
    (r"agend", "Agendou/atualizou contato"),
    (r"leads", "Ação em lead"),
    (r"whatsapp|mensagens", "Enviou mensagem"),
    (r"usuarios-sistema", "Usuário do sistema"),
    (r"usuarios", "Usuário do financeiro"),
    (r"contas-pagar", "Contas a pagar"),
    (r"afiliados", "Afiliados"),
    (r"condominios", "Alterou condomínio"),
]]
_EQUIPE_IGNORAR = re.compile(r"/(auth|login|logout|refresh|token|webhook)(/|$)|^/api/leads/whatsapp|/check-|/validate", re.I)
PAPEIS = {"admin_sistema": "admin do sistema", "admin_condominio": "admin", "sindico": "síndico",
          "operador": "operador", "porteiro": "porteiro"}

# tabela -> (coluna nome, coluna condomínio, colunas extras p/ descrição)
TABELAS = {
    "moradores":         ("nome", "condominio_id", ("apartamento", "bloco")),
    "encomendas":        ("nome_destinatario", "condominio_id", ("apartamento", "bloco")),
    "operadores":        ("Nome", "condominio_id", ("email",)),
    "mobile_operadores": ("nome", "condominio_id", ("email", "role")),
    "condominios":       ("nome", "id", ()),
    "unidades":          ("apartamento", "condominio_id", ("bloco",)),
}
_SENSIVEIS = re.compile(r"senha|password|token|secret|hash", re.I)


def _regra(metodo: str, caminho: str):
    for m, rx, acao, entidade, tabela in _REGRAS:
        if m == metodo:
            achou = rx.match(caminho)
            if achou:
                ids = [g for g in achou.groups() if g and g.isdigit()]
                return acao, entidade, tabela, (int(ids[-1]) if ids else None)
    return None


def _usuario_do_token(auth: str) -> dict:
    """Quem fez: tenta a chave do admin/app e depois a do financeiro. Nunca levanta erro."""
    if not auth.lower().startswith("bearer "):
        return {}
    tk = auth[7:].strip()
    for chave in (settings.SECRET_KEY, os.getenv("JWT_SECRET_KEY")):
        if not chave:
            continue
        try:
            p = jwt.decode(tk, chave, algorithms=["HS256"])
            return {"sub": p.get("sub"), "user_id": p.get("user_id"), "nome": p.get("nome"),
                    "role": p.get("role"), "condominio_id": p.get("condominio_id"),
                    "tipo": p.get("tipo"), "nivel_sistema": p.get("nivel_sistema"),
                    "painel": "financeiro" if chave != settings.SECRET_KEY else None}
        except JWTError:
            continue
    return {}


def _registro(db, tabela: Optional[str], rid: Optional[int]) -> Optional[dict]:
    if not tabela or not rid or tabela not in TABELAS:
        return None
    col_nome, col_cond, extras = TABELAS[tabela]
    cols = ", ".join([col_nome, col_cond, *extras])
    try:
        r = db.execute(text(f"SELECT {cols} FROM {tabela} WHERE id = :i"), {"i": rid}).fetchone()
    except Exception:
        return None
    if not r:
        return None
    d = dict(zip([col_nome, col_cond, *extras], r))
    return {"nome": d.get(col_nome), "condominio_id": d.get(col_cond),
            "extras": {k: d[k] for k in extras if d.get(k) not in (None, "")}}


def _descricao(rotulo: str, reg: Optional[dict], corpo: dict) -> str:
    nome = (reg or {}).get("nome") or corpo.get("nome") or corpo.get("Nome") \
        or corpo.get("nome_destinatario") or corpo.get("email") or ""
    ex = (reg or {}).get("extras") or {}
    if not ex and corpo.get("apartamento"):
        ex = {"apartamento": corpo.get("apartamento"), "bloco": corpo.get("bloco")}
    partes = []
    if ex.get("apartamento"):
        partes.append(f"apto {ex['apartamento']}" + (f" bl {ex['bloco']}" if ex.get("bloco") else ""))
    if ex.get("email") and ex.get("email") != nome:
        partes.append(str(ex["email"]))
    return f"{rotulo}: {nome}" + (f" ({', '.join(partes)})" if partes else "") if nome else rotulo


def _gravar(metodo, caminho, regra, antes, corpo, resposta, headers):
    acao, entidade, tabela, rid = regra
    db = SessionLocal()
    try:
        if not rid and isinstance(resposta, dict):   # POST: id vem na resposta
            cand = resposta.get("id")
            for chave in ("data", entidade, "morador", "operador", "usuario"):
                if not cand and isinstance(resposta.get(chave), dict):
                    cand = resposta[chave].get("id")
            rid = int(cand) if str(cand or "").isdigit() else None
        if not rid and metodo == "POST" and tabela in TABELAS and isinstance(corpo, dict):
            # resposta sem id (ex.: POST /api/moradores): último registro com o nome enviado
            col_nome, col_cond, _ = TABELAS[tabela]
            nome_env = corpo.get("nome") or corpo.get("Nome")
            if nome_env:
                sql = f"SELECT id FROM {tabela} WHERE {col_nome} = :n"
                prm = {"n": nome_env}
                cond_env = corpo.get("condominio_id") or _usuario_do_token(headers.get("authorization", "")).get("condominio_id")
                if cond_env and col_cond != "id":
                    sql += f" AND {col_cond} = :c"
                    prm["c"] = cond_env
                rid = db.execute(text(sql + " ORDER BY id DESC LIMIT 1"), prm).scalar()
        depois = _registro(db, tabela, rid) if metodo != "DELETE" else None
        reg = (depois or antes) if metodo in ("PUT", "PATCH") else (antes or depois)
        descr_antes = None
        if metodo in ("PUT", "PATCH") and antes and depois and \
                (antes.get("nome"), antes.get("extras")) != (depois.get("nome"), depois.get("extras")):
            descr_antes = _descricao("", antes, {}).lstrip(": ")

        u = _usuario_do_token(headers.get("authorization", ""))
        quem = u.get("nome") or u.get("sub") or "desconhecido"
        if str(quem).isdigit():
            quem = db.execute(text("SELECT nome FROM mobile_operadores WHERE id = :i"),
                              {"i": int(quem)}).scalar() or f"operador #{quem}"
        uid = u.get("user_id") or (int(u["sub"]) if str(u.get("sub") or "").isdigit() else None)
        fin_uid = None
        if u.get("painel") == "financeiro":  # 2026-10-05: id do financeiro NÃO é mobile_operadores.id (FK) — vai nos detalhes
            fin_uid, uid = uid, None
        role = (u.get("role") or "").lower()
        papel = PAPEIS.get(role) or role or None
        if role == "admin_sistema":  # 2026-10-04: master/colaborador conferido no banco
            try:
                from app.services.nivel_sistema import nivel_do_id
                papel = "colaborador do sistema" if nivel_do_id(uid) == "colaborador" else "master"
            except Exception:
                pass
        elif u.get("painel") == "financeiro":
            papel = "master (financeiro)" if (u.get("tipo") or "") in ("admin", "master") else "colaborador (financeiro)"

        host = (headers.get("origin") or headers.get("referer") or headers.get("host") or "").lower()
        origem = u.get("painel") or ("app" if ("portaria" in host or "mobile" in host) else
                                     "financeiro" if "financeiro" in host else "admin")
        ip = (headers.get("cf-connecting-ip") or (headers.get("x-forwarded-for") or "").split(",")[0].strip()
              or headers.get("x-real-ip") or None)

        campos = sorted(k for k in corpo if not _SENSIVEIS.search(k)) if isinstance(corpo, dict) else []
        detalhes = {
            "descricao": _descricao(ROTULOS.get(acao, acao), reg, corpo if isinstance(corpo, dict) else {}),
            "usuario_nome": quem, "papel": papel, "origem": origem,
            "campos": campos if metodo in ("PUT", "PATCH") else None,
            "senha_alterada": True if isinstance(corpo, dict) and any(_SENSIVEIS.search(k) and corpo.get(k) for k in corpo) else None,
            "caminho": f"{metodo} {caminho}",
            "antes": descr_antes,
            "financeiro_usuario_id": fin_uid,
        }
        if acao == "acao_equipe":  # 2026-10-04
            txt = next((t for rx, t in _ACOES_EQUIPE if rx.search(caminho)), None)
            detalhes["descricao"] = f"{txt or 'Ação da equipe'} ({metodo} {caminho})"
        if acao == "moradores_importados" and isinstance(resposta, dict):
            detalhes["resultado"] = {k: v for k, v in resposta.items() if isinstance(v, (int, str)) and len(str(v)) < 80}
        detalhes = {k: v for k, v in detalhes.items() if v not in (None, [], {})}
        cond = (reg or {}).get("condominio_id") if entidade != "condominio" else rid
        cond = cond or u.get("condominio_id")

        db.execute(text("""
            INSERT INTO mobile_audit_logs (usuario_id, condominio_id, acao, entidade, entidade_id, detalhes, ip, user_agent, criado_em)
            VALUES (:u, :c, :a, :e, :i, :d, :ip, :ua, NOW())
        """), {"u": uid if str(uid or "").isdigit() else None, "c": cond, "a": acao, "e": entidade, "i": rid,
               "d": json.dumps(detalhes, ensure_ascii=False, default=str), "ip": (ip or "")[:45] or None,
               "ua": (headers.get("user-agent") or "")[:500] or None})
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"AUDITORIA falhou ao registrar {metodo} {caminho}: {e}")
    finally:
        db.close()


class AuditoriaMiddleware:
    """Middleware ASGI puro: guarda o corpo do pedido e da resposta sem interferir na rota."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)
        regra = _regra(scope["method"], scope["path"]) or self._regra_equipe(scope)
        if not regra:
            return await self.app(scope, receive, send)

        mensagens, corpo_bytes = [], bytearray()
        while True:
            m = await receive()
            mensagens.append(m)
            if m["type"] != "http.request":
                break
            corpo_bytes += m.get("body", b"")
            if not m.get("more_body"):
                break
        fila = list(mensagens)

        async def receive_repetido():
            return fila.pop(0) if fila else await receive()

        antes = None
        if scope["method"] in ("DELETE", "PUT", "PATCH"):
            antes = await run_in_threadpool(self._antes, regra)

        estado = {"status": 0}
        resposta = bytearray()

        async def send_espiao(msg):
            if msg["type"] == "http.response.start":
                estado["status"] = msg["status"]
            elif msg["type"] == "http.response.body" and len(resposta) < 50000:
                resposta.extend(msg.get("body", b""))
            await send(msg)

        await self.app(scope, receive_repetido, send_espiao)

        if 200 <= estado["status"] < 300:
            try:
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
                corpo = _json(corpo_bytes) if "json" in headers.get("content-type", "") else {}
                await run_in_threadpool(_gravar, scope["method"], scope["path"], regra,
                                        antes, corpo, _json(resposta), headers)
            except Exception as e:
                logger.warning(f"AUDITORIA erro: {e}")

    @staticmethod
    def _regra_equipe(scope):
        """2026-10-04: escrita feita pela equipe (admin_sistema ou financeiro) → registra como ação genérica."""
        if _EQUIPE_IGNORAR.search(scope["path"]):
            return None
        auth = ""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                auth = v.decode("latin-1"); break
        u = _usuario_do_token(auth)
        if (u.get("role") or "").lower() == "admin_sistema" or u.get("painel") == "financeiro":
            return ("acao_equipe", "sistema", None, None)
        return None

    @staticmethod
    def _antes(regra):
        db = SessionLocal()
        try:
            return _registro(db, regra[2], regra[3])
        finally:
            db.close()


def _json(b: bytes):
    try:
        return json.loads(bytes(b).decode("utf-8")) if b else {}
    except Exception:
        return {}
