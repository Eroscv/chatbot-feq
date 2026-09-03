"""
main.py — chatbot-feq

Ponto de entrada da aplicação FastAPI.
"""

import logging
import re as _re

from fastapi import Body, FastAPI, Request
from pydantic import BaseModel

from app.config import load_settings
from app.dedup import dedup
from app.log_sanitizer import install_sanitizer
from app.rate_limit import rate_limiter
from app.database import Database
from app.handler import handle_incoming_message, set_database, restore_state_from_db
from app.handoff import take as handoff_take, release as handoff_release, list_all as handoff_list, get_status as handoff_status
from app.logging_config import setup_logging
from app.message_buffer import MessageBuffer, BufferedMessage
from app.webhook_parser import parse_whapi_webhook
from app.whapi_client import WhapiClient

settings = load_settings()
setup_logging(settings.log_level)
install_sanitizer()
logger = logging.getLogger(__name__)

app = FastAPI(title="chatbot-feq")

# ── Whapi ─────────────────────────────────────────────────────────────────────
whapi = WhapiClient(token=settings.whapi_token, base_url=settings.whapi_base_url)

# ── Banco de dados SQLite ─────────────────────────────────────────────────────
db = Database("chatbot.db")
db.init()
set_database(db)
whapi.set_database(db)

# Restaura conversas ativas
_restored = restore_state_from_db()
if _restored:
    logger.info("[Main] %d conversa(s) restaurada(s) do SQLite", _restored)
else:
    logger.info("[Main] Nenhuma conversa ativa para restaurar")

# ── CRM placeholder (nenhum por enquanto) ─────────────────────────────────────
crm = None  # substituir quando CRM for definido

# ── Buffer de mensagens ───────────────────────────────────────────────────────
buffer = MessageBuffer(handler=handle_incoming_message, whapi=whapi, crm=crm)

# ── Whitelist ────────────────────────────────────────────────────────────────
_ALLOWED_PHONES: set[str] = set()
if settings.allowed_phones:
    _ALLOWED_PHONES = {
        _re.sub(r"\D", "", p.strip())
        for p in settings.allowed_phones.split(",")
        if p.strip()
    }
    logger.info("[Main] Whitelist ativa: %d número(s) autorizados", len(_ALLOWED_PHONES))
else:
    logger.info("[Main] Whitelist desativada — todos os números são atendidos")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/leads")
def list_leads(state: str | None = None) -> dict:
    return {"leads": db.get_all_leads(state=state)}


@app.get("/leads/{numero}")
def get_lead(numero: str) -> dict:
    lead = db.get_lead(numero)
    if not lead:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    conversation = db.get_conversation(numero)
    return {"lead": lead, "conversation": conversation}


@app.get("/stats")
def stats() -> dict:
    return db.stats()


# ── Handoff ───────────────────────────────────────────────────────────────────

class HandoffRequest(BaseModel):
    agent: str = "humano"
    reason: str = ""


@app.get("/handoff")
def list_handoff() -> dict:
    return {"handoff": handoff_list()}


@app.get("/handoff/{numero}")
def get_handoff(numero: str) -> dict:
    entry = handoff_status(numero)
    if not entry:
        return {"status": "bot", "numero": numero}
    return {"status": "human", **entry}


@app.post("/handoff/{numero}/take")
def take_handoff(numero: str, body: HandoffRequest = HandoffRequest()) -> dict:
    entry = handoff_take(numero, agent=body.agent, reason=body.reason)
    logger.info("[Handoff] %s assumido por %s", numero, body.agent)
    return {"status": "taken", **entry}


@app.post("/handoff/{numero}/release")
def release_handoff(numero: str) -> dict:
    result = handoff_release(numero)
    logger.info("[Handoff] %s liberado", numero)
    return result


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.post("/webhook/whapi")
async def whapi_webhook(payload: dict = Body(...)) -> dict:
    messages = parse_whapi_webhook(payload)
    for msg in messages:
        try:
            if dedup.seen(msg.message_id):
                continue

            numero = msg.chat_id.split("@")[0]
            numero_digits = _re.sub(r"\D", "", numero)
            if _ALLOWED_PHONES and numero_digits not in _ALLOWED_PHONES:
                logger.debug("[Webhook] Número não autorizado descartado: %s", numero_digits[-4:])
                continue

            if not rate_limiter.allow(numero):
                logger.warning("[Webhook] Rate limit atingido para %s — msg descartada", numero)
                continue

            buffer.push(BufferedMessage(
                numero=msg.chat_id.split("@")[0],
                phone=msg.phone,
                from_name=msg.from_name,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                timestamp=msg.timestamp,
                kind=msg.kind,
                text=msg.text,
                selected_id=msg.selected_id,
                selected_title=msg.selected_title,
                raw=msg.raw,
            ))
        except Exception:
            logger.exception("Erro ao enfileirar mensagem %s de %s", msg.message_id, msg.phone)
    return {"status": "ok"}


@app.on_event("shutdown")
def shutdown() -> None:
    whapi.close()
    db.close()
