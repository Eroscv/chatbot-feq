"""
dedup.py

Deduplicação de mensagens por message_id.

Problema:
  A Whapi pode re-entregar o mesmo webhook em caso de timeout/retry.
  Sem deduplicação, o bot processa a mesma mensagem duas vezes.

Solução:
  Cache em memória com TTL — guarda os message_ids processados
  por DEDUP_TTL_SEC segundos. Após o TTL, o ID é removido.

Uso:
  if dedup.seen(message_id):
      return  # já processado
"""

from __future__ import annotations

import logging
import time
from threading import Lock

logger = logging.getLogger(__name__)

# Tempo (segundos) que um message_id fica na cache de deduplicação
DEDUP_TTL_SEC: int = 300  # 5 minutos


class MessageDedup:
    def __init__(self, ttl: int = DEDUP_TTL_SEC) -> None:
        self._ttl = ttl
        self._seen: dict[str, float] = {}  # message_id → timestamp de entrada
        self._lock = Lock()

    def seen(self, message_id: str) -> bool:
        """
        Retorna True se o message_id já foi processado (duplicata).
        Registra o ID e retorna False na primeira vez.
        Limpa entradas expiradas a cada chamada.
        """
        if not message_id:
            return False

        now = time.monotonic()

        with self._lock:
            self._evict(now)

            if message_id in self._seen:
                logger.warning("[Dedup] message_id duplicado ignorado: %s", message_id)
                return True

            self._seen[message_id] = now
            return False

    def _evict(self, now: float) -> None:
        """Remove IDs com TTL expirado."""
        expired = [mid for mid, ts in self._seen.items() if now - ts > self._ttl]
        for mid in expired:
            del self._seen[mid]
        if expired:
            logger.debug("[Dedup] %d IDs expirados removidos", len(expired))

    def size(self) -> int:
        with self._lock:
            return len(self._seen)


# Instância global
dedup = MessageDedup()
