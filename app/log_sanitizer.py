"""
log_sanitizer.py

Sanitiza dados sensíveis nos logs (LGPD).

Mascaramento aplicado:
  - Telefone brasileiro: 5511999999999 → 5511***9999
  - CNPJ: 12.345.678/0001-90 → 12.***.***/**-**
  - CPF: 123.456.789-00 → 123.***.***-**
  - E-mail: user@domain.com → u***@domain.com

Instalação no logging:
  from app.log_sanitizer import install_sanitizer
  install_sanitizer()

Depois disso, todos os handlers de log passam pelo filtro automaticamente.
"""

from __future__ import annotations

import logging
import re

# ── Padrões para mascarar ─────────────────────────────────────────────────────

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Telefone brasileiro: 55 + DDD + número (10-11 dígitos)
    (re.compile(r'\b(55\d{2})\d{4,5}(\d{4})\b'), r'\1***\2'),

    # CNPJ com pontuação: 12.345.678/0001-90
    (re.compile(r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b'), r'**.***.***/**-**'),

    # CNPJ sem pontuação: 12345678000190
    (re.compile(r'\b\d{14}\b'), r'**************'),

    # CPF com pontuação: 123.456.789-00
    (re.compile(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b'), r'***.***.**-**'),

    # E-mail: preserva domínio, mascara usuário
    (re.compile(r'\b([a-zA-Z0-9])[a-zA-Z0-9._%+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'), r'\1***@\2'),
]


def sanitize(text: str) -> str:
    """Aplica todos os padrões de mascaramento ao texto."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SanitizingFilter(logging.Filter):
    """Filtro de logging que mascara dados sensíveis em todas as mensagens."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitiza a mensagem formatada
        record.msg = sanitize(str(record.msg))
        # Sanitiza os args (evita que sejam interpolados antes da sanitização)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize(str(a)) for a in record.args)
        return True


def install_sanitizer(logger_name: str = "") -> None:
    """
    Instala o filtro de sanitização no logger raiz (ou no logger especificado).
    Deve ser chamado uma única vez, logo após configurar o logging.
    """
    target = logging.getLogger(logger_name)
    target.addFilter(SanitizingFilter())
    logging.getLogger(__name__).info(
        "[LogSanitizer] Filtro de dados sensíveis instalado no logger '%s'",
        logger_name or "root",
    )
