from fastapi import APIRouter, Depends, HTTPException
# ALTERAÇÃO 2026-09-26: cobrança sempre BOLETO (boleto + PIX na mesma fatura), nunca só PIX
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from typing import Optional
import logging, os, httpx

logger = logging.getLogger(__name__)

try:
    from app.database import get_db
except ImportError:
    try:
        from financeiro_modelos import get_db
    except ImportError:
        from database import get_db

router = APIRouter()
ASAAS_API_KEY  = os.getenv("ASAAS_API_KEY", "")
ASAAS_ENV      = os.getenv("ASAAS_ENV", "sandbox")
ASAAS_BASE_URL = "https://api-sandbox.asaas.com/v3" if ASAAS_ENV == "sandbox" else "https://api.asaas.com/v3"

def _h(): return {"access_token": ASAAS_API_KEY, "Content-Type": "application/json"}
def _link(aid):
    if not aid: return None
    aid = aid.replace("pay_","")
    return f"https://sandbox.asaas.com/i/{aid}" if ASAAS_ENV=="sandbox" else f"https://www.asaas.com/i/{aid}"

def _get_cond(db, cid):
    try:
        r = db.execute(text("SELECT id,nome,validade_ate,total_apartamentos,plano_selecionado,valor_plano_final,valor_mensal_base,cobranca_responsavel,cobranca_email,cobranca_whats,email_financeiro,sindico,email,telefone,cnpj,asaas_customer_id FROM condominios WHERE id=:id"),{"id":cid}).fetchone()
        if not r: return None
        return {"id":r[0],"nome":r[1],"validade_ate":r[2].isoformat() if r[2] else None,"total_apartamentos":r[3],"plano_selecionado":r[4] or "mensal","valor_plano_final":float(r[5]) if r[5] else None,"valor_mensal_base":float(r[6]) if r[6] else None,"cobranca_responsavel":r[7],"cobranca_email":r[8],"cobranca_whats":r[9],"email_financeiro":r[10],"sindico":r[11],"email":r[12],"telefone":r[13],"cnpj":r[14],"asaas_customer_id":r[15]}
    except Exception as e:
        logger.error(f"Erro cond {cid}: {e}"); return None

@router.get("/cobrancas/historico/{id_condominio}")
async def get_historico(id_condominio: int, db: Session = Depends(get_db)):
    cond = _get_cond(db, id_condominio)
    if not cond: raise HTTPException(404, "Condomínio não encontrado")
    try:
        rows = db.execute(text("SELECT id_cobranca,valor,status,data_vencimento,data_pagamento,descricao,forma_pagamento,NULL,asaas_payment_id,id_cobranca,asaas_invoice_id,invoice_status,invoice_pdf_url FROM cobrancas WHERE id_condominio=:id ORDER BY data_vencimento DESC LIMIT 100"),{"id":id_condominio}).fetchall() 
    except:
        rows = []
    cobrancas=[]; tc=tr=0.0; pa=pe=ve=0; hoje=datetime.now().date()
    for r in rows:
        s=str(r[2] or "").lower()
        if s in("received","confirmed","pago"): status="pago"
        elif s in("pending","pendente"):
            v=r[3].date() if hasattr(r[3],"date") else r[3]
            status="vencido" if v and v<hoje else "pendente"
        elif s in("overdue","vencido"): status="vencido"
        elif s in("cancelled","cancelada"): status="cancelada"
        else: status=s
        val=float(r[1] or 0); tc+=val
        if status=="pago": pa+=1; tr+=val
        elif status=="pendente": pe+=1
        elif status=="vencido": ve+=1
        cobrancas.append({"id":r[0],"id_cobranca":r[9],"valor":val,"status":status,"data_vencimento":r[3].isoformat() if r[3] else None,"data_pagamento":r[4].isoformat() if r[4] else None,"descricao":r[5],"forma_pagamento":r[6],"link_pagamento":r[7] or _link(r[8]),"asaas_payment_id":r[8],"asaas_invoice_id":r[10],"invoice_status":r[11],"invoice_pdf_url":r[12]})
    resp=cond.get("cobranca_responsavel") or cond.get("sindico") or ""
    ecob=cond.get("cobranca_email") or cond.get("email_financeiro") or cond.get("email") or ""
    wcob=cond.get("cobranca_whats") or cond.get("telefone") or ""
    contato={"responsavel":resp,"email":ecob,"whatsapp":wcob} if (resp or ecob or wcob) else None
    return {"condominio":{"id":cond["id"],"nome":cond["nome"],"validade_ate":cond["validade_ate"],"total_apartamentos":cond["total_apartamentos"],"plano_selecionado":cond["plano_selecionado"],"valor_plano_final":cond["valor_plano_final"],"valor_mensal_base":cond["valor_mensal_base"]},"contato":contato,"cobrancas":cobrancas,"totalizadores":{"total":len(cobrancas),"pagas":pa,"pendentes":pe,"vencidas":ve,"total_cobrado":tc,"total_recebido":tr}}

