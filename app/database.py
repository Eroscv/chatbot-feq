"""
database.py

SQLite persistente para o chatbot Máximo.

Tabelas:
  leads          — um registro por número de telefone (dados de qualificação)
  conversations  — histórico completo de mensagens por lead
  cards          — card_id do CRM associado ao lead

Uso:
  db = Database("chatbot.db")
  db.init()
  db.upsert_lead(numero, nome, ctx)
  db.log_message(numero, direction, kind, content)
  db.save_card(numero, card_id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Mapeamento state → ciclo numérico
_CICLO_MAP = {
    "new":       0,
    "welcome":   1,
    "pj":        2,
    "servicos":  3,
    "cidade":    4,
    "cnpj":      5,
    "canal":     6,
    "aguarda_email": 6,
    "concluido": 7,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    numero          TEXT NOT NULL UNIQUE,
    nome            TEXT,
    state           TEXT DEFAULT 'new',
    ciclo           INTEGER DEFAULT 0,
    empresa         TEXT,
    cnpj            TEXT,
    cidade          TEXT,
    servico         TEXT,
    canal           TEXT,
    email           TEXT,
    interesse       TEXT,
    card_id         TEXT,
    ctx_json        TEXT,
    criado_em       TEXT NOT NULL,
    atualizado_em   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    numero      TEXT NOT NULL,
    from_name   TEXT,
    direction   TEXT NOT NULL CHECK(direction IN ('in', 'out')),
    kind        TEXT NOT NULL,
    content     TEXT,
    selected_id TEXT,
    message_id  TEXT,
    ts          TEXT NOT NULL,
    FOREIGN KEY(numero) REFERENCES leads(numero)
);

-- Migração: adiciona from_name se já existir o banco sem a coluna
CREATE TABLE IF NOT EXISTS _migrations (applied TEXT PRIMARY KEY);

CREATE INDEX IF NOT EXISTS idx_conversations_numero ON conversations(numero);
CREATE INDEX IF NOT EXISTS idx_conversations_ts     ON conversations(ts);
CREATE INDEX IF NOT EXISTS idx_leads_state          ON leads(state);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str = "chatbot.db") -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def init(self) -> None:
        """Cria o banco e as tabelas se não existirem."""
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        logger.info("[DB] Banco inicializado em %s", self._path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def _migrate(self) -> None:
        """Aplica migrações incrementais no banco existente."""
        # Adiciona from_name se coluna não existe (bancos criados antes desta versão)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(conversations)").fetchall()]
        if "from_name" not in cols:
            self.conn.execute("ALTER TABLE conversations ADD COLUMN from_name TEXT")
            logger.info("[DB] Migração aplicada: conversations.from_name adicionado")

    @property
    def conn(self) -> sqlite3.Connection:
        if not self._conn:
            raise RuntimeError("Database não inicializado. Chame db.init() primeiro.")
        return self._conn

    # ── Leads ─────────────────────────────────────────────────────────────────

    def upsert_lead(self, numero: str, nome: str, ctx: dict) -> None:
        """Insere ou atualiza o lead com o contexto atual da conversa."""
        ciclo = _CICLO_MAP.get(ctx.get("state", "new"), 0)
        now = _now()
        # Usa None (não string vazia) para campos ausentes no ctx,
        # garantindo que COALESCE preserve o valor existente no banco.
        def _val(key):
            v = ctx.get(key)
            return v if v else None

        self.conn.execute("""
            INSERT INTO leads (numero, nome, state, ciclo, empresa, cnpj, cidade, servico,
                               canal, email, interesse, card_id, ctx_json, criado_em, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(numero) DO UPDATE SET
                nome          = excluded.nome,
                state         = excluded.state,
                ciclo         = excluded.ciclo,
                empresa       = COALESCE(excluded.empresa, leads.empresa),
                cnpj          = COALESCE(excluded.cnpj, leads.cnpj),
                cidade        = COALESCE(excluded.cidade, leads.cidade),
                servico       = COALESCE(excluded.servico, leads.servico),
                canal         = COALESCE(excluded.canal, leads.canal),
                email         = COALESCE(excluded.email, leads.email),
                interesse     = COALESCE(excluded.interesse, leads.interesse),
                card_id       = COALESCE(excluded.card_id, leads.card_id),
                ctx_json      = excluded.ctx_json,
                atualizado_em = excluded.atualizado_em
        """, (
            numero,
            nome,
            ctx.get("state", "new"),
            ciclo,
            _val("empresa"),
            _val("cnpj"),
            _val("cidade"),
            _val("servico"),
            _val("canal"),
            _val("email"),
            _val("interesse"),
            _val("card_id"),
            json.dumps(ctx, ensure_ascii=False),
            now,
            now,
        ))
        self.conn.commit()
        logger.debug("[DB] Lead %s upserted (state=%s ciclo=%s)", numero, ctx.get("state"), ciclo)

    def get_lead(self, numero: str) -> dict | None:
        """Retorna os dados do lead ou None se não existir."""
        row = self.conn.execute(
            "SELECT * FROM leads WHERE numero = ?", (numero,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_leads(self, state: str | None = None) -> list[dict]:
        """Retorna todos os leads, opcionalmente filtrados por state."""
        if state:
            rows = self.conn.execute(
                "SELECT * FROM leads WHERE state = ? ORDER BY atualizado_em DESC", (state,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM leads ORDER BY atualizado_em DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Conversas ─────────────────────────────────────────────────────────────

    def log_message(
        self,
        numero: str,
        direction: str,        # "in" | "out"
        kind: str,             # "text" | "button" | "interactive" | "other"
        content: str | None,
        selected_id: str | None = None,
        message_id: str | None = None,
        from_name: str | None = None,
    ) -> None:
        """Registra uma mensagem no histórico da conversa."""
        self.conn.execute("""
            INSERT INTO conversations (numero, from_name, direction, kind, content, selected_id, message_id, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (numero, from_name, direction, kind, content, selected_id, message_id, _now()))
        self.conn.commit()
        logger.debug("[DB] Msg %s/%s logada para %s", direction, kind, numero)

    def get_conversation(self, numero: str, limit: int = 50) -> list[dict]:
        """Retorna as últimas N mensagens de um lead."""
        rows = self.conn.execute("""
            SELECT * FROM conversations
            WHERE numero = ?
            ORDER BY ts DESC
            LIMIT ?
        """, (numero, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]  # ordem cronológica

    def get_all_conversations(self, limit: int = 200) -> list[dict]:
        """Retorna as últimas N mensagens de todos os leads."""
        rows = self.conn.execute("""
            SELECT * FROM conversations
            ORDER BY ts DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def load_active_states(self) -> dict[str, dict]:
        """
        Restaura o estado em memória dos leads que ainda não concluíram o fluxo.

        Retorna um dict {numero: ctx} com todos os leads cujo state NÃO é
        'concluido' ou 'new', permitindo que o handler retome conversas
        interrompidas por reinicialização do servidor.

        Inclui somente leads atualizados nas últimas 72 horas para evitar
        recarregar leads muito antigos que provavelmente não voltarão.
        """
        rows = self.conn.execute("""
            SELECT numero, ctx_json, state
            FROM leads
            WHERE state NOT IN ('concluido', 'new')
              AND ctx_json IS NOT NULL
              AND atualizado_em >= datetime('now', '-72 hours')
            ORDER BY atualizado_em DESC
        """).fetchall()

        restored: dict[str, dict] = {}
        for row in rows:
            try:
                ctx = json.loads(row["ctx_json"])
                restored[row["numero"]] = ctx
            except (json.JSONDecodeError, TypeError):
                logger.warning("[DB] ctx_json inválido para %s — ignorado", row["numero"])

        logger.info("[DB] %d estado(s) ativo(s) restaurado(s) do banco", len(restored))
        return restored

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Retorna estatísticas gerais."""
        total = self.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        concluidos = self.conn.execute(
            "SELECT COUNT(*) FROM leads WHERE state = 'concluido'"
        ).fetchone()[0]
        por_servico = self.conn.execute("""
            SELECT servico, COUNT(*) as total
            FROM leads WHERE servico != ''
            GROUP BY servico ORDER BY total DESC
        """).fetchall()
        por_ciclo = self.conn.execute("""
            SELECT ciclo, COUNT(*) as total
            FROM leads GROUP BY ciclo ORDER BY ciclo
        """).fetchall()
        return {
            "total_leads": total,
            "concluidos": concluidos,
            "por_servico": [dict(r) for r in por_servico],
            "por_ciclo": [dict(r) for r in por_ciclo],
        }
