"""
message_buffer.py

Buffer de mensagens por número de telefone.

Problema que resolve:
  Usuários frequentemente enviam várias mensagens rápidas antes do bot processar
  a primeira. Sem buffer, o bot responderia N vezes a N mensagens simultâneas,
  causando respostas duplicadas e estados corrompidos.

Solução:
  Ao receber uma mensagem, ela entra numa fila por número.
  Um worker por número processa a fila com um delay configurable (FLUSH_DELAY_SEC).
  Se novas mensagens chegarem durante o delay, são acumuladas e enviadas juntas
  como uma única mensagem concatenada.

Fluxo:
  webhook recebe msg → MessageBuffer.push(numero, msg)
                     → timer FLUSH_DELAY_SEC
                     → flush: concatena textos / pega último botão
                     → chama handle_incoming_message(...)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# Tempo de espera (segundos) antes de processar a fila
# Mensagens que chegarem dentro desse janela são agrupadas
FLUSH_DELAY_SEC: float = 2.0


@dataclass
class BufferedMessage:
    """Representa uma mensagem aguardando processamento."""
    numero: str
    phone: str
    from_name: str
    chat_id: str
    message_id: str
    timestamp: int
    kind: str
    text: str | None
    selected_id: str | None
    selected_title: str | None
    raw: dict = field(default_factory=dict)


# Tipo do handler que o buffer vai chamar
HandlerFunc = Callable[..., None]


class MessageBuffer:
    """
    Buffer assíncrono de mensagens por número.

    Agrupa mensagens do mesmo número que chegam dentro de FLUSH_DELAY_SEC,
    entregando ao handler apenas uma mensagem consolidada.
    """

    def __init__(self, handler: HandlerFunc, whapi, crm) -> None:
        self._handler = handler
        self._whapi = whapi
        self._crm = crm
        self._queues: dict[str, list[BufferedMessage]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_dependencies(self, whapi, crm) -> None:
        self._whapi = whapi
        self._crm = crm

    # ── API pública ───────────────────────────────────────────────────────────

    def push(self, msg: BufferedMessage) -> None:
        """
        Adiciona mensagem ao buffer do número.
        Reinicia o timer de flush se já havia uma mensagem aguardando.
        """
        numero = msg.numero
        if numero not in self._queues:
            self._queues[numero] = []

        self._queues[numero].append(msg)
        logger.debug("[Buffer] Msg enfileirada para %s (fila=%d)", numero, len(self._queues[numero]))

        # Cancela timer anterior e agenda novo
        self._cancel_timer(numero)
        self._schedule_flush(numero)

    # ── Internos ──────────────────────────────────────────────────────────────

    def _cancel_timer(self, numero: str) -> None:
        handle = self._timers.pop(numero, None)
        if handle:
            handle.cancel()

    def _schedule_flush(self, numero: str) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Sem event loop — processa imediatamente (modo sync/teste)
            self._flush(numero)
            return

        handle = loop.call_later(FLUSH_DELAY_SEC, self._flush, numero)
        self._timers[numero] = handle

    def _flush(self, numero: str) -> None:
        """Processa todas as mensagens acumuladas para o número."""
        msgs = self._queues.pop(numero, [])
        self._timers.pop(numero, None)

        if not msgs:
            return

        logger.info("[Buffer] Flush %s — %d mensagem(ns) acumulada(s)", numero, len(msgs))

        # Consolida em uma única mensagem para o handler
        consolidated = self._consolidate(msgs)

        try:
            self._handler(self._whapi, self._crm, consolidated)
        except Exception:
            logger.exception("[Buffer] Erro ao processar mensagens de %s", numero)

    def _consolidate(self, msgs: list[BufferedMessage]) -> "ConsolidatedMessage":
        """
        Consolida lista de mensagens em uma única para o handler.

        Regras:
        - Se há botão → usa o botão (última interação intencional)
        - Se há só textos → concatena com espaço
        - Usa os metadados (nome, phone) da primeira mensagem
        """
        # Prioriza botão — se o usuário clicou num botão, é a ação principal
        button_msgs = [m for m in msgs if m.kind == "button"]
        if button_msgs:
            last_button = button_msgs[-1]
            logger.debug("[Buffer] Consolidado como botão: %s", last_button.selected_id)
            return ConsolidatedMessage.from_buffered(last_button)

        # Concatena textos
        text_parts = [m.text for m in msgs if m.kind == "text" and m.text]
        combined_text = " ".join(text_parts) if text_parts else None

        base = msgs[-1]  # Usa metadados da última mensagem
        result = ConsolidatedMessage.from_buffered(base)
        result.text = combined_text
        result.kind = "text" if combined_text else base.kind

        if len(msgs) > 1:
            logger.info("[Buffer] %d msgs concatenadas: %r", len(msgs), combined_text)

        return result


class ConsolidatedMessage:
    """
    Interface compatível com InboundMessage para o handler.
    Gerada pelo buffer após consolidação.
    """

    def __init__(self) -> None:
        self.message_id: str = ""
        self.phone: str = ""
        self.from_name: str = ""
        self.chat_id: str = ""
        self.timestamp: int = 0
        self.kind: str = "text"
        self.text: str | None = None
        self.selected_id: str | None = None
        self.selected_title: str | None = None
        self.raw: dict = {}

    @classmethod
    def from_buffered(cls, msg: BufferedMessage) -> "ConsolidatedMessage":
        m = cls()
        m.message_id    = msg.message_id
        m.phone         = msg.phone
        m.from_name     = msg.from_name
        m.chat_id       = msg.chat_id
        m.timestamp     = msg.timestamp
        m.kind          = msg.kind
        m.text          = msg.text
        m.selected_id   = msg.selected_id
        m.selected_title = msg.selected_title
        m.raw           = msg.raw
        return m
