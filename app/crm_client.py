"""
crm_client.py

Cliente HTTP para a API de Cards do CRM (Supabase Edge Functions).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class CRMError(Exception):
    pass


def format_phone(chat_id: str) -> str:
    """
    Extrai o número de telefone do chat_id da Whapi.

    O chat_id vem no formato '5511999999999@s.whatsapp.net' ou similar.
    Retorna apenas os dígitos antes do '@'.

    Ex: '5511999999999@s.whatsapp.net' → '5511999999999'
        '5511999999999'               → '5511999999999'
    """
    return str(chat_id).split("@")[0]


class CRMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        pipeline_id: str,
        stage_id: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._pipeline_id = pipeline_id
        self._stage_id = stage_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    # ── GET card ─────────────────────────────────────────────────────────────

    def card_exists(self, phone: str) -> bool:
        """
        Verifica se já existe um card para o telefone informado.

        Chama GET api-cards-get?pipeline_id=...&phone=<phone_formatado>.

        Retorna:
            False → card não encontrado (pode criar)
            True  → card já existe (não criar)

        Lança CRMError em caso de falha de comunicação.
        """
        numero = format_phone(phone)
        url = (
            f"{self._base_url}/api-cards-get"
            f"?pipeline_id={self._pipeline_id}&phone={numero}"
        )
        try:
            resp = self._client.get(url, headers=self._headers)

            # 404 ou resposta com "not found" → card não existe
            if resp.status_code == 404:
                logger.info("[CRM] Card não encontrado para %s (404).", numero)
                return False

            resp.raise_for_status()
            data = resp.json()

            # A API retorna {"error": "..."} ou {"message": "Card não encontrado"}
            if isinstance(data, dict):
                # Checa tanto "error" quanto "message"
                msg_text = str(data.get("error", "") or data.get("message", "")).lower()
                if any(kw in msg_text for kw in ("not found", "não encontrado", "nao encontrado", "card não encontrado")):
                    logger.info("[CRM] Card não encontrado para %s", numero)
                    return False

            logger.info("[CRM] Card já existe para %s.", numero)
            return True

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            body = e.response.text[:300]
            logger.error("[CRM] HTTP %s ao verificar card para %s: %s", e.response.status_code, numero, body)
            raise CRMError(f"HTTP {e.response.status_code}: {body}") from e
        except httpx.RequestError as e:
            logger.error("[CRM] Erro de conexão ao verificar card para %s: %s", numero, e)
            raise CRMError(f"Erro de conexão: {e}") from e

    # ── CREATE card ───────────────────────────────────────────────────────────

    def create_card(
        self,
        title: str,
        phone: str,
        from_name: str,
        extra_fields: dict | None = None,
    ) -> str:
        """
        Cria um card no CRM e retorna o card_id.

        Deve ser chamado SOMENTE após confirmar que card_exists() retorna False.
        O phone passado aqui deve ser o número formatado (sem @...).
        """
        numero = format_phone(phone)
        now = datetime.now(timezone.utc).isoformat()

        fields = {
            "Nome do Lead": from_name,
            "Telefone": numero,
            "WhatsApp": numero,
            "Fonte": "Chatbot",
            "Data de Entrada": now,
            "Cnpj": "",
            "E-mail": "",
            "Empresa": "",
            "Serviço desejado": "",
            "Carga horária": "",
        }
        if extra_fields:
            # Separa description (campo nativo) dos campos customizados
            description = extra_fields.pop("Descrição", None)
            fields.update(extra_fields)
        else:
            description = None

        payload = {
            "pipeline_id": self._pipeline_id,
            "stage_id": self._stage_id,
            "title": title,
            "labels": ["Chatbot"],
            "fields": fields,
        }
        if description is not None:
            payload["description"] = description

        try:
            resp = self._client.post(
                f"{self._base_url}/api-cards-create",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            card_id = self._extract_card_id(data)
            logger.info("[CRM] Card criado: %s (phone=%s)", card_id, numero)
            return card_id
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            logger.error("[CRM] HTTP %s ao criar card para %s: %s", e.response.status_code, numero, body)
            raise CRMError(f"HTTP {e.response.status_code}: {body}") from e
        except httpx.RequestError as e:
            logger.error("[CRM] Erro de conexão ao criar card para %s: %s", numero, e)
            raise CRMError(f"Erro de conexão: {e}") from e

    # ── UPDATE card ───────────────────────────────────────────────────────────

    def update_card(self, card_id: str, fields: dict) -> None:
        """Atualiza campos de um card existente.

        O campo especial 'Descrição' é mapeado para o campo nativo `description`
        da API (enviado fora do objeto `fields`).
        """
        # Separa description (campo nativo) dos campos customizados
        description = fields.pop("Descrição", None)
        payload: dict = {"card_id": card_id, "fields": fields}
        if description is not None:
            payload["description"] = description
        try:
            resp = self._client.post(
                f"{self._base_url}/api-cards-update",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            logger.info("[CRM] Card %s atualizado.", card_id)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            logger.error("[CRM] HTTP %s ao atualizar card %s: %s", e.response.status_code, card_id, body)
            raise CRMError(f"HTTP {e.response.status_code}: {body}") from e
        except httpx.RequestError as e:
            logger.error("[CRM] Erro de conexão ao atualizar card %s: %s", card_id, e)
            raise CRMError(f"Erro de conexão: {e}") from e

    # ── MOVE card ─────────────────────────────────────────────────────────────

    def move_card(self, card_id: str, stage_id: str) -> None:
        """Move um card para outro estágio do pipeline."""
        try:
            resp = self._client.post(
                f"{self._base_url}/api-cards-move",
                json={"card_id": card_id, "stage_id": stage_id},
                headers=self._headers,
            )
            resp.raise_for_status()
            logger.info("[CRM] Card %s movido para stage %s.", card_id, stage_id)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            logger.error("[CRM] HTTP %s ao mover card %s: %s", e.response.status_code, card_id, body)
            raise CRMError(f"HTTP {e.response.status_code}: {body}") from e
        except httpx.RequestError as e:
            logger.error("[CRM] Erro de conexão ao mover card %s: %s", card_id, e)
            raise CRMError(f"Erro de conexão: {e}") from e

    def get_card_stage(self, card_id: str) -> str | None:
        """
        Consulta o stage_id atual do card.

        Retorna:
          str  → stage_id encontrado
          None → card não encontrado, erro de comunicação ou stage ausente na resposta.
                 Neste caso o bot falha aberto (permite resposta) para não travar o fluxo.

        Erros são sempre logados como WARNING ou ERROR para facilitar diagnóstico.
        """
        url = f"{self._base_url}/api-cards-get?card_id={card_id}"
        try:
            resp = self._client.get(url, headers=self._headers)

            if resp.status_code == 404:
                logger.warning("[CRM] Card %s não encontrado ao consultar stage (404)", card_id)
                return None

            if resp.status_code == 401:
                logger.error(
                    "[CRM] Falha de autenticação ao consultar stage do card %s — "
                    "verifique SUPABASE_API_KEY", card_id
                )
                return None

            resp.raise_for_status()
            data = resp.json()

            # Tenta extrair stage_id de diferentes formatos de resposta
            if isinstance(data, dict):
                # Formato direto: {"stage_id": "..."}
                if "stage_id" in data:
                    stage = str(data["stage_id"])
                    logger.debug("[CRM] Card %s → stage %s", card_id, stage)
                    return stage
                # Formato aninhado: {"card": {"stage_id": "..."}} ou {"cards": [...]}
                card = data.get("card") or data.get("cards", [{}])
                if isinstance(card, list):
                    card = card[0] if card else {}
                if isinstance(card, dict) and "stage_id" in card:
                    stage = str(card["stage_id"])
                    logger.debug("[CRM] Card %s → stage %s (aninhado)", card_id, stage)
                    return stage

            # Stage ausente na resposta — log para diagnóstico
            logger.error(
                "[CRM] stage_id ausente na resposta para card %s. "
                "Resposta: %s — verifique o formato da API do CRM.",
                card_id, str(data)[:300],
            )
            return None

        except httpx.HTTPStatusError as e:
            logger.error(
                "[CRM] HTTP %s ao consultar stage do card %s: %s",
                e.response.status_code, card_id, e.response.text[:200],
            )
            return None
        except httpx.RequestError as e:
            logger.error("[CRM] Timeout/conexão ao consultar stage do card %s: %s", card_id, e)
            return None
        except Exception as e:
            logger.exception("[CRM] Erro inesperado ao consultar stage do card %s", card_id)
            return None

    # ── Interno ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_card_id(data: dict) -> str:
        """Extrai card_id da resposta da API.

        Formatos suportados:
          {"id": "..."}                          → direto
          {"card": {"id": "..."}}                → aninhado em "card"
          {"cards": [{"id": "..."}]}             → lista em "cards"
          {"success": true, "card": {"id": ...}} → wrapper de sucesso
        """
        # Formato direto
        if "id" in data:
            return str(data["id"])
        # Formato {"card": {"id": ...}} ou {"success": true, "card": {...}}
        card = data.get("card")
        if isinstance(card, dict) and "id" in card:
            return str(card["id"])
        # Formato {"cards": [...]}
        cards = data.get("cards", [])
        if cards and isinstance(cards[0], dict) and "id" in cards[0]:
            return str(cards[0]["id"])
        logger.warning("[CRM] Não foi possível extrair card_id da resposta: %s", str(data)[:200])
        return ""
