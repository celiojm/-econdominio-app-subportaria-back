# ============================================================================
# ARQUIVO: financeiro_contratos_licenca.py
# PASTA: /home/visionlpr/app_subportaria_back/financeiro/
# DESCRIÇÃO: Menu "Contratos" do financeiro — Contrato de Licença de Uso, Acesso e Suporte do eCondomínio.
#            Condomínio já cadastrado (dados do cadastro) ou só CNPJ (busca na Receita via ReceitaWS);
#            plano (mensal/trimestral/semestral/anual, preços da mesma regra de /preco/planos), início,
#            1º vencimento e período de teste (padrão 14 dias). Gera o PDF (reportlab), grava no STORAGE
#            (POST /upload/documento → pasta documentos, sem acesso público) e registra em
#            contratos_condominio (quem gerou vem do login). Download sempre pelo backend (com login).
#            Rotas (prefixo /api/financeiro): GET /contratos-licenca, GET /contratos-licenca/condominio/{id},
#            GET /contratos-licenca/cnpj/{cnpj}, POST /contratos-licenca, GET /contratos-licenca/{id}/pdf
# VERSÃO: 1.0.0 - criação
# data criação: 2026-10-05 data alteração: 2026-10-05
# ============================================================================
import base64
import io
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.protecao_financeiro import usuario_interno

logger = logging.getLogger(__name__)
router = APIRouter(tags=["contratos-licenca"])

STORAGE_BASE_URL = os.getenv("IMAGE_STORAGE_BASE_URL") or os.getenv("STORAGE_URL", "http://127.0.0.1:4000")
STORAGE_API_KEY = os.getenv("IMAGE_STORAGE_API_KEY") or os.getenv("STORAGE_API_KEY", "")
PLANOS_MESES = {"mensal": 1, "trimestral": 3, "semestral": 6, "anual": 12}
PLANOS_NOME = {"mensal": "Mensal", "trimestral": "Trimestral", "semestral": "Semestral", "anual": "Anual"}
FORMA_PADRAO = "Boleto bancário ou PIX (fatura emitida pela plataforma Asaas)"
MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
            "setembro", "outubro", "novembro", "dezembro"]


# ─── utilidades ─────────────────────────────────────────────────────────────
def _digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _fmt_cnpj(v) -> str:
    d = _digitos(v)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else (v or "")


def _fmt_cpf(v) -> str:
    d = _digitos(v)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else (v or "")


def _moeda(v) -> str:
    s = f"{float(v or 0):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _data_br(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _data_extenso(d: date) -> str:
    return f"{d.day} de {MESES_PT[d.month - 1]} de {d.year}"


def _endereco(c: dict) -> str:
    partes = [c.get("endereco") or "", c.get("numero") or "", c.get("complemento") or ""]
    linha = ", ".join(p for p in partes if p).strip(", ")
    resto = " - ".join(p for p in [c.get("bairro") or "", f"{c.get('cidade') or ''}/{c.get('estado') or ''}".strip("/")] if p)
    cep = _digitos(c.get("cep"))
    cep = f"CEP {cep[:5]}-{cep[5:]}" if len(cep) == 8 else ""
    return ", ".join(p for p in [linha, resto, cep] if p)


def _tabela_planos(db: Session, qtd: Optional[int], condominio_id: Optional[int]) -> list:
    """Mesmos valores de GET /api/financeiro/preco/planos (fonte única da regra de preço)."""
    from financeiro.financeiro_preco_routes import listar_planos
    r = listar_planos(qtd=qtd or None, condominio_id=condominio_id, db=db)
    itens = r.get("planos", r) if isinstance(r, dict) else r
    return [i for i in itens if i.get("codigo") in PLANOS_MESES]


