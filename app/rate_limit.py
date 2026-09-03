"""
rate_limit.py

Rate limiting por número de telefone — token bucket simples.

Problema:
  Um número pode enviar centenas de mensagens por segundo,
  sobrecarregando o servidor e gerando custos desnecessários.

Solução:
  Token bucket por número:
    - Cada número tem um bucket de MAX_TOKENS tokens
    - A cada mensagem, consome 1 token
    - Tokens são reabastecidos a REFILL_RATE por segundo
    - Quando o bucket esvazia, mensagens são descartadas

Configuração padrão: 10 msgs em burst, reabastece 1 msg/2s
"""

from __future__ import annotations

import logging
import time
from threading import Lock

logger = logging.getLogger(__name__)

# Capacidade máxima do bucket (burst máximo permitido)
MAX_TOKENS: float = 10.0

# Taxa de reabastecimento (tokens por segundo)
REFILL_RATE: float = 0.5  # 1 mensagem a cada 2 segundos

# TTL para limpar buckets inativos (segundos)
BUCKET_TTL_SEC: int = 600  # 10 minutos


class TokenBucket:
    __slots__ = ("tokens", "last_refill")

    def __init__(self) -> None:
        self.tokens: float = MAX_TOKENS
        self.last_refill: float = time.monotonic()


class RateLimiter:
    def __init__(
        self,
        max_tokens: float = MAX_TOKENS,
        refill_rate: float = REFILL_RATE,
    ) -> None:
        self._max = max_tokens
        self._rate = refill_rate
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = Lock()

    def allow(self, numero: str) -> bool:
        """
        Retorna True se a mensagem deve ser processada.
        Retorna False se o número excedeu o rate limit.
        """
        now = time.monotonic()

        with self._lock:
            self._evict(now)

            bucket = self._buckets.get(numero)
            if bucket is None:
                bucket = TokenBucket()
                self._buckets[numero] = bucket

            # Reabastece tokens proporcionalmente ao tempo decorrido
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._max, bucket.tokens + elapsed * self._rate)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True

            logger.warning(
                "[RateLimit] %s excedeu rate limit (%.2f tokens restantes)",
                numero, bucket.tokens,
            )
            return False

    def _evict(self, now: float) -> None:
        """Remove buckets de números inativos."""
        expired = [
            num for num, b in self._buckets.items()
            if now - b.last_refill > BUCKET_TTL_SEC
        ]
        for num in expired:
            del self._buckets[num]

    def size(self) -> int:
        with self._lock:
            return len(self._buckets)


# Instância global
rate_limiter = RateLimiter()
