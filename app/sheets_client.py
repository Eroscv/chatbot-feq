"""
sheets_client.py

Atualiza a planilha de leads via Maton → Google Sheets API.

Colunas (A-H):
  A: Número  B: Nome  C: Ciclo  D: Empresa  E: CNPJ
  F: Localidade  G: Tipo de Serviço  H: Melhor Forma de Envio

Ciclo (0-7) = etapa atual da qualificação.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_CICLO_MAP = {
    "new":       0,
    "welcome":   1,
    "pj":        2,
    "servicos":  3,
    "cidade":    4,
    "cnpj":      5,
    "canal":     6,
    "concluido": 7,
}

SPREADSHEET_ID = "1qsg_dXp9Xrvyym-_aWUaAWozR723rGsjQySqQbiJlW4"
SHEET_NAME = "Página1"


class SheetsError(Exception):
    pass


class SheetsClient:
    def __init__(self, maton_api_key: str) -> None:
        self._key = maton_api_key
        self._base = "https://api.maton.ai/google-sheets/v4/spreadsheets"
        self._client = httpx.Client(timeout=_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _range_url(self, range_str: str, suffix: str = "") -> str:
        """Monta URL com range URL-encoded corretamente."""
        import urllib.parse
        encoded = urllib.parse.quote(range_str)
        base = f"{self._base}/{SPREADSHEET_ID}/values/{encoded}"
        return f"{base}{suffix}" if suffix else base

    # ── Lê todos os números já cadastrados ────────────────────────────────────

    def _get_all_rows(self) -> list[list]:
        """Retorna todas as linhas da planilha (incluindo cabeçalho)."""
        url = self._range_url(f"{SHEET_NAME}!A:H")
        try:
            resp = self._client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json().get("values", [])
        except Exception as e:
            raise SheetsError(f"Erro ao ler planilha: {e}") from e

    # ── Insere ou atualiza linha pelo número ──────────────────────────────────

    def upsert_lead(self, ctx: dict, numero: str, nome: str) -> None:
        """Insere ou atualiza linha do lead na planilha."""
        ciclo = _CICLO_MAP.get(ctx.get("state", "new"), 0)
        linha = [
            numero,
            nome,
            ciclo,
            ctx.get("empresa", ""),
            ctx.get("cnpj", ""),
            ctx.get("cidade", ""),
            ctx.get("servico", ""),
            ctx.get("canal", ""),
        ]

        # Normaliza número (só dígitos) para comparação consistente
        import re
        numero_norm = re.sub(r"\D", "", str(numero))

        # Procura linha existente pelo número (coluna A)
        rows = self._get_all_rows()
        row_index = None  # 1-based, incluindo cabeçalho
        for i, row in enumerate(rows):
            if i == 0:
                continue  # pula cabeçalho
            if row and re.sub(r"\D", "", str(row[0])) == numero_norm:
                row_index = i + 1  # sheets é 1-indexed
                break

        if row_index:
            # Atualiza linha existente
            url = self._range_url(f"{SHEET_NAME}!A{row_index}:H{row_index}", "?valueInputOption=USER_ENTERED")
            try:
                resp = self._client.put(url, headers=self._headers, json={"values": [linha]})
                resp.raise_for_status()
                logger.info("[Sheets] Lead %s atualizado na linha %s (ciclo=%s)", numero, row_index, ciclo)
            except httpx.HTTPStatusError as e:
                raise SheetsError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        else:
            # Appenda nova linha
            url = self._range_url(f"{SHEET_NAME}!A1:H1", ":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
            try:
                resp = self._client.post(url, headers=self._headers, json={"values": [linha]})
                resp.raise_for_status()
                logger.info("[Sheets] Lead %s inserido na planilha (ciclo=%s)", numero, ciclo)
            except httpx.HTTPStatusError as e:
                raise SheetsError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
