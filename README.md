# chatbot-feq

Chatbot WhatsApp via Whapi — baseado na estrutura do chatbot-maximo.

## Stack
- FastAPI + Uvicorn (porta 8001)
- Whapi (WhatsApp)
- SQLite (estado local)
- CRM: a definir
- Fluxo de conversa: a definir

## Estrutura

```
app/
  main.py          — FastAPI + webhook
  handler.py       — Fluxo de estados (editar aqui quando o fluxo for definido)
  config.py        — Settings via .env
  database.py      — SQLite (leads + histórico)
  whapi_client.py  — Envio de msgs WhatsApp
  webhook_parser.py — Parse do payload Whapi
  handoff.py       — Atendimento humano
  dedup.py         — Deduplicação de mensagens
  rate_limit.py    — Rate limiting por número
  message_buffer.py — Buffer de mensagens (agrupa msgs rápidas)
```

## Setup

```bash
cp .env.example .env
# editar .env com token Whapi e demais configs

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Endpoints

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/health` | Status |
| GET | `/leads` | Lista leads |
| GET | `/leads/{numero}` | Lead + histórico |
| GET | `/stats` | Estatísticas |
| POST | `/webhook/whapi` | Webhook Whapi |
| GET | `/handoff` | Lista em atendimento humano |
| POST | `/handoff/{numero}/take` | Assume atendimento |
| POST | `/handoff/{numero}/release` | Libera para o bot |

## Serviço systemd

```ini
# /etc/systemd/system/chatbot-feq.service
[Unit]
Description=Chatbot FEQ
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/.openclaw/workspace/chatbot_feq
ExecStart=/home/ubuntu/.openclaw/workspace/chatbot_feq/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
