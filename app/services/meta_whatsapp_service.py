import logging
import os
import re
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    try:
        from app.config import settings  # type: ignore
        val = getattr(settings, key, None)
        if val is not None and str(val).strip():
            return str(val)
    except Exception:
        pass
    return os.environ.get(key, default)


class MetaWhatsAppService:
    def __init__(self) -> None:
        self.enabled = str(_env("WHATSAPP_META_ENABLED", "true")).lower() in ("true", "1", "yes")
        self.graph_version = _env("WHATSAPP_GRAPH_VERSION", "v25.0")
        self.phone_number_id = _env("WHATSAPP_PHONE_NUMBER_ID", "")
        self.access_token = _env("WHATSAPP_ACCESS_TOKEN", "")
        self.timeout = int(_env("WHATSAPP_REQUEST_TIMEOUT", "30"))

    @staticmethod
    def normalize_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone or "")
        if digits.startswith("55") and len(digits) in (12, 13):
            return digits
        if len(digits) in (10, 11):
            return "55" + digits
        return digits

    def _validate(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Meta desabilitada"
        if not self.phone_number_id:
            return False, "WHATSAPP_PHONE_NUMBER_ID não configurado"
        if not self.access_token:
            return False, "WHATSAPP_ACCESS_TOKEN não configurado"
        return True, "OK"

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ok, msg = self._validate()
        if not ok:
            return {
                "success": False,
                "provider": "meta",
                "status_code": None,
                "response_json": None,
                "message_id": None,
                "error": msg,
            }

        url = f"https://graph.facebook.com/{self.graph_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            try:
                response_json = response.json()
            except Exception:
                response_json = {"raw_text": response.text}

            if response.ok:
                message_id = None
                messages = response_json.get("messages") or []
                if messages:
                    message_id = messages[0].get("id")

                logger.info("Meta envio OK | message_id=%s", message_id)
                return {
                    "success": True,
                    "provider": "meta",
                    "status_code": response.status_code,
                    "response_json": response_json,
                    "message_id": message_id,
                    "error": None,
                }

            logger.error("Meta falhou | status=%s | body=%s", response.status_code, response_json)
            return {
                "success": False,
                "provider": "meta",
                "status_code": response.status_code,
                "response_json": response_json,
                "message_id": None,
                "error": response_json,
            }

        except requests.RequestException as exc:
            logger.exception("Erro HTTP Meta")
            return {
                "success": False,
                "provider": "meta",
                "status_code": None,
                "response_json": None,
                "message_id": None,
                "error": str(exc),
            }

    def send_text_message(self, phone: str, text: str) -> Dict[str, Any]:
        phone = self.normalize_phone(phone)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": text},
        }
        return self._post(payload)

    def send_template_message(
        self,
        phone: str,
        template_name: str,
        language_code: str = "pt_BR",
        body_params: Optional[list[str]] = None,
        button_params: Optional[list[str]] = None,  # ✅ NOVO: parâmetros para botões URL dinâmicos
    ) -> Dict[str, Any]:
        phone = self.normalize_phone(phone)

        template: Dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }

        # ✅ Monta components: body + botões URL dinâmicos
        components = []

        if body_params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            })

        if button_params:
            for idx, val in enumerate(button_params):
                components.append({
                    "type": "button",
                    "sub_type": "url",
                    "index": str(idx),
                    "parameters": [{"type": "text", "text": val}],
                })

        if components:
            template["components"] = components

        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": template,
        }
        return self._post(payload)


meta_whatsapp_service = MetaWhatsAppService()
