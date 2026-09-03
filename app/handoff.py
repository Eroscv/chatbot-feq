"""
handoff.py

Sistema de handoff — pausa o bot quando um humano assume o atendimento.

Como funciona:
  - Quando um atendente humano assume, chama POST /handoff/{numero}/take
  - O bot para de responder para aquele número (estado "human")
  - Quando o atendente libera, chama POST /handoff/{numero}/release
  - O bot volta a responder normalmente

Endpoints:
  POST /handoff/{numero}/take     → atendente assume
  POST /handoff/{numero}/release  → atendente libera
  GET  /handoff                   → lista todos em atendimento humano
  GET  /handoff/{numero}          → status de um número específico
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Mapeia numero → {"agent": str, "since": str, "reason": str}
_HANDOFF_STATE: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── API do handoff ────────────────────────────────────────────────────────────

def take(numero: str, agent: str = "humano", reason: str = "") -> dict:
    """
    Registra que um humano assumiu o atendimento do número.
    O bot para de responder para esse número.
    """
    _HANDOFF_STATE[numero] = {
        "numero": numero,
        "agent":  agent,
        "reason": reason,
        "since":  _now(),
    }
    logger.info("[Handoff] %s assumido por %s (motivo: %s)", numero, agent, reason or "—")
    return _HANDOFF_STATE[numero]


def release(numero: str) -> dict:
    """
    Remove o número do handoff — bot retoma o atendimento.
    """
    entry = _HANDOFF_STATE.pop(numero, None)
    if entry:
        logger.info("[Handoff] %s liberado (era: %s)", numero, entry.get("agent"))
        return {"status": "released", "entry": entry}
    return {"status": "not_found", "numero": numero}


def is_human(numero: str) -> bool:
    """Retorna True se o número está em atendimento humano."""
    return numero in _HANDOFF_STATE


def get_status(numero: str) -> dict | None:
    """Retorna o estado do handoff para o número, ou None."""
    return _HANDOFF_STATE.get(numero)


def list_all() -> list[dict]:
    """Lista todos os números em atendimento humano."""
    return list(_HANDOFF_STATE.values())
