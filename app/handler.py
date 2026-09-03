"""
handler.py — chatbot-feq

Fluxo de atendimento: A DEFINIR.
Este arquivo é o esqueleto pronto para receber o fluxo quando for especificado.

Estados previstos (placeholder):
  state=new       → Boas-vindas
  state=welcome   → Aguardando escolha inicial
  ...             → (demais etapas a definir)
"""

from __future__ import annotations

import logging

from app.database import Database
from app.handoff import is_human
from app.webhook_parser import InboundMessage
from app.whapi_client import WhapiClient, WhapiError

logger = logging.getLogger(__name__)

# ── Mensagens (PLACEHOLDER — substituir quando fluxo for definido) ────────────

WELCOME_BODY = "Olá! Como posso te ajudar?"
WELCOME_BUTTONS = [
    {"id": "id1", "title": "Opção 1"},
    {"id": "id2", "title": "Opção 2"},
]

def welcome_header(from_name: str) -> str:
    return f"Olá, {from_name}! 👋"


# ── Database injetado em runtime ──────────────────────────────────────────────
_db: Database | None = None


def set_database(database: Database) -> None:
    global _db
    _db = database


def restore_state_from_db() -> int:
    """Restaura o _CONVERSATION_STATE a partir do SQLite."""
    if _db is None:
        logger.warning("[Handler] restore_state_from_db chamado sem banco configurado")
        return 0

    restored = _db.load_active_states()
    for numero, ctx in restored.items():
        if numero not in _CONVERSATION_STATE:
            _CONVERSATION_STATE[numero] = ctx

    if restored:
        logger.info(
            "[Handler] Estado restaurado para %d lead(s): %s",
            len(restored),
            ", ".join(f"{n}={c.get('state')}" for n, c in list(restored.items())[:10]),
        )
    return len(restored)


# ── Estado da conversa (em memória) ──────────────────────────────────────────
_CONVERSATION_STATE: dict[str, dict] = {}

# ── Lock por número ───────────────────────────────────────────────────────────
import threading as _threading
_PROCESSING_LOCKS: dict[str, _threading.Lock] = {}
_LOCKS_MUTEX = _threading.Lock()


def _get_lock(numero: str) -> _threading.Lock:
    with _LOCKS_MUTEX:
        if numero not in _PROCESSING_LOCKS:
            _PROCESSING_LOCKS[numero] = _threading.Lock()
        return _PROCESSING_LOCKS[numero]


# ── Helpers DB ────────────────────────────────────────────────────────────────

def _save_db(ctx: dict, numero: str, nome: str) -> None:
    if _db is None:
        return
    try:
        _db.upsert_lead(numero, nome, ctx)
    except Exception:
        logger.exception("[Handler] Falha ao salvar lead %s no banco", numero)


def _log_in(msg: InboundMessage, numero: str) -> None:
    if _db is None:
        return
    try:
        _db.log_message(
            numero=numero,
            direction="in",
            kind=msg.kind,
            content=msg.text or msg.selected_title,
            selected_id=msg.selected_id,
            message_id=msg.message_id,
            from_name=msg.from_name,
        )
    except Exception:
        logger.exception("[Handler] Falha ao logar msg entrada %s", numero)


def _log_out(numero: str, kind: str, content: str) -> None:
    if _db is None:
        return
    try:
        _db.log_message(numero=numero, direction="out", kind=kind, content=content)
    except Exception:
        logger.exception("[Handler] Falha ao logar msg saida %s", numero)


# ── Handler principal ─────────────────────────────────────────────────────────

def handle_incoming_message(
    whapi: WhapiClient,
    crm: None,           # placeholder — CRM a definir
    msg: InboundMessage,
) -> None:
    numero = msg.chat_id.split("@")[0]

    lock = _get_lock(numero)
    if not lock.acquire(blocking=False):
        logger.info("[Handler] %s — já em processamento, mensagem descartada", numero)
        return
    try:
        _handle_message_locked(whapi, msg, numero)
    finally:
        lock.release()


def _handle_message_locked(
    whapi: WhapiClient,
    msg: InboundMessage,
    numero: str,
) -> None:
    ctx   = _CONVERSATION_STATE.get(numero, {"state": "new"})
    state = ctx.get("state", "new")

    logger.info(
        "[Handler] phone=%s state=%s kind=%s selected_id=%s",
        numero, state, msg.kind, msg.selected_id,
    )
    _log_in(msg, numero)

    # Handoff: bot silenciado para esse número
    if is_human(numero):
        logger.info("[Handler] %s em atendimento humano — bot silenciado", numero)
        return

    # Lead já concluído
    if state == "concluido":
        logger.info("[Handler] %s — lead concluído — ignorando", numero)
        return

    # Resposta a botão
    if msg.kind == "button" and msg.selected_id:
        if state == "welcome":
            _handle_button_welcome(whapi, msg, numero, ctx)
        else:
            _handle_welcome(whapi, msg, numero)
        return

    # Qualquer mensagem de texto livre → boas-vindas
    _handle_welcome(whapi, msg, numero)


# ── Etapa 1: Boas-vindas ──────────────────────────────────────────────────────

def _handle_welcome(
    whapi: WhapiClient,
    msg: InboundMessage,
    numero: str,
) -> None:
    sent = _send_welcome(whapi, msg.phone, msg.from_name)
    if not sent:
        return

    new_ctx: dict = {"state": "welcome"}
    _CONVERSATION_STATE[numero] = new_ctx
    _save_db(new_ctx, numero, msg.from_name)


# ── Etapa 2: Tratamento do botão de boas-vindas (PLACEHOLDER) ────────────────

def _handle_button_welcome(
    whapi: WhapiClient,
    msg: InboundMessage,
    numero: str,
    ctx: dict,
) -> None:
    """
    TODO: implementar as rotas após a escolha inicial.
    Por enquanto responde com texto de placeholder.
    """
    logger.info("[Handler] %s escolheu id=%s — fluxo a definir", numero, msg.selected_id)
    try:
        whapi.send_text(
            to=msg.phone,
            body="✅ Recebemos sua escolha! Em breve você será atendido.",
        )
        new_ctx = {**ctx, "state": "concluido"}
        _CONVERSATION_STATE[numero] = new_ctx
        _save_db(new_ctx, numero, msg.from_name)
    except WhapiError:
        logger.exception("[Handler] Falha ao responder escolha para %s", numero)


# ── Helper: envia boas-vindas ─────────────────────────────────────────────────

def _send_welcome(whapi: WhapiClient, phone: str, from_name: str) -> bool:
    try:
        whapi.send_buttons(
            to=phone,
            body=WELCOME_BODY,
            buttons=WELCOME_BUTTONS,
            header=welcome_header(from_name),
        )
        logger.info("[Handler] Boas-vindas enviadas para %s", phone)
        return True
    except WhapiError:
        logger.exception("[Handler] Falha ao enviar boas-vindas para %s", phone)
        return False
