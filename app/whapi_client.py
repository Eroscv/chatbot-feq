"""
whapi_client.py

Cliente HTTP para a API da Whapi.
Responsável por enviar mensagens (texto, botões, lista) via WhatsApp.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.database import Database

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_RETRY_ATTEMPTS = 3        # tentativas totais
_RETRY_BACKOFF  = [0, 2, 5]  # segundos de espera antes de cada tentativa


class WhapiError(Exception):
    pass


class WhapiClient:
    def __init__(self, token: str, base_url: str = "https://gate.whapi.cloud") -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=_TIMEOUT)
        self._db: "Database | None" = None  # injetado via set_database()

    def set_database(self, db: "Database") -> None:
        """Injeta o banco para logging automático de mensagens enviadas."""
        self._db = db

    def close(self) -> None:
        self._client.close()

    # ── Mensagens ────────────────────────────────────────────────────────────

    def send_text(self, to: str, body: str) -> dict:
        """Envia mensagem de texto simples."""
        result = self._post("/messages/text", {"to": to, "body": body})
        self._log_sent(to, "text", body, message_id=result.get("message", {}).get("id"))
        return result

    def get_last_message(self, chat_id: str) -> dict | None:
        """
        Retorna a última mensagem do chat ou None em caso de erro.

        Campos relevantes:
          from_me (bool) — True se foi enviada pelo bot/atendente via API ou app
          source  (str)  — "api" | "web" | "ios" | "android" | ...
        
        Lógica de handoff automático:
          - from_me=True  + source="api"              → última msg foi do bot      → responde
          - from_me=False                             → última msg foi do lead     → responde
          - from_me=True  + source != "api"           → humano enviou pelo app     → silencia
        """
        url = f"{self._base_url}/messages/list/{chat_id}?count=1"
        try:
            resp = self._client.get(url, headers={"Authorization": f"Bearer {self._token}"})
            resp.raise_for_status()
            msgs = resp.json().get("messages", [])
            return msgs[0] if msgs else None
        except Exception as e:
            logger.warning("[Whapi] Falha ao buscar última msg do chat %s: %s", chat_id, e)
            return None

    def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict],
        header: str | None = None,
        footer: str | None = None,
    ) -> dict:
        """
        Envia mensagem interativa com botões (até 3).

        buttons: lista de dicts com 'id' e 'title'.
        Ex: [{"id": "id1", "title": "Sim"}, {"id": "id2", "title": "Não"}]
        """
        payload: dict[str, Any] = {
            "type": "button",
            "to": to,
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "quick_reply", "id": b["id"], "title": b["title"]}
                    for b in buttons
                ]
            },
        }
        if header:
            payload["header"] = {"text": header}
        if footer:
            payload["footer"] = {"text": footer}

        result = self._post("/messages/interactive", payload)
        label = " | ".join(b["title"] for b in buttons)
        self._log_sent(to, "interactive", f"[buttons] {body} | [{label}]",
                       message_id=result.get("message", {}).get("id"))
        return result

    def send_list(
        self,
        to: str,
        body: str,
        button_label: str,
        rows: list[dict],
        header: str | None = None,
        footer: str | None = None,
        section_title: str = "Opções",
    ) -> dict:
        """
        Envia mensagem interativa com lista (mais de 3 opções).

        rows: lista de dicts com 'id' e 'title' (e opcionalmente 'description').
        """
        payload: dict[str, Any] = {
            "type": "list",
            "to": to,
            "body": {"text": body},
            "action": {
                "button": button_label,
                "sections": [
                    {
                        "title": section_title,
                        "rows": [
                            {
                                "id": r["id"],
                                "title": r["title"],
                                **({"description": r["description"]} if r.get("description") else {}),
                            }
                            for r in rows
                        ],
                    }
                ],
            },
        }
        if header:
            payload["header"] = {"text": header}
        if footer:
            payload["footer"] = {"text": footer}

        result = self._post("/messages/interactive", payload)
        row_labels = " | ".join(r["title"] for r in rows)
        self._log_sent(to, "list", f"[list] {body} | [{row_labels}]",
                       message_id=result.get("message", {}).get("id"))
        return result

    # ── Interno ──────────────────────────────────────────────────────────────

    def _log_sent(self, to: str, kind: str, content: str, message_id: str | None = None) -> None:
        """Registra mensagem enviada pelo bot no banco de dados."""
        if self._db is None:
            return
        try:
            numero = to.split("@")[0] if "@" in to else to
            self._db.log_message(
                numero=numero,
                direction="out",
                kind=kind,
                content=content,
                message_id=message_id,
                from_name="Bot",
            )
        except Exception:
            logger.warning("[Whapi] Falha ao logar mensagem enviada para %s", to)

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        for attempt, wait in enumerate(_RETRY_BACKOFF):
            if wait:
                logger.warning("[Whapi] Tentativa %d/%d em %s (aguardando %ds)",
                               attempt + 1, _RETRY_ATTEMPTS, path, wait)
                time.sleep(wait)
            try:
                resp = self._client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
                if attempt > 0:
                    logger.info("[Whapi] POST %s OK na tentativa %d", path, attempt + 1)
                else:
                    logger.debug("[Whapi] POST %s → %s", path, resp.status_code)
                return resp.json()
            except httpx.HTTPStatusError as e:
                body = e.response.text[:300]
                # Não faz retry em erros 4xx (problema no payload, não no servidor)
                if 400 <= e.response.status_code < 500:
                    logger.error("[Whapi] HTTP %s em %s: %s", e.response.status_code, path, body)
                    raise WhapiError(f"HTTP {e.response.status_code}: {body}") from e
                logger.warning("[Whapi] HTTP %s em %s (tentativa %d/%d): %s",
                               e.response.status_code, path, attempt + 1, _RETRY_ATTEMPTS, body)
                last_exc = WhapiError(f"HTTP {e.response.status_code}: {body}")
            except httpx.RequestError as e:
                logger.warning("[Whapi] Erro de conexão em %s (tentativa %d/%d): %s",
                               path, attempt + 1, _RETRY_ATTEMPTS, e)
                last_exc = WhapiError(f"Erro de conexão: {e}")
        logger.error("[Whapi] Todas as %d tentativas falharam em %s", _RETRY_ATTEMPTS, path)
        raise last_exc or WhapiError(f"Falha persistente em {path}")