@router.post("/cobrancas")
async def criar_cobranca(payload: dict, db: Session = Depends(get_db)):
    cid=payload.get("id_condominio") or payload.get("condominio_id")
    val=payload.get("valor"); venc=payload.get("data_vencimento")
    forma=payload.get("forma_pagamento","pix"); desc=payload.get("descricao","Mensalidade")
    if not cid or not val or not venc: raise HTTPException(422,"id_condominio, valor e data_vencimento obrigatórios")
    cond=_get_cond(db,cid)
    if not cond: raise HTTPException(404,"Condomínio não encontrado")
    bt="BOLETO"  # regra: sempre boleto + PIX, nunca só PIX
    if ASAAS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Buscar customer_id igual ao admin — query separada por nome de coluna
                row = db.execute(text("SELECT asaas_customer_id FROM condominios WHERE id=:id"),{"id":cid}).fetchone()
                customer_id = row.asaas_customer_id if row else None
                if not customer_id:
                    cnpj="".join(filter(str.isdigit,cond.get("cnpj") or ""))
                    email=cond.get("cobranca_email") or cond.get("email") or f"contato{cond['id']}@econdominio.com.br"
                    tel="".join(filter(str.isdigit,cond.get("cobranca_whats") or cond.get("telefone") or ""))
                    r=await client.post(f"{ASAAS_BASE_URL}/customers",headers=_h(),json={"name":cond["nome"],"cpfCnpj":cnpj,"email":email,"phone":tel[:11] if tel else None,"externalReference":f"COND_{cond['id']}"})
                    if r.status_code in(200,201):
                        customer_id=r.json().get("id")
                        db.execute(text("UPDATE condominios SET asaas_customer_id=:c WHERE id=:i"),{"c":customer_id,"i":cond["id"]}); db.commit()
                    else: raise Exception(f"Erro customer: {r.text}")
                r=await client.post(f"{ASAAS_BASE_URL}/payments",headers=_h(),json={"customer":customer_id,"billingType":bt,"value":float(val),"dueDate":venc,"description":desc or f"Mensalidade - {cond['nome']}","externalReference":f"COND_{cond['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}"})
                if r.status_code not in(200,201): raise Exception(f"Erro payment: {r.text}")
                apid=r.json().get("id"); lnk=r.json().get("invoiceUrl") or _link(apid)
            db.execute(text("INSERT INTO cobrancas (id_condominio,valor,status,data_vencimento,forma_pagamento,descricao,asaas_payment_id) VALUES (:c,:v,'pendente',:d,:f,:desc,:a)"),{"c":cid,"v":float(val),"d":venc,"f":bt.lower(),"desc":desc,"a":apid}); db.commit()
            return {"success":True,"message":"Cobrança criada!","asaas_payment_id":apid,"link_pagamento":lnk}
        except Exception as e:
            db.rollback(); logger.warning(f"Asaas falhou, fallback: {e}")
    try:
        db.execute(text("INSERT INTO cobrancas (id_condominio,valor,status,data_vencimento,forma_pagamento,descricao) VALUES (:c,:v,'pendente',:d,:f,:desc)"),{"c":cid,"v":float(val),"d":venc,"f":bt.lower(),"desc":desc}); db.commit()
        return {"success":True,"message":"Cobrança criada (sem link de pagamento)"}
    except Exception as e:
        db.rollback(); raise HTTPException(500,str(e))

@router.put("/condominios/{id_condominio}")
async def atualizar_condominio(id_condominio: int, payload: dict, db: Session = Depends(get_db)):
    ok={"plano_selecionado","valor_mensal_base","valor_plano_final","plano_desconto","total_apartamentos","sindico","telefone","email","cobranca_responsavel","cobranca_email","cobranca_whats","email_financeiro","ativo"}
    upd={k:v for k,v in payload.items() if k in ok}
    if not upd: return {"success":True,"message":"Nenhum campo"}
    upd["id"]=id_condominio
    try:
        res=db.execute(text(f"UPDATE condominios SET {', '.join(f'{k}=:{k}' for k in upd if k!='id')} WHERE id=:id"),upd); db.commit()
        if res.rowcount==0: raise HTTPException(404,"Não encontrado")
        return {"success":True,"message":"Atualizado!"}
    except HTTPException: raise
    except Exception as e: db.rollback(); raise HTTPException(500,str(e))

@router.post("/whatsapp/enviar")
async def enviar_whatsapp(payload: dict, db: Session = Depends(get_db)):
    id_c=payload.get("id_cobranca")
    if not id_c: raise HTTPException(422,"id_cobranca obrigatório")
    try:
        from financeiro_whatsapp_routes import enviar_cobranca_whatsapp
        return await enviar_cobranca_whatsapp(id_c,db)
    except: pass
    return {"success":False,"message":"WhatsApp não configurado"}

@router.post("/email/reenviar/{id_cobranca}")
async def reenviar_email_por_id(id_cobranca: str, db: Session = Depends(get_db)):
    try:
        from financeiro.financeiro_email_routes import reenviar_email_cobranca
        return await reenviar_email_cobranca(id_cobranca, db)
    except Exception as e:
        logger.warning(f"Erro reenviar email {id_cobranca}: {e}")
        # Fallback: chama via importação direta
        try:
            from financeiro.financeiro_email_routes import router as email_router
            return await reenviar_email_cobranca(id_cobranca, db)
        except Exception as e2:
            logger.error(f"Erro fallback email {id_cobranca}: {e2}")
    return {"success": False, "message": "Erro ao enviar email"}
@router.post("/email/reenviar")
async def reenviar_email(payload: dict, db: Session = Depends(get_db)):


    id_c=payload.get("id_cobranca")
    if not id_c: raise HTTPException(422,"id_cobranca obrigatório")
    try:
        from financeiro_email_routes import reenviar_email_cobranca
        return await reenviar_email_cobranca(str(id_c),db)
    except: pass
    return {"success":False,"message":"Email não configurado"}
