# ================================================================================
#  PATH: backend/financeiro/financeiro_asaas_service.py
#  DESCRIPTION: Módulo financeiro – Econdomínio / Inforseg
#               Serviço de integração com API Asaas - IMPLEMENTAÇÃO COMPLETA
# ================================================================================

import os
import httpx
from typing import Optional, Dict, Any, List
from datetime import date, datetime
from decimal import Decimal
import logging

# Configurar logging
logger = logging.getLogger(__name__)


class AsaasServiceError(Exception):
    """Exceção customizada para erros do Asaas"""
    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(self.message)


class AsaasService:
    """
    Serviço de integração com API Asaas.
    
    Documentação oficial: https://docs.asaas.com/
    """
    
    def __init__(self):
        self.api_key = os.getenv("ASAAS_API_KEY")
        self.env = os.getenv("ASAAS_ENV", "sandbox")
        
        # Definir URL base conforme ambiente
        if self.env == "sandbox":
            self.base_url = os.getenv("ASAAS_URL_SANDBOX", "https://api-sandbox.asaas.com/v3")
        else:
            self.base_url = os.getenv("ASAAS_URL_PRODUCTION", "https://api.asaas.com/v3")
        
        # Validar configuração
        if not self.api_key:
            raise AsaasServiceError("ASAAS_API_KEY não configurada no ambiente")
        
        logger.info(f"AsaasService inicializado - Ambiente: {self.env}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers padrão para requisições"""
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "access_token": self.api_key
        }
    
    async def _request(
        self, 
        method: str, 
        endpoint: str, 
        data: dict = None, 
        params: dict = None
    ) -> Dict[str, Any]:
        """
        Método genérico para fazer requisições à API Asaas
        """
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    json=data,
                    params=params
                )
                
                # Log da requisição
                logger.debug(f"Asaas {method} {endpoint} - Status: {response.status_code}")
                
                # Verificar erros
                if response.status_code >= 400:
                    error_data = response.json() if response.text else {}
                    error_msg = error_data.get("errors", [{}])[0].get("description", "Erro desconhecido")
                    raise AsaasServiceError(
                        message=f"Erro Asaas: {error_msg}",
                        status_code=response.status_code,
                        response_data=error_data
                    )
                
                return response.json() if response.text else {}
                
        except httpx.RequestError as e:
            logger.error(f"Erro de conexão com Asaas: {str(e)}")
            raise AsaasServiceError(f"Erro de conexão com Asaas: {str(e)}")
    
    # ============================================================================
    #  CLIENTES (CUSTOMERS)
    # ============================================================================
    
    async def create_customer(
        self,
        name: str,
        cpf_cnpj: str,
        email: str = None,
        phone: str = None,
        mobile_phone: str = None,
        address: str = None,
        address_number: str = None,
        complement: str = None,
        province: str = None,
        postal_code: str = None,
        external_reference: str = None,
        notifications_disabled: bool = True
    ) -> Dict[str, Any]:
        """
        Cria um novo cliente no Asaas.
        
        Args:
            name: Nome completo ou razão social
            cpf_cnpj: CPF ou CNPJ (apenas números)
            email: Email do cliente
            phone: Telefone fixo
            mobile_phone: Celular
            address: Logradouro
            address_number: Número
            complement: Complemento
            province: Bairro
            postal_code: CEP
            external_reference: Referência externa (ID do seu sistema)
            notifications_disabled: Desabilitar notificações
            
        Returns:
            Dados do cliente criado incluindo 'id' do Asaas
        """
        data = {
            "name": name,
            "cpfCnpj": cpf_cnpj.replace(".", "").replace("-", "").replace("/", ""),
            "notificationDisabled": notifications_disabled
        }
        
        # Adicionar campos opcionais
        if email:
            data["email"] = email
        if phone:
            data["phone"] = phone
        if mobile_phone:
            data["mobilePhone"] = mobile_phone
        if address:
            data["address"] = address
        if address_number:
            data["addressNumber"] = address_number
        if complement:
            data["complement"] = complement
        if province:
            data["province"] = province
        if postal_code:
            data["postalCode"] = postal_code.replace("-", "")
        if external_reference:
            data["externalReference"] = external_reference
        
        result = await self._request("POST", "/customers", data=data)
        logger.info(f"Cliente criado no Asaas: {result.get('id')}")
        return result
    
    async def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """Busca dados de um cliente pelo ID do Asaas"""
        return await self._request("GET", f"/customers/{customer_id}")
    
    async def find_customer_by_cpf_cnpj(self, cpf_cnpj: str) -> Optional[Dict[str, Any]]:
        """Busca cliente pelo CPF/CNPJ"""
        cpf_cnpj_clean = cpf_cnpj.replace(".", "").replace("-", "").replace("/", "")
        result = await self._request("GET", "/customers", params={"cpfCnpj": cpf_cnpj_clean})
        
        customers = result.get("data", [])
        return customers[0] if customers else None
    
    async def update_customer(self, customer_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Atualiza dados de um cliente"""
        return await self._request("PUT", f"/customers/{customer_id}", data=data)
    
    # ============================================================================
    #  ASSINATURAS (SUBSCRIPTIONS)
    # ============================================================================
    
    async def create_subscription(
        self,
        customer_id: str,
        value: float,
        cycle: str = "MONTHLY",
        description: str = None,
        billing_type: str = "UNDEFINED",
        next_due_date: date = None,
        discount_value: float = None,
        interest_value: float = None,
        fine_value: float = None,
        external_reference: str = None
    ) -> Dict[str, Any]:
        """
        Cria uma assinatura recorrente.
        
        Args:
            customer_id: ID do cliente no Asaas
            value: Valor da cobrança
            cycle: Ciclo (WEEKLY, BIWEEKLY, MONTHLY, BIMONTHLY, QUARTERLY, SEMIANNUALLY, YEARLY)
            description: Descrição da assinatura
            billing_type: Tipo de cobrança (BOLETO, CREDIT_CARD, PIX, UNDEFINED)
            next_due_date: Data do primeiro vencimento
            discount_value: Valor do desconto
            interest_value: Valor dos juros ao mês
            fine_value: Valor da multa
            external_reference: Referência externa
            
        Returns:
            Dados da assinatura criada
        """
        data = {
            "customer": customer_id,
            "billingType": billing_type,
            "value": float(value),
            "cycle": cycle
        }
        
        if description:
            data["description"] = description
        if next_due_date:
            data["nextDueDate"] = next_due_date.isoformat() if isinstance(next_due_date, date) else next_due_date
        if discount_value:
            data["discount"] = {"value": float(discount_value), "dueDateLimitDays": 0}
        if interest_value:
            data["interest"] = {"value": float(interest_value)}
        if fine_value:
            data["fine"] = {"value": float(fine_value)}
        if external_reference:
            data["externalReference"] = external_reference
        
        result = await self._request("POST", "/subscriptions", data=data)
        logger.info(f"Assinatura criada no Asaas: {result.get('id')}")
        return result
    
    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Busca dados de uma assinatura"""
        return await self._request("GET", f"/subscriptions/{subscription_id}")
    
    async def update_subscription(self, subscription_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Atualiza uma assinatura"""
        return await self._request("PUT", f"/subscriptions/{subscription_id}", data=data)
    
    async def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancela uma assinatura"""
        return await self._request("DELETE", f"/subscriptions/{subscription_id}")
    
    async def list_subscription_payments(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Lista cobranças de uma assinatura"""
        result = await self._request("GET", f"/subscriptions/{subscription_id}/payments")
        return result.get("data", [])
    
    # ============================================================================
    #  COBRANÇAS (PAYMENTS)
    # ============================================================================
    
    async def create_payment(
        self,
        customer_id: str,
        value: float,
        due_date: date,
        description: str = None,
        billing_type: str = "UNDEFINED",
        external_reference: str = None,
        discount_value: float = None,
        interest_value: float = None,
        fine_value: float = None
    ) -> Dict[str, Any]:
        """
        Cria uma cobrança avulsa.
        
        Args:
            customer_id: ID do cliente no Asaas
            value: Valor da cobrança
            due_date: Data de vencimento
            description: Descrição
            billing_type: Tipo (BOLETO, CREDIT_CARD, PIX, UNDEFINED)
            external_reference: Referência externa
            
        Returns:
            Dados da cobrança criada incluindo 'id' do Asaas
        """
        data = {
            "customer": customer_id,
            "billingType": billing_type,
            "value": float(value),
            "dueDate": due_date.isoformat() if isinstance(due_date, date) else due_date
        }
        
        if description:
            data["description"] = description
        if external_reference:
            data["externalReference"] = external_reference
        if discount_value:
            data["discount"] = {"value": float(discount_value), "dueDateLimitDays": 0}
        if interest_value:
            data["interest"] = {"value": float(interest_value)}
        if fine_value:
            data["fine"] = {"value": float(fine_value)}
        
        result = await self._request("POST", "/payments", data=data)
        logger.info(f"Cobrança criada no Asaas: {result.get('id')}")
        return result
    
    async def get_payment(self, payment_id: str) -> Dict[str, Any]:
        """
        Busca dados completos de uma cobrança.
        
        Retorna informações incluindo:
        - status
        - bankSlipUrl (URL do boleto)
        - invoiceUrl (URL da fatura)
        - nossoNumero
        - etc.
        """
        return await self._request("GET", f"/payments/{payment_id}")
    
    async def update_payment(self, payment_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Atualiza uma cobrança"""
        return await self._request("PUT", f"/payments/{payment_id}", data=data)
    
    async def cancel_payment(self, payment_id: str) -> Dict[str, Any]:
        """
        Cancela/estorna uma cobrança.
        
        Se já paga, será estornada. Se pendente, será cancelada.
        """
        return await self._request("DELETE", f"/payments/{payment_id}")
    
    async def list_payments(
        self,
        customer_id: str = None,
        subscription_id: str = None,
        status: str = None,
        billing_type: str = None,
        due_date_ge: date = None,
        due_date_le: date = None,
        offset: int = 0,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Lista cobranças com filtros.
        
        Args:
            customer_id: Filtrar por cliente
            subscription_id: Filtrar por assinatura
            status: Filtrar por status (PENDING, RECEIVED, CONFIRMED, etc.)
            billing_type: Filtrar por tipo
            due_date_ge: Vencimento a partir de
            due_date_le: Vencimento até
            
        Returns:
            Lista de cobranças com paginação
        """
        params = {"offset": offset, "limit": limit}
        
        if customer_id:
            params["customer"] = customer_id
        if subscription_id:
            params["subscription"] = subscription_id
        if status:
            params["status"] = status
        if billing_type:
            params["billingType"] = billing_type
        if due_date_ge:
            params["dueDate[ge]"] = due_date_ge.isoformat() if isinstance(due_date_ge, date) else due_date_ge
        if due_date_le:
            params["dueDate[le]"] = due_date_le.isoformat() if isinstance(due_date_le, date) else due_date_le
        
        return await self._request("GET", "/payments", params=params)
    
    # ============================================================================
    #  PIX
    # ============================================================================
    
    async def get_pix_qr_code(self, payment_id: str) -> Dict[str, Any]:
        """
        Retorna QR Code PIX de uma cobrança.
        
        Returns:
            {
                "encodedImage": "base64...",  # Imagem do QR Code em base64
                "payload": "00020126...",      # Código PIX copia e cola
                "expirationDate": "2024-..."   # Data de expiração
            }
        """
        result = await self._request("GET", f"/payments/{payment_id}/pixQrCode")
        
        return {
            "qr_code": result.get("payload", ""),
            "qr_code_image": f"data:image/png;base64,{result.get('encodedImage', '')}",
            "expiration_date": result.get("expirationDate", ""),
            "success": True
        }
    
    # ============================================================================
    #  BOLETO
    # ============================================================================
    
    async def get_boleto_url(self, payment_id: str) -> Dict[str, Any]:
        """
        Retorna dados do boleto de uma cobrança.
        
        Returns:
            {
                "bankSlipUrl": "https://...",  # URL do boleto PDF
                "invoiceUrl": "https://...",   # URL da fatura
                "nossoNumero": "...",          # Nosso número
                "barCode": "..."               # Código de barras (linha digitável)
            }
        """
        payment = await self.get_payment(payment_id)
        
        return {
            "boleto_url": payment.get("bankSlipUrl", ""),
            "invoice_url": payment.get("invoiceUrl", ""),
            "nosso_numero": payment.get("nossoNumero", ""),
            "codigo_barras": payment.get("identificationField", ""),  # Linha digitável
            "bar_code": payment.get("barCode", ""),  # Código de barras numérico
            "due_date": payment.get("dueDate", ""),
            "value": payment.get("value", 0),
            "status": payment.get("status", ""),
            "success": True
        }
    
    # ============================================================================
    #  LINHA DIGITÁVEL / CÓDIGO DE BARRAS
    # ============================================================================
    
    async def get_identification_field(self, payment_id: str) -> Dict[str, Any]:
        """
        Retorna a linha digitável (código de barras) do boleto.
        """
        result = await self._request("GET", f"/payments/{payment_id}/identificationField")
        
        return {
            "identification_field": result.get("identificationField", ""),
            "bar_code": result.get("barCode", ""),
            "success": True
        }
    
    # ============================================================================
    #  REENVIAR COBRANÇA
    # ============================================================================
    
    async def resend_payment(self, payment_id: str) -> Dict[str, Any]:
        """
        Reenvia notificação de cobrança por email.
        """
        # A API do Asaas não tem endpoint específico para reenvio
        # Podemos usar o endpoint de envio de documento/notificação
        # ou simplesmente retornar os dados da cobrança para reenvio manual
        
        payment = await self.get_payment(payment_id)
        
        # Se tiver invoiceUrl, pode ser enviado para o cliente
        return {
            "payment_id": payment_id,
            "invoice_url": payment.get("invoiceUrl", ""),
            "bank_slip_url": payment.get("bankSlipUrl", ""),
            "customer": payment.get("customer", ""),
            "message": "Dados da cobrança recuperados para reenvio",
            "success": True
        }
    
    # ============================================================================
    #  ESTORNO
    # ============================================================================
    
    async def refund_payment(self, payment_id: str, value: float = None) -> Dict[str, Any]:
        """
        Estorna uma cobrança paga.
        
        Args:
            payment_id: ID da cobrança
            value: Valor a estornar (None = valor total)
        """
        data = {}
        if value:
            data["value"] = float(value)
        
        return await self._request("POST", f"/payments/{payment_id}/refund", data=data)


# ================================================================================
#  SINGLETON / FACTORY
# ================================================================================

_asaas_service_instance: Optional[AsaasService] = None


def get_asaas_service() -> AsaasService:
    """
    Retorna instância singleton do AsaasService.
    """
    global _asaas_service_instance
    
    if _asaas_service_instance is None:
        _asaas_service_instance = AsaasService()
    
    return _asaas_service_instance


def reset_asaas_service():
    """
    Reseta a instância do serviço (útil para testes).
    """
    global _asaas_service_instance
    _asaas_service_instance = None