# ─── dados para a tela ──────────────────────────────────────────────────────
def _dados_condominio(db: Session, cid: int) -> dict:
    r = db.execute(text("""
        SELECT id, nome, cnpj, endereco, numero, complemento, bairro, cidade, estado, cep, telefone, email,
               sindico, total_apartamentos, plano_selecionado, forma_pagamento, valor_plano_final,
               cobranca_email, cobranca_whats
          FROM condominios WHERE id = :i"""), {"i": cid}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Condomínio não encontrado")
    c = dict(r._mapping)
    return {
        "condominio_id": c["id"], "razao_social": c["nome"] or "", "cnpj": _fmt_cnpj(c["cnpj"]),
        "endereco": _endereco(c), "cidade": c.get("cidade") or "", "uf": c.get("estado") or "",
        "representante": c.get("sindico") or "", "cpf_representante": "",
        "email": c.get("email") or c.get("cobranca_email") or "",
        "telefone": c.get("telefone") or c.get("cobranca_whats") or "",
        "quantidade_unidades": c.get("total_apartamentos") or None,
        "plano": (c.get("plano_selecionado") or "mensal").lower() if (c.get("plano_selecionado") or "").lower() in PLANOS_MESES else "mensal",
        "forma_pagamento": FORMA_PADRAO,
    }


@router.get("/contratos-licenca")
def listar_contratos(condominio_id: Optional[int] = Query(None), db: Session = Depends(get_db),
                     quem: dict = Depends(usuario_interno)):
    sql = """SELECT k.id, k.condominio_id, k.cnpj, k.razao_social, k.plano, k.valor, k.inicio_vigencia,
                    k.primeiro_vencimento, k.periodo_teste_dias, k.arquivo, k.status, k.criado_por_nome, k.criado_em
               FROM contratos_condominio k"""
    prm = {}
    if condominio_id:
        sql += " WHERE k.condominio_id = :c"
        prm["c"] = condominio_id
    itens = []
    for r in db.execute(text(sql + " ORDER BY k.id DESC LIMIT 300"), prm):
        d = dict(r._mapping)
        for k in ("inicio_vigencia", "primeiro_vencimento", "criado_em"):
            d[k] = d[k].isoformat() if d.get(k) else None
        d["valor"] = float(d["valor"] or 0)
        itens.append(d)
    return {"items": itens}


@router.get("/contratos-licenca/condominio/{condominio_id}")
def dados_condominio(condominio_id: int, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    d = _dados_condominio(db, condominio_id)
    d["planos"] = _tabela_planos(db, d["quantidade_unidades"], condominio_id)
    return d


@router.get("/contratos-licenca/cnpj/{cnpj}")
def buscar_cnpj(cnpj: str, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    dig = _digitos(cnpj)
    if len(dig) != 14:
        raise HTTPException(status_code=400, detail="CNPJ inválido (precisa ter 14 dígitos)")
    # já cadastrado? usa o cadastro
    cid = db.execute(text("""SELECT id FROM condominios
                              WHERE REPLACE(REPLACE(REPLACE(cnpj,'.',''),'/',''),'-','') = :d
                              ORDER BY ativo DESC, id DESC LIMIT 1"""), {"d": dig}).scalar()
    if cid:
        d = _dados_condominio(db, cid)
        d["planos"] = _tabela_planos(db, d["quantidade_unidades"], cid)
        d["origem"] = "cadastro"
        return d
    # 4xx em vez de 502/504 (o Cloudflare troca 5xx de gateway pela página dele — CLAUDE.md 15-S)
    try:
        resp = requests.get(f"https://www.receitaws.com.br/v1/cnpj/{dig}", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=424, detail="A consulta à Receita não respondeu. Tente de novo ou preencha à mão.")
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Muitas consultas seguidas na Receita (limite de 3 por minuto). Aguarde um pouco.")
    try:
        r = resp.json()
    except ValueError:
        r = {}
    if resp.status_code != 200 or r.get("status") != "OK":
        raise HTTPException(status_code=404, detail=r.get("message") or "CNPJ não encontrado na Receita")
    c = {"endereco": r.get("logradouro"), "numero": r.get("numero"), "complemento": r.get("complemento"),
         "bairro": r.get("bairro"), "cidade": r.get("municipio"), "estado": r.get("uf"), "cep": r.get("cep")}
    return {
        "condominio_id": None, "origem": "receita", "razao_social": r.get("nome") or r.get("fantasia") or "",
        "cnpj": _fmt_cnpj(dig), "endereco": _endereco(c), "cidade": r.get("municipio") or "", "uf": r.get("uf") or "",
        "representante": "", "cpf_representante": "", "email": r.get("email") or "", "telefone": r.get("telefone") or "",
        "quantidade_unidades": None, "plano": "mensal", "forma_pagamento": FORMA_PADRAO, "situacao_receita": r.get("situacao"),
        "planos": _tabela_planos(db, None, None),
    }


# ─── geração ────────────────────────────────────────────────────────────────
class NovoContrato(BaseModel):
    model_config = ConfigDict(extra="ignore")
    condominio_id: Optional[int] = None
    razao_social: str = Field(..., min_length=3, max_length=255)
    cnpj: Optional[str] = Field(None, max_length=20)
    endereco: Optional[str] = Field(None, max_length=400)
    cidade: Optional[str] = Field(None, max_length=120)
    uf: Optional[str] = Field(None, max_length=2)
    representante: Optional[str] = Field(None, max_length=150)
    cpf_representante: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=150)
    telefone: Optional[str] = Field(None, max_length=40)
    quantidade_unidades: Optional[int] = Field(None, ge=1, le=100000)
    plano: str
    valor: float = Field(..., gt=0)
    forma_pagamento: Optional[str] = Field(None, max_length=150)
    inicio_vigencia: date
    primeiro_vencimento: date
    periodo_teste_dias: int = Field(14, ge=0, le=365)


@router.post("/contratos-licenca")
def gerar_contrato(dados: NovoContrato, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    plano = (dados.plano or "").lower()
    if plano not in PLANOS_MESES:
        raise HTTPException(status_code=400, detail="Plano inválido (mensal, trimestral, semestral ou anual)")
    if dados.condominio_id:
        _dados_condominio(db, dados.condominio_id)  # confere se existe
    if dados.primeiro_vencimento < dados.inicio_vigencia:
        raise HTTPException(status_code=400, detail="O primeiro vencimento não pode ser antes do início da vigência")
    planos = _tabela_planos(db, dados.quantidade_unidades, dados.condominio_id)
    pdf = montar_pdf(dados, plano, planos)

    try:
        prefixo = f"contrato_{dados.condominio_id or _digitos(dados.cnpj)[:14] or 'novo'}_{datetime.now():%Y%m%d}"
        resp = httpx.post(f"{STORAGE_BASE_URL}/upload/documento", headers={"x-api-key": STORAGE_API_KEY},
                          json={"base64": base64.b64encode(pdf).decode(), "prefixo": prefixo}, timeout=30)
        resp.raise_for_status()
        arquivo = resp.json()["filename"]
    except Exception as e:
        logger.error("CONTRATO: falha ao gravar o PDF no storage: %s", e)
        raise HTTPException(status_code=424, detail="Não foi possível salvar o PDF no storage. Tente de novo.")

    resumo = dados.model_dump(mode="json")
    resumo["plano_nome"] = PLANOS_NOME[plano]
    resumo["planos_tabela"] = planos
    r = db.execute(text("""
        INSERT INTO contratos_condominio (condominio_id, cnpj, razao_social, plano, valor, forma_pagamento,
            inicio_vigencia, primeiro_vencimento, periodo_teste_dias, quantidade_unidades, dados, arquivo,
            tamanho_bytes, criado_por_nome)
        VALUES (:cid, :cnpj, :rs, :pl, :v, :fp, :ini, :venc, :teste, :qtd, :dados, :arq, :tam, :quem)"""),
        {"cid": dados.condominio_id, "cnpj": _fmt_cnpj(dados.cnpj) or None, "rs": dados.razao_social.strip(),
         "pl": plano, "v": round(dados.valor, 2), "fp": dados.forma_pagamento or FORMA_PADRAO,
         "ini": dados.inicio_vigencia, "venc": dados.primeiro_vencimento, "teste": dados.periodo_teste_dias,
         "qtd": dados.quantidade_unidades, "dados": json.dumps(resumo, ensure_ascii=False), "arq": arquivo,
         "tam": len(pdf), "quem": quem.get("nome")})
    db.commit()
    return {"success": True, "id": r.lastrowid, "arquivo": arquivo, "tamanho_bytes": len(pdf)}


@router.get("/contratos-licenca/{contrato_id}/pdf")
def baixar_pdf(contrato_id: int, db: Session = Depends(get_db), quem: dict = Depends(usuario_interno)):
    r = db.execute(text("SELECT razao_social, arquivo, criado_em FROM contratos_condominio WHERE id = :i"),
                   {"i": contrato_id}).fetchone()
    if not r or not r.arquivo:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    try:
        resp = httpx.get(f"{STORAGE_BASE_URL}/storage/documento/{r.arquivo}", headers={"x-api-key": STORAGE_API_KEY}, timeout=30)
    except Exception:
        raise HTTPException(status_code=424, detail="Storage indisponível no momento")
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="PDF não encontrado no storage")
    nome = re.sub(r"[^A-Za-z0-9]+", "_", r.razao_social or "condominio").strip("_")[:60]
    return Response(content=resp.content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Contrato_{nome}_{r.criado_em:%Y%m%d}.pdf"'})


# ─── PDF ────────────────────────────────────────────────────────────────────
ESCOPO = [
    "Aplicativo para uso da portaria em dispositivos compatíveis (iOS e Android).",
    "Registro de encomendas por foto da etiqueta.",
    "Notificação automática via WhatsApp para moradores, sujeita à disponibilidade e às políticas técnicas da plataforma de terceiros.",
    "Histórico de encomendas, fotos e controle de retirada.",
    "Painel administrativo para síndico e gestão.",
    "Suporte remoto pelos canais oficiais da eCondomínio, inclusive WhatsApp.",
]

PREAMBULO = ("Pelo presente instrumento particular, de um lado, <b>E-CONDOMINIO SISTEMAS DE GESTAO LTDA</b>, inscrita no CNPJ "
             "nº 64.931.933/0001-85, com sede na Rua Professora Sofia Quint de Souza, nº 544, Capoeiras, Florianópolis/SC, "
             "CEP 88.085-040, e-mail econdominio@econdominio.com.br, doravante denominada <b>CONTRATADA</b>; e, de outro lado, "
             "a pessoa jurídica identificada no Quadro Resumo, neste ato representada por seu representante legal ou síndico, "
             "doravante denominada <b>CONTRATANTE</b>, resolvem celebrar o presente Contrato de Licença de Uso, Acesso e Suporte "
             "do Software eCondomínio, mediante as cláusulas e condições a seguir.")

CLAUSULAS = [
    ("CLÁUSULA 1 — OBJETO E LICENÇA DE USO", [
        "1.1. O presente Contrato tem por objeto a concessão, pela CONTRATADA à CONTRATANTE, de licença temporária, limitada, não exclusiva e intransferível de acesso e uso do software eCondomínio, destinado à gestão de encomendas e rotinas relacionadas à portaria, durante a vigência contratual.",
        "1.2. A licença compreende as funcionalidades indicadas no Quadro Resumo, no Escopo Incluído e no Anexo I, além das atualizações evolutivas e corretivas disponibilizadas pela CONTRATADA para a versão contratada, sem transferência de titularidade sobre o software, código-fonte, marcas, bancos de dados estruturais, métodos ou demais direitos de propriedade intelectual.",
        "1.3. A CONTRATANTE utilizará o sistema exclusivamente em suas operações internas e por usuários autorizados, responsabilizando-se pela correta gestão de credenciais e pelos atos praticados em suas contas.",
        "1.4. A contratação não inclui o fornecimento de internet, dispositivos móveis, computadores, equipamentos de rede ou outros itens de hardware, salvo quando houver previsão expressa e específica no Quadro Resumo ou em proposta complementar.",
    ]),
    ("CLÁUSULA 2 — PLANOS, VIGÊNCIA, PREÇO E FORMA DE PAGAMENTO", [
        "2.1. A CONTRATANTE declara ter escolhido o plano indicado no Quadro Resumo, dentre as modalidades mensal, trimestral, semestral e anual, observados os respectivos valores e condições comerciais.",
        "2.2. O plano mensal possui ciclo de cobrança mensal e permanecerá ativo enquanto vigente a contratação, observadas as regras de cancelamento deste instrumento. Os planos trimestral, semestral e anual possuem, respectivamente, ciclos de 3 (três), 6 (seis) e 12 (doze) meses.",
        "2.3. Nos planos trimestral, semestral e anual, o valor integral do ciclo contratado será pago antecipadamente em parcela única, salvo condição diferente expressamente registrada no Quadro Resumo.",
        "2.4. O início da vigência remunerada, o primeiro vencimento e eventual período de teste serão os constantes do Quadro Resumo. Período de teste, quando concedido, não gera cobrança durante o intervalo expressamente indicado.",
        "2.5. Os valores permanecerão fixos durante o ciclo contratual em curso. Qualquer alteração comercial para ciclo posterior dependerá de informação prévia e aceite da CONTRATANTE, inclusive por meio eletrônico.",
        "2.6. A implantação inicial e as orientações usuais de ativação não terão taxa adicional, salvo se serviço extraordinário ou customizado estiver expressamente previsto em proposta específica.",
        "2.7. Encerrado o ciclo trimestral, semestral ou anual, eventual renovação dependerá de manifestação das partes, por aditivo, nova proposta ou aceite eletrônico. Não haverá renovação automática desses ciclos sem concordância da CONTRATANTE.",
    ]),
    ("CLÁUSULA 3 — IMPLANTAÇÃO, SUPORTE E RESPONSABILIDADES DA CONTRATADA", [
        "3.1. A CONTRATADA realizará a disponibilização inicial do ambiente e prestará as orientações razoavelmente necessárias à implantação do sistema.",
        "3.2. A CONTRATADA prestará suporte remoto pelos canais oficiais informados à CONTRATANTE e empregará esforços técnicos para correção de falhas reproduzíveis atribuíveis ao software.",
        "3.3. A CONTRATADA poderá realizar atualizações, correções e manutenções, procurando minimizar impactos na operação. Manutenções programadas relevantes serão comunicadas previamente sempre que tecnicamente possível.",
        "3.4. Alterações substanciais de escopo, integrações customizadas, desenvolvimento sob demanda ou serviços não descritos neste instrumento dependerão de orçamento e aceite específicos.",
    ]),
    ("CLÁUSULA 4 — DISPONIBILIDADE E CONTINUIDADE DO SERVIÇO", [
        "4.1. A CONTRATADA empregará infraestrutura e procedimentos técnicos compatíveis com a natureza do serviço, buscando disponibilidade mensal mínima de 99,5% (noventa e nove vírgula cinco por cento).",
        "4.2. Não serão consideradas falhas imputáveis à CONTRATADA as indisponibilidades decorrentes de: (a) internet, energia, rede local ou equipamentos da CONTRATANTE; (b) falhas, bloqueios ou mudanças de serviços de terceiros, inclusive WhatsApp/Meta, lojas de aplicativos, operadoras, nuvem ou provedores externos; (c) manutenção programada; (d) uso inadequado, credenciais comprometidas ou alterações feitas pela CONTRATANTE; e (e) caso fortuito ou força maior.",
        "4.3. Havendo indisponibilidade relevante diretamente atribuível ao eCondomínio, a CONTRATADA atuará para restabelecer o serviço no menor prazo razoavelmente possível e manterá a CONTRATANTE informada quando a falha tiver impacto operacional significativo.",
        "4.4. Caso uma falha grave e contínua imputável à CONTRATADA impeça substancialmente o uso da solução e não seja solucionada em até 15 (quinze) dias corridos após notificação formal da CONTRATANTE, poderá ser aplicada a rescisão por descumprimento prevista neste Contrato.",
    ]),
    ("CLÁUSULA 5 — OBRIGAÇÕES DA CONTRATANTE", [
        "5.1. Compete à CONTRATANTE: (a) fornecer dados corretos para implantação; (b) manter equipamentos, conexão à internet e dispositivos compatíveis; (c) cadastrar apenas usuários autorizados; (d) orientar seus colaboradores quanto ao uso adequado; (e) zelar pelas credenciais de acesso; e (f) comunicar prontamente incidentes, acessos indevidos ou falhas percebidas.",
        "5.2. A CONTRATANTE é responsável pela legitimidade dos dados pessoais inseridos no sistema, pelas bases legais aplicáveis às operações de tratamento sob sua decisão e pelas informações fornecidas aos moradores, funcionários, destinatários e demais titulares, quando exigidas pela legislação.",
        "5.3. É vedado utilizar o sistema para finalidade ilícita, violar direitos de terceiros, tentar acessar áreas não autorizadas, realizar engenharia reversa, copiar indevidamente o software ou praticar atos que comprometam a segurança da plataforma.",
    ]),
    ("CLÁUSULA 6 — PROTEÇÃO DE DADOS PESSOAIS E LGPD", [
        "6.1. As partes comprometem-se a cumprir a Lei nº 13.709/2018 (Lei Geral de Proteção de Dados Pessoais — LGPD) e demais normas aplicáveis ao tratamento de dados pessoais realizado no contexto deste Contrato.",
        "6.2. Nas operações em que a CONTRATANTE determinar as finalidades e os meios essenciais do tratamento de dados de moradores, destinatários, visitantes, colaboradores ou outros titulares inseridos no eCondomínio, a CONTRATANTE atuará como Controladora e a CONTRATADA atuará como Operadora, tratando os dados para execução do serviço contratado e de acordo com instruções lícitas e documentadas da CONTRATANTE.",
        "6.3. A CONTRATADA adotará medidas técnicas e administrativas adequadas e proporcionais para proteger os dados pessoais contra acessos não autorizados e situações acidentais ou ilícitas de destruição, perda, alteração, comunicação, divulgação ou tratamento inadequado.",
        "6.4. O acesso a dados pessoais será limitado às pessoas que necessitem deles para prestar, manter, administrar ou suportar o serviço, sujeitas a deveres de confidencialidade e segurança.",
        "6.5. A CONTRATADA poderá utilizar fornecedores de infraestrutura, hospedagem, comunicação e outros suboperadores necessários à prestação do serviço, exigindo deles obrigações de proteção de dados compatíveis com as atividades desempenhadas.",
        "6.6. Em caso de incidente de segurança envolvendo dados pessoais tratados no âmbito deste Contrato, a CONTRATADA comunicará a CONTRATANTE sem demora indevida após tomar ciência, fornecendo, na medida em que estiverem disponíveis, as informações necessárias para avaliação, contenção e cumprimento das obrigações legais aplicáveis.",
        "6.7. A CONTRATADA colaborará, dentro dos limites técnicos e razoáveis do serviço contratado, com solicitações da CONTRATANTE relacionadas ao exercício de direitos dos titulares e ao cumprimento de obrigações legais de proteção de dados.",
        "6.8. Dados cadastrais e contratuais da própria CONTRATANTE, de seus representantes e contatos poderão ser tratados pela CONTRATADA como controladora independente quando necessários a faturamento, suporte, segurança, prevenção a fraude, cumprimento de obrigações legais e gestão da relação comercial.",
        "6.9. Encerrado o Contrato, a CONTRATANTE poderá solicitar exportação razoável dos dados disponíveis durante o período de até 15 (quinze) dias, observadas as funcionalidades técnicas existentes. Após esse prazo, a CONTRATADA poderá eliminar ou anonimizar dados do ambiente ativo e das rotinas ordinárias, ressalvados prazos de backup, obrigações legais, exercício regular de direitos e registros que devam ser preservados.",
    ]),
    ("CLÁUSULA 7 — CONFIDENCIALIDADE", [
        "7.1. Cada parte deverá manter sob sigilo informações técnicas, comerciais, operacionais, credenciais, documentos e dados não públicos recebidos da outra parte em razão deste Contrato, utilizando-os somente para sua execução.",
        "7.2. O dever de confidencialidade não se aplica a informações comprovadamente públicas sem violação deste Contrato, já conhecidas legitimamente pela parte receptora, recebidas de terceiro autorizado ou cuja divulgação seja exigida por lei ou autoridade competente.",
        "7.3. O dever de confidencialidade permanecerá vigente por 5 (cinco) anos após o término do Contrato, sem prejuízo de proteção por prazo superior quando a natureza da informação ou a lei assim exigir.",
    ]),
    ("CLÁUSULA 8 — SERVIÇOS E PLATAFORMAS DE TERCEIROS", [
        "8.1. Algumas funcionalidades podem depender de serviços de terceiros, tais como conexão à internet, sistema operacional, lojas de aplicativos, WhatsApp/Meta e provedores de infraestrutura. A CONTRATADA não garante a continuidade de recursos que sejam unilateralmente descontinuados, bloqueados ou alterados por esses terceiros.",
        "8.2. Caso alteração obrigatória de política, tecnologia ou tarifação de terceiro gere custo novo e material para manutenção de funcionalidade incluída, a CONTRATADA comunicará previamente a CONTRATANTE. Qualquer repasse de custo ou mudança comercial dependerá de acordo entre as partes e não produzirá efeitos retroativos.",
    ]),
    ("CLÁUSULA 9 — PROPRIEDADE INTELECTUAL", [
        "9.1. O eCondomínio, suas interfaces, documentação, marcas, logotipos, métodos, códigos e componentes permanecem de titularidade da CONTRATADA ou de seus licenciantes. Este Contrato concede apenas direito de uso durante a vigência.",
        "9.2. Os dados operacionais inseridos pela CONTRATANTE e os conteúdos por ela produzidos permanecem vinculados à CONTRATANTE, respeitados os direitos dos respectivos titulares de dados pessoais e as obrigações legais de cada parte.",
    ]),
    ("CLÁUSULA 10 — RESPONSABILIDADE", [
        "10.1. Cada parte responderá pelos danos diretos que comprovadamente causar à outra em decorrência de descumprimento contratual, dolo ou culpa, observados o nexo causal e a legislação aplicável.",
        "10.2. A CONTRATADA não será responsável por extravios físicos de encomendas, atos de porteiros, moradores ou terceiros, decisões administrativas do condomínio, falhas de conferência física, indisponibilidade de equipamentos locais ou eventos que não decorram de defeito do software.",
        "10.3. O sistema constitui ferramenta de apoio à gestão e à rastreabilidade e não substitui os procedimentos internos de segurança, conferência, entrega, fiscalização e controle adotados pela CONTRATANTE.",
        "10.4. Nenhuma disposição deste instrumento excluirá responsabilidades que, por lei, não possam ser afastadas, inclusive em hipóteses de dolo, violação deliberada de confidencialidade ou descumprimento de deveres legais de proteção de dados.",
    ]),
    ("CLÁUSULA 11 — INADIMPLEMENTO E SUSPENSÃO", [
        "11.1. O não pagamento de valor no respectivo vencimento caracteriza inadimplemento da CONTRATANTE.",
        "11.2. Persistindo o inadimplemento após notificação formal e não sendo a situação regularizada em até 15 (quinze) dias corridos do recebimento da notificação, a CONTRATADA poderá suspender o acesso ao sistema e/ou rescindir o Contrato, permanecendo exigíveis apenas os valores vencidos e os serviços regularmente disponibilizados até a data efetiva do encerramento.",
        "11.3. A suspensão por inadimplemento não impede a CONTRATANTE de solicitar o cancelamento nos termos da Cláusula 12, sem prejuízo da quitação de valores vencidos.",
    ]),
    ("CLÁUSULA 12 — CANCELAMENTO, RESCISÃO E DEVOLUÇÃO DE VALORES", [
        "12.1. A CONTRATANTE poderá solicitar o cancelamento do serviço a qualquer momento, sem multa, penalidade de fidelidade ou cobrança do saldo integral do ciclo contratado, desde que comunique formalmente a CONTRATADA com antecedência mínima de 30 (trinta) dias.",
        "12.2. Durante o prazo de aviso prévio de 30 (trinta) dias, o serviço permanecerá disponível e os valores correspondentes a esse período continuarão devidos. A data efetiva de encerramento será o término do aviso prévio, salvo acordo escrito entre as partes.",
        "12.3. Nos planos trimestral, semestral ou anual pagos antecipadamente, a CONTRATADA restituirá à CONTRATANTE os valores correspondentes ao período não utilizado posterior à data efetiva do encerramento, calculados proporcionalmente sobre o valor efetivamente pago no plano contratado (pro rata temporis; o valor será exatamente o valor já pago, não se aplicando reajuste, multa, juros etc.).",
        "12.4. A restituição prevista no item 12.3 será realizada em até 15 (quinze) dias úteis após a data efetiva do encerramento e a apuração dos valores, preferencialmente pelo mesmo meio de pagamento quando tecnicamente possível, ou por transferência para conta indicada pela CONTRATANTE.",
        "12.5. No plano mensal, não haverá restituição de valores referentes ao período de serviço já prestado ou abrangido pelo aviso prévio, permanecendo devidos os valores até a data efetiva do encerramento.",
        "12.6. O pedido de cancelamento deverá ser realizado por escrito, por meio que permita comprovar seu envio e conteúdo, inclusive e-mail corporativo, plataforma de assinatura eletrônica ou outro canal formal acordado entre as partes. A simples interrupção do uso do software, desinstalação do aplicativo ou interrupção unilateral dos pagamentos não constitui pedido válido de cancelamento.",
        "12.7. O Contrato poderá ser rescindido por qualquer das partes em caso de descumprimento relevante da outra parte, desde que o inadimplemento seja comunicado por escrito e não seja sanado no prazo de 15 (quinze) dias corridos contados do recebimento da notificação, quando a correção for possível.",
        "12.8. Se a rescisão decorrer de descumprimento relevante imputável à CONTRATADA, a CONTRATANTE poderá encerrar o Contrato sem necessidade de cumprir o aviso prévio de 30 (trinta) dias, sendo restituídos os valores pagos antecipadamente referentes ao período posterior à data efetiva da rescisão.",
        "12.9. A rescisão motivada por uso ilícito do sistema, violação grave das regras de segurança ou outro descumprimento relevante imputável à CONTRATANTE poderá resultar na suspensão imediata do acesso quando necessária à proteção da plataforma, dos dados ou de terceiros, sem prejuízo da posterior formalização da rescisão.",
    ]),
    ("CLÁUSULA 13 — ENCERRAMENTO DO ACESSO E PORTABILIDADE DOS DADOS", [
        "13.1. Na data efetiva do encerramento, o acesso ordinário da CONTRATANTE ao sistema poderá ser desativado, ressalvado eventual período técnico concedido exclusivamente para exportação de dados.",
        "13.2. A CONTRATANTE deverá solicitar eventual exportação de dados no prazo e nas condições previstos na Cláusula 6.9.",
        "13.3. O encerramento do Contrato não afasta obrigações de confidencialidade, proteção de dados, propriedade intelectual, pagamento de valores vencidos ou demais obrigações que, por sua natureza, devam sobreviver ao término.",
    ]),
    ("CLÁUSULA 14 — COMUNICAÇÕES", [
        "14.1. Comunicações operacionais poderão ser realizadas pelos canais usuais de suporte. Notificações de inadimplemento, pedido de cancelamento, rescisão, suspensão, incidente relevante de segurança ou alteração contratual deverão ser realizadas por meio que permita comprovar o envio, o recebimento e o respectivo conteúdo.",
        "14.2. As partes comprometem-se a manter seus dados de contato atualizados durante a vigência.",
    ]),
    ("CLÁUSULA 15 — DISPOSIÇÕES GERAIS", [
        "15.1. Este Contrato, o Quadro Resumo e seus anexos constituem o entendimento entre as partes sobre o objeto contratado e substituem tratativas comerciais anteriores que forem incompatíveis com seu conteúdo.",
        "15.2. Em caso de divergência entre disposições comerciais genéricas deste instrumento e condições específicas expressamente preenchidas no Quadro Resumo ou em proposta aceita e vinculada a este Contrato, prevalecerão as condições específicas, desde que não contrariem obrigação legal.",
        "15.3. A tolerância quanto ao descumprimento de qualquer obrigação não constituirá novação ou renúncia de direito.",
        "15.4. Se qualquer disposição for considerada inválida ou inexequível, as demais permanecerão válidas, devendo as partes substituir a disposição afetada por outra que preserve, na maior medida possível, sua finalidade econômica e jurídica.",
        "15.5. Nenhuma parte poderá ceder integralmente este Contrato a terceiro sem anuência da outra, exceto em reorganização societária que não reduza as garantias de cumprimento.",
        "15.6. O presente instrumento poderá ser assinado eletronicamente por método apto a demonstrar autoria e integridade, produzindo os mesmos efeitos da assinatura física, na forma da legislação aplicável.",
    ]),
    ("CLÁUSULA 16 — FORO", [
        "16.1. Fica eleito o foro da Comarca de Florianópolis/SC para dirimir controvérsias decorrentes deste Contrato, ressalvadas hipóteses legais de competência diversa e privilegiando-se, antes do ajuizamento, tentativa de solução amigável entre as partes.",
    ]),
]


def _texto_teste(d: NovoContrato) -> str:
    if not d.periodo_teste_dias:
        return "Não se aplica"
    fim = d.inicio_vigencia + timedelta(days=d.periodo_teste_dias - 1)
    return f"{d.periodo_teste_dias} dias (de {_data_br(d.inicio_vigencia)} a {_data_br(fim)})"


def montar_pdf(d: NovoContrato, plano: str, planos: list) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from xml.sax.saxutils import escape as esc

    base = getSampleStyleSheet()
    s_tit = ParagraphStyle("tit", parent=base["Title"], fontName="Helvetica-Bold", fontSize=14, leading=18, spaceAfter=2)
    s_sub = ParagraphStyle("sub", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=11, alignment=TA_CENTER, leading=14)
    s_mini = ParagraphStyle("mini", parent=base["Normal"], fontSize=8, alignment=TA_CENTER, textColor=colors.HexColor("#555555"), spaceAfter=10)
    s_h = ParagraphStyle("h", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=10, spaceAfter=4)
    s_p = ParagraphStyle("p", parent=base["Normal"], fontSize=9, leading=12.5, alignment=TA_JUSTIFY, spaceAfter=4)
    s_cel = ParagraphStyle("cel", parent=base["Normal"], fontSize=8.5, leading=11)
    s_celb = ParagraphStyle("celb", parent=s_cel, fontName="Helvetica-Bold")
    azul = colors.HexColor("#1e3a8a")
    cinza = colors.HexColor("#eef2f7")

    def P(t, st=s_p):
        return Paragraph(t, st)

    def tabela(linhas, larguras, cabecalho=False):
        """cabecalho=True: 1ª linha é o título das colunas (fundo azul). Senão: 1ª coluna é o rótulo (fundo cinza)."""
        dados = []
        for i, linha in enumerate(linhas):
            cels = []
            for j, c in enumerate(linha):
                txt = esc(str(c))
                if cabecalho and i == 0:
                    cels.append(Paragraph(f'<font color="white"><b>{txt}</b></font>', s_cel))
                elif not cabecalho and j == 0:
                    cels.append(Paragraph(txt, s_celb))
                else:
                    cels.append(Paragraph(txt, s_cel))
            dados.append(cels)
        st = [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b8")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        st.append(("BACKGROUND", (0, 0), (-1, 0), azul) if cabecalho else ("BACKGROUND", (0, 0), (0, -1), cinza))
        t = Table(dados, colWidths=larguras)
        t.setStyle(TableStyle(st))
        return t

    plano_nome = PLANOS_NOME[plano]
    meses = PLANOS_MESES[plano]
    forma = d.forma_pagamento or FORMA_PADRAO
    teste = _texto_teste(d)
    representante = (d.representante or "").strip()
    if d.cpf_representante:
        representante += f" — CPF {_fmt_cpf(d.cpf_representante)}"
    contato = " / ".join(x for x in [d.email or "", d.telefone or ""] if x)

    linhas_planos = [["Plano", "Valor", "Forma de pagamento", "Equivalente mensal"]]
    for p in planos:
        desc = float(p.get("desconto_pct") or 0)
        nome = p.get("nome") or PLANOS_NOME.get(p.get("codigo"), "")
        if desc:
            nome += f" — {desc:.0f}% OFF"
        vm, vt = p.get("valor_mensal"), p.get("valor_total")
        if vm is None:
            linhas_planos.append([nome, "conforme proposta", "—", "—"])
        elif p.get("meses", 1) <= 1:
            linhas_planos.append([nome, f"{_moeda(vm)} por mês", "Mensal", _moeda(vm)])
        else:
            linhas_planos.append([nome, _moeda(vt), "Pagamento único", f"{_moeda(vm)}/mês"])

    w = A4[0] - 4 * cm
    story = [
        P("CONTRATO DE LICENÇA DE USO, ACESSO E SUPORTE", s_tit),
        P("SOFTWARE eCONDOMÍNIO", s_sub),
        P("MODELO PADRÃO — DADOS ESPECÍFICOS DA CONTRATAÇÃO NO QUADRO RESUMO", s_mini),
        P("QUADRO RESUMO DA CONTRATAÇÃO", s_h),
        tabela([
            ["CONTRATANTE / CONDOMÍNIO", d.razao_social.strip()],
            ["CNPJ", _fmt_cnpj(d.cnpj) or "—"],
            ["ENDEREÇO", d.endereco or "—"],
            ["REPRESENTANTE LEGAL / SÍNDICO", representante or "—"],
            ["E-MAIL / TELEFONE", contato or "—"],
            ["QUANTIDADE DE UNIDADES", str(d.quantidade_unidades or "—")],
            ["PLANO CONTRATADO", plano_nome.upper()],
            ["VALOR CONTRATADO", _moeda(d.valor) + (" por mês" if meses == 1 else f" (parcela única, ciclo de {meses} meses)")],
            ["FORMA DE PAGAMENTO", forma],
            ["INÍCIO DA VIGÊNCIA", _data_br(d.inicio_vigencia)],
            ["PRIMEIRO VENCIMENTO", _data_br(d.primeiro_vencimento)],
            ["PERÍODO DE TESTE, SE HOUVER", teste],
        ], [5.6 * cm, w - 5.6 * cm]),
        P("PLANOS COMERCIAIS DISPONÍVEIS", s_h),
        tabela(linhas_planos, [4.6 * cm, 4.0 * cm, 3.8 * cm, w - 12.4 * cm], cabecalho=True),
        Spacer(1, 4),
        P("<b>Observação comercial:</b> os planos trimestral, semestral e anual são pagos antecipadamente em parcela única. "
          "Os valores indicados acima correspondem à tabela comercial desta versão do contrato; havendo proposta específica "
          "aceita pelas partes, prevalecerão o plano e o valor registrados no Quadro Resumo."),
        P("ESCOPO INCLUÍDO", s_h),
    ]
    story += [P(f"• {esc(e)}") for e in ESCOPO]
    story += [Spacer(1, 6), P(PREAMBULO)]
    for titulo, itens in CLAUSULAS:
        story.append(KeepTogether([P(titulo, s_h), P(esc(itens[0]))]))
        story += [P(esc(i)) for i in itens[1:]]

    local = f"Florianópolis/SC e {esc(d.cidade or '________')}/{esc(d.uf or '__')}, {_data_extenso(date.today())}."
    linha = "_" * 42
    ass = Table([
        [P(linha, s_cel), P(linha, s_cel)],
        [P("<b>E-CONDOMINIO SISTEMAS DE GESTAO LTDA</b><br/>CNPJ 64.931.933/0001-85<br/>CONTRATADA", s_cel),
         P(f"<b>{esc(d.razao_social.strip())}</b><br/>CNPJ {esc(_fmt_cnpj(d.cnpj) or '________')}<br/>"
           f"{esc((d.representante or '').strip() or 'Representante legal / Síndico')}<br/>CONTRATANTE", s_cel)],
    ], colWidths=[w / 2, w / 2])
    ass.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, 0), 28)]))
    test = Table([
        [P(linha, s_cel), P(linha, s_cel)],
        [P("1. Nome: ______________________________<br/>CPF: _______________________________", s_cel),
         P("2. Nome: ______________________________<br/>CPF: _______________________________", s_cel)],
    ], colWidths=[w / 2, w / 2])
    test.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, 0), 24)]))
    story += [Spacer(1, 8), P("E, por estarem de acordo, as partes firmam o presente instrumento."), P(local),
              KeepTogether([ass, Spacer(1, 6), P("TESTEMUNHAS", s_h), test])]

    story += [PageBreak(), P("ANEXO I", s_tit), P("CONDIÇÕES COMERCIAIS E FUNCIONALIDADES", s_sub), Spacer(1, 8),
              P("1. CONDIÇÕES COMERCIAIS", s_h),
              tabela([
                  ["Plano contratado", plano_nome],
                  ["Prazo do ciclo", f"{meses} {'mês' if meses == 1 else 'meses'}"],
                  ["Valor contratado", _moeda(d.valor)],
                  ["Forma de pagamento", forma],
                  ["Início da vigência", _data_br(d.inicio_vigencia)],
                  ["Primeiro vencimento", _data_br(d.primeiro_vencimento)],
                  ["Período de teste, se houver", teste],
                  ["Implantação", "Sem taxa adicional, salvo proposta específica"],
                  ["Cancelamento", "Aviso prévio mínimo de 30 dias, sem multa"],
                  ["Planos antecipados", "Restituição proporcional do período não utilizado após a data efetiva do cancelamento"],
              ], [5.6 * cm, w - 5.6 * cm]),
              P("2. TABELA DE PLANOS", s_h),
              tabela(linhas_planos, [4.6 * cm, 4.0 * cm, 3.8 * cm, w - 12.4 * cm], cabecalho=True),
              P("3. FUNCIONALIDADES CONTEMPLADAS", s_h)]
    story += [P(f"• {esc(e)}") for e in ESCOPO]
    story += [Spacer(1, 6), P("Este Anexo integra o Contrato para todos os fins. Condições adicionais, módulos, integrações ou "
                              "serviços não expressamente descritos dependerão de proposta específica e aceite das partes.")]

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(2 * cm, 1.2 * cm, f"Contrato de Licença eCondomínio — {d.razao_social.strip()[:70]}")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                            title=f"Contrato de Licença eCondomínio — {d.razao_social.strip()}", author="E-CONDOMINIO SISTEMAS DE GESTAO LTDA")
    doc.build(story, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()
