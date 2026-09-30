# ================================================================================
#  PATH: backend/financeiro/supertokens_config.py
#  DESCRIPTION: SuperTokens - Signup desabilitado, Rate Limiting ativado
# ================================================================================

from supertokens_python import init, InputAppInfo, SupertokensConfig
from supertokens_python.recipe import emailpassword, session, dashboard
from supertokens_python.recipe.emailpassword import InputFormField
from supertokens_python.recipe.emailpassword.interfaces import APIInterface, APIOptions, SignUpPostOkResult
from supertokens_python.recipe.emailpassword.types import FormField
from typing import List, Dict, Union, Any
import os
import time
from collections import defaultdict
import threading

# ============================================================================
# Rate Limiting - Controle de tentativas de login
# ============================================================================
class RateLimiter:
    def __init__(self, max_attempts: int = 5, block_duration: int = 300):
        self.max_attempts = max_attempts  # 5 tentativas
        self.block_duration = block_duration  # 300 segundos = 5 minutos
        self.attempts: Dict[str, List[float]] = defaultdict(list)
        self.blocked: Dict[str, float] = {}
        self.lock = threading.Lock()
    
    def is_blocked(self, ip: str) -> bool:
        with self.lock:
            if ip in self.blocked:
                if time.time() < self.blocked[ip]:
                    return True
                else:
                    del self.blocked[ip]
                    self.attempts[ip] = []
            return False
    
    def get_remaining_time(self, ip: str) -> int:
        with self.lock:
            if ip in self.blocked:
                remaining = int(self.blocked[ip] - time.time())
                return max(0, remaining)
            return 0
    
    def record_attempt(self, ip: str, success: bool) -> bool:
        with self.lock:
            now = time.time()
            
            if success:
                self.attempts[ip] = []
                if ip in self.blocked:
                    del self.blocked[ip]
                return True
            
            # Limpar tentativas antigas (últimos 5 minutos)
            self.attempts[ip] = [t for t in self.attempts[ip] if now - t < self.block_duration]
            self.attempts[ip].append(now)
            
            if len(self.attempts[ip]) >= self.max_attempts:
                self.blocked[ip] = now + self.block_duration
                print(f"🚫 IP {ip} bloqueado por {self.block_duration}s após {self.max_attempts} tentativas")
                return False
            
            return True

# Instância global do rate limiter
rate_limiter = RateLimiter(max_attempts=5, block_duration=300)

# ============================================================================
# Configurações
# ============================================================================
SUPERTOKENS_CONNECTION_URI = os.getenv("SUPERTOKENS_CONNECTION_URI", "")
SUPERTOKENS_API_KEY = os.getenv("SUPERTOKENS_API_KEY", None)
APP_NAME = os.getenv("APP_NAME", "Econdomínio Financeiro")
API_DOMAIN = os.getenv("API_DOMAIN", "http://localhost:8000")
WEBSITE_DOMAIN = os.getenv("WEBSITE_DOMAIN", "http://localhost:3006")

# ============================================================================
# Override das APIs - Desabilitar Signup
# ============================================================================
def override_emailpassword_apis(original_implementation: APIInterface) -> APIInterface:
    
    # Desabilitar signup via API
    async def sign_up_post(form_fields: List[FormField], tenant_id: str, 
                           session: Any, should_try_linking_with_session_user: bool,
                           api_options: APIOptions, user_context: Dict[str, Any]):
        # Retornar erro - signup desabilitado
        from supertokens_python.recipe.emailpassword.interfaces import SignUpPostNotAllowedResponse
        return SignUpPostNotAllowedResponse("Registro de novos usuários desabilitado. Contate o administrador.")
    
    # Override do signin com rate limiting
    original_sign_in = original_implementation.sign_in_post
    
    async def sign_in_post(form_fields: List[FormField], tenant_id: str,
                           session: Any, should_try_linking_with_session_user: bool,
                           api_options: APIOptions, user_context: Dict[str, Any]):
        
        # Obter IP do cliente
        request = api_options.request
        ip = "unknown"
        
        # Tentar obter IP real (headers de proxy)
        forwarded_for = request.get_header("x-forwarded-for")
        real_ip = request.get_header("x-real-ip")
        
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
        elif real_ip:
            ip = real_ip
        else:
            ip = getattr(request, 'client', {})
            if hasattr(ip, 'host'):
                ip = ip.host
            else:
                ip = str(ip) if ip else "unknown"
        
        # Verificar se IP está bloqueado
        if rate_limiter.is_blocked(ip):
            remaining = rate_limiter.get_remaining_time(ip)
            from supertokens_python.recipe.emailpassword.interfaces import SignInPostWrongCredentialsError
            print(f"🚫 Tentativa de login bloqueada para IP {ip}. Tempo restante: {remaining}s")
            return SignInPostWrongCredentialsError()
        
        # Executar login original
        result = await original_sign_in(form_fields, tenant_id, session, 
                                         should_try_linking_with_session_user,
                                         api_options, user_context)
        
        # Registrar tentativa
        from supertokens_python.recipe.emailpassword.interfaces import SignInPostOkResult
        success = isinstance(result, SignInPostOkResult)
        rate_limiter.record_attempt(ip, success)
        
        if not success:
            attempts_left = rate_limiter.max_attempts - len(rate_limiter.attempts.get(ip, []))
            print(f"⚠️ Login falhou para IP {ip}. Tentativas restantes: {attempts_left}")
        
        return result
    
    original_implementation.sign_up_post = sign_up_post
    original_implementation.sign_in_post = sign_in_post
    
    return original_implementation

# ============================================================================
# Inicialização
# ============================================================================
def init_supertokens():
    print(f"📦 SuperTokens Config:")
    print(f"   API Domain: {API_DOMAIN}")
    print(f"   Website Domain: {WEBSITE_DOMAIN}")
    print(f"   Signup: DESABILITADO (apenas via terminal)")
    print(f"   Rate Limit: 5 tentativas / bloqueio 5 minutos")
    
    init(
        app_info=InputAppInfo(
            app_name=APP_NAME,
            api_domain=API_DOMAIN,
            website_domain=WEBSITE_DOMAIN,
            api_base_path="/auth",
            website_base_path="/financeiro/auth"
        ),
        supertokens_config=SupertokensConfig(
            connection_uri=SUPERTOKENS_CONNECTION_URI,
            api_key=SUPERTOKENS_API_KEY if SUPERTOKENS_API_KEY else None
        ),
        framework='fastapi',
        recipe_list=[
            emailpassword.init(
                sign_up_feature=emailpassword.InputSignUpFeature(
                    form_fields=[
                        InputFormField(id="email"),
                        InputFormField(id="password"),
                        InputFormField(id="name", optional=True),
                    ]
                ),
                override=emailpassword.InputOverrideConfig(
                    apis=override_emailpassword_apis
                )
            ),
            session.init(),
            dashboard.init(),
        ],
        mode='asgi'
    )

# Exportar rate_limiter para uso em outras partes
def get_rate_limiter():
    return rate_limiter
