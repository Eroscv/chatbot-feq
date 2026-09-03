"""
webhook_parser.py

Normaliza o payload recebido da Whapi para uma estrutura simples e consistente.
Apenas mensagens de entrada reais (não enviadas pelo próprio bot) são retornadas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class InboundMessage:
    """Mensagem normalizada recebida via webhook da Whapi."""

    message_id: str          # ID único da mensagem
    phone: str               # Número do remetente (ex: 5511999999999)
    from_name: str           # Nome do contato
    chat_id: str             # ID da conversa
    timestamp: int           # Unix timestamp
    kind: str                # "text" | "button" | "list" | "other"
    text: str | None         # Texto livre (kind=text)
    selected_id: str | None  # ID do botão/item selecionado (kind=button ou list)
    selected_title: str | None  # Título do botão/item selecionado
    raw: dict = field(repr=False, default_factory=dict)  # Payload bruto


def parse_whapi_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """
    Extrai mensagens relevantes do payload da Whapi.

    Ignora:
    - Mensagens enviadas pelo próprio canal (fromMe=true)
    - Mensagens sem ID ou sem remetente
    - Eventos que não são mensagens

    Retorna lista de InboundMessage (geralmente 1 por webhook).
    """
    messages = payload.get("messages", [])
    result: list[InboundMessage] = []

    for msg in messages:
        # Ignora mensagens enviadas pelo próprio bot
        if msg.get("from_me") or msg.get("fromMe"):
            continue

        message_id = msg.get("id", "")
        phone = msg.get("from", "")
        chat_id = msg.get("chat_id", phone)
        timestamp = msg.get("timestamp", 0)

        if not message_id or not phone:
            logger.debug("Mensagem ignorada: sem id ou from. Payload: %s", msg)
            continue

        # Nome do contato — Whapi pode entregar em _vname, pushname ou from_name
        from_name = (
            msg.get("_vname")
            or msg.get("pushname")
            or msg.get("from_name")
            or msg.get("contact", {}).get("name", "")
            or phone
        )

        # Detecta tipo e conteúdo
        kind, text, selected_id, selected_title = _extract_content(msg)

        result.append(InboundMessage(
            message_id=message_id,
            phone=phone,
            from_name=from_name,
            chat_id=chat_id,
            timestamp=timestamp,
            kind=kind,
            text=text,
            selected_id=selected_id,
            selected_title=selected_title,
            raw=msg,
        ))

    return result


def _extract_content(
    msg: dict,
) -> tuple[str, str | None, str | None, str | None]:
    """Retorna (kind, text, selected_id, selected_title)."""

    # Resposta a botão (quick_reply)
    if "button_reply" in msg or (
        msg.get("type") == "interactive"
        and msg.get("interactive", {}).get("type") == "button_reply"
    ):
        reply = msg.get("button_reply") or msg.get("interactive", {}).get("button_reply", {})
        return "button", None, _normalize_btn_id(reply.get("id")), reply.get("title")

    # Resposta a botão — formato Whapi type=reply com reply.buttons_reply
    # Estrutura: {"type": "reply", "reply": {"type": "buttons_reply", "buttons_reply": {"id": "ButtonsV3:id1", "title": "..."}}}
    if msg.get("type") == "reply" and isinstance(msg.get("reply"), dict):
        r = msg["reply"]
        if r.get("type") == "buttons_reply" and isinstance(r.get("buttons_reply"), dict):
            br = r["buttons_reply"]
            return "button", None, _normalize_btn_id(br.get("id")), br.get("title")

    # Resposta a botão — formato Whapi type=button (quick reply via template/interactive)
    # Estrutura: {"type": "button", "button": {"payload": "id1", "text": "Título"}}
    if msg.get("type") == "button" and isinstance(msg.get("button"), dict):
        btn = msg["button"]
        btn_id = btn.get("payload") or btn.get("id", "")
        btn_title = btn.get("text") or btn.get("title", "")
        if btn_id:
            return "button", None, _normalize_btn_id(str(btn_id)), str(btn_title) if btn_title else None

    # Resposta a lista
    if "list_reply" in msg or (
        msg.get("type") == "interactive"
        and msg.get("interactive", {}).get("type") == "list_reply"
    ):
        reply = msg.get("list_reply") or msg.get("interactive", {}).get("list_reply", {})
        return "list", None, reply.get("id"), reply.get("title")

    # Texto simples
    text_body = (
        msg.get("text", {}).get("body")
        or msg.get("body")
        or msg.get("text")
    )
    if isinstance(text_body, str) and text_body.strip():
        return "text", text_body.strip(), None, None

    return "other", None, None, None


def _normalize_btn_id(raw_id: str | None) -> str | None:
    """
    Normaliza o ID do botão removendo prefixos adicionados pela Whapi.

    A Whapi pode prefixar IDs com 'ButtonsV3:', 'ButtonV2:' etc.
    Ex: 'ButtonsV3:id1' → 'id1'
        'id1'            → 'id1'
    """
    if not raw_id:
        return raw_id
    # Remove qualquer prefixo antes do ':'
    if ":" in raw_id:
        return raw_id.split(":", 1)[1]
    return raw_id

    # Texto simples
    text_body = (
        msg.get("text", {}).get("body")
        or msg.get("body")
        or msg.get("text")
    )
    if isinstance(text_body, str) and text_body.strip():
        return "text", text_body.strip(), None, None

    return "other", None, None, None
