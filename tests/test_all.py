"""
test_all.py — Suite completa de testes internos do chatbot_maximo
Cobre: dedup, rate_limit, log_sanitizer, database, sheets_client,
       message_buffer, handoff, webhook_parser, handler (state machine), crm_client
"""
import asyncio
import json
import os
import sys
import time
import tempfile
import pytest

# garante que app/ é importável
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ─────────────────────────────────────────────────────────────────────────────
# DEDUP
# ─────────────────────────────────────────────────────────────────────────────
class TestDedup:
    def setup_method(self):
        from app.dedup import MessageDedup
        self.d = MessageDedup(ttl=2)

    def test_first_time_returns_false(self):
        assert self.d.seen("msg-001") is False

    def test_second_time_returns_true(self):
        self.d.seen("msg-abc")
        assert self.d.seen("msg-abc") is True

    def test_different_ids_independent(self):
        self.d.seen("msg-x")
        assert self.d.seen("msg-y") is False

    def test_empty_id_never_blocks(self):
        assert self.d.seen("") is False
        assert self.d.seen("") is False  # nunca bloqueia ID vazio

    def test_ttl_expiry(self):
        self.d.seen("msg-expire")
        time.sleep(2.1)
        # após TTL, deve permitir novamente
        assert self.d.seen("msg-expire") is False

    def test_size_grows_and_shrinks(self):
        for i in range(5):
            self.d.seen(f"msg-{i}")
        assert self.d.size() == 5

# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMITER
# ─────────────────────────────────────────────────────────────────────────────
class TestRateLimiter:
    def setup_method(self):
        from app.rate_limit import RateLimiter
        self.rl = RateLimiter(max_tokens=3, refill_rate=10.0)  # recarga rápida para teste

    def test_burst_allowed(self):
        results = [self.rl.allow("5511001") for _ in range(3)]
        assert all(results), "Burst de 3 deve ser permitido"

    def test_exceeds_burst_blocked(self):
        for _ in range(3):
            self.rl.allow("5511002")
        assert self.rl.allow("5511002") is False

    def test_different_numbers_independent(self):
        for _ in range(3):
            self.rl.allow("5511003")
        assert self.rl.allow("5511004") is True  # número diferente ainda tem tokens

    def test_refill_over_time(self):
        rl = __import__("app.rate_limit", fromlist=["RateLimiter"]).RateLimiter(max_tokens=1, refill_rate=100)
        rl.allow("5511005")  # esvazia
        time.sleep(0.02)    # refill_rate=100 → 2 tokens em 20ms
        assert rl.allow("5511005") is True

    def test_size_tracked(self):
        self.rl.allow("5511006")
        assert self.rl.size() >= 1

# ─────────────────────────────────────────────────────────────────────────────
# LOG SANITIZER
# ─────────────────────────────────────────────────────────────────────────────
class TestLogSanitizer:
    def setup_method(self):
        from app.log_sanitizer import sanitize
        self.s = sanitize

    def test_phone_masked(self):
        out = self.s("phone=5511999990001")
        assert "999990001" not in out
        assert "5511" in out  # prefixo mantido

    def test_cnpj_masked(self):
        out = self.s("cnpj=02.617.943/0001-93")
        assert "345.678" not in out

    def test_cpf_masked(self):
        out = self.s("cpf=123.456.789-00")
        assert "456.789" not in out

    def test_email_masked(self):
        out = self.s("email=joao@empresa.com")
        assert "joao" not in out
        assert "empresa.com" in out  # domínio mantido

    def test_plain_text_unchanged(self):
        txt = "Serviço de limpeza selecionado"
        assert self.s(txt) == txt

    def test_multiple_patterns_in_one_string(self):
        out = self.s("Lead 5511999990001 cnpj=02.617.943/0001-93 email=a@b.com")
        assert "999990001" not in out
        assert "345.678" not in out

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
class TestDatabase:
    def setup_method(self):
        from app.database import Database
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.tmp.name)
        self.db.init()

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_upsert_lead_insert(self):
        self.db.upsert_lead("5511001", "Ana", {"state": "welcome"})
        lead = self.db.get_lead("5511001")
        assert lead is not None
        assert lead["nome"] == "Ana"
        assert lead["state"] == "welcome"
        assert lead["ciclo"] == 1

    def test_upsert_lead_update(self):
        self.db.upsert_lead("5511002", "Bia", {"state": "welcome"})
        self.db.upsert_lead("5511002", "Bia", {"state": "concluido", "cnpj": "02.617.943/0001-93"})
        lead = self.db.get_lead("5511002")
        assert lead["state"] == "concluido"
        assert lead["ciclo"] == 7
        assert lead["cnpj"] == "02.617.943/0001-93"

    def test_upsert_preserves_existing_fields(self):
        self.db.upsert_lead("5511003", "Carlos", {"state": "cnpj", "servico": "Limpeza"})
        self.db.upsert_lead("5511003", "Carlos", {"state": "canal"})  # sem servico
        lead = self.db.get_lead("5511003")
        assert lead["servico"] == "Limpeza"  # não deve ser apagado

    def test_log_message_in(self):
        self.db.upsert_lead("5511004", "Duda", {"state": "new"})
        self.db.log_message("5511004", "in", "text", "Olá")
        conv = self.db.get_conversation("5511004")
        assert len(conv) == 1
        assert conv[0]["direction"] == "in"
        assert conv[0]["content"] == "Olá"

    def test_log_message_out(self):
        self.db.upsert_lead("5511005", "Ed", {"state": "new"})
        self.db.log_message("5511005", "out", "interactive", "Boas-vindas")
        conv = self.db.get_conversation("5511005")
        assert conv[0]["direction"] == "out"
        assert conv[0]["kind"] == "interactive"

    def test_get_all_leads_no_filter(self):
        for i, name in enumerate(["F", "G", "H"]):
            self.db.upsert_lead(f"551100{i}", name, {"state": "welcome"})
        leads = self.db.get_all_leads()
        assert len(leads) >= 3

    def test_get_all_leads_state_filter(self):
        self.db.upsert_lead("5511010", "I", {"state": "concluido"})
        self.db.upsert_lead("5511011", "J", {"state": "welcome"})
        concluidos = self.db.get_all_leads(state="concluido")
        assert all(l["state"] == "concluido" for l in concluidos)

    def test_stats(self):
        self.db.upsert_lead("5511020", "K", {"state": "concluido", "servico": "Limpeza"})
        self.db.upsert_lead("5511021", "L", {"state": "concluido", "servico": "Limpeza"})
        stats = self.db.stats()
        assert stats["total_leads"] >= 2
        assert stats["concluidos"] >= 2
        assert any(s["servico"] == "Limpeza" for s in stats["por_servico"])

    def test_get_nonexistent_lead(self):
        assert self.db.get_lead("0000000000") is None

    def test_conversation_chronological_order(self):
        self.db.upsert_lead("5511030", "M", {"state": "new"})
        self.db.log_message("5511030", "in", "text", "msg1")
        self.db.log_message("5511030", "out", "text", "resp1")
        self.db.log_message("5511030", "in", "text", "msg2")
        conv = self.db.get_conversation("5511030")
        directions = [c["direction"] for c in conv]
        assert directions == ["in", "out", "in"]

# ─────────────────────────────────────────────────────────────────────────────
# HANDOFF
# ─────────────────────────────────────────────────────────────────────────────
class TestHandoff:
    def setup_method(self):
        import app.handoff as h
        h._HANDOFF_STATE.clear()
        self.h = h

    def test_take_registers(self):
        self.h.take("5511001", agent="João", reason="VIP")
        assert self.h.is_human("5511001") is True

    def test_is_human_false_by_default(self):
        assert self.h.is_human("5511999") is False

    def test_release_removes(self):
        self.h.take("5511002")
        self.h.release("5511002")
        assert self.h.is_human("5511002") is False

    def test_release_nonexistent_safe(self):
        result = self.h.release("naoexiste")
        assert result["status"] == "not_found"

    def test_list_all(self):
        self.h.take("5511003")
        self.h.take("5511004")
        lista = self.h.list_all()
        numeros = [e["numero"] for e in lista]
        assert "5511003" in numeros
        assert "5511004" in numeros

    def test_get_status_returns_entry(self):
        self.h.take("5511005", agent="Maria", reason="teste")
        entry = self.h.get_status("5511005")
        assert entry["agent"] == "Maria"

    def test_get_status_none_when_bot(self):
        assert self.h.get_status("naoexiste") is None

# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK PARSER
# ─────────────────────────────────────────────────────────────────────────────
class TestWebhookParser:
    def setup_method(self):
        from app.webhook_parser import parse_whapi_webhook
        self.parse = parse_whapi_webhook

    def _text_payload(self, text, from_me=False, msg_id="test-id-001"):
        return {"messages": [{"id": msg_id, "from_me": from_me, "type": "text",
            "timestamp": 1000, "chat_id": "5511999@s.whatsapp.net",
            "from": "5511999", "_vname": "Teste",
            "text": {"body": text}}]}

    def _button_payload(self, selected_id, title):
        return {"messages": [{"id": "btn-001", "from_me": False, "type": "interactive",
            "timestamp": 1000, "chat_id": "5511999@s.whatsapp.net",
            "from": "5511999", "_vname": "Teste",
            "interactive": {"type": "button_reply",
                            "button_reply": {"id": selected_id, "title": title}}}]}

    def test_parses_text_message(self):
        msgs = self.parse(self._text_payload("Olá"))
        assert len(msgs) == 1
        assert msgs[0].text == "Olá"
        assert msgs[0].kind == "text"

    def test_ignores_from_me(self):
        msgs = self.parse(self._text_payload("msg bot", from_me=True))
        assert len(msgs) == 0

    def test_parses_button(self):
        msgs = self.parse(self._button_payload("id1", "Limpeza"))
        assert len(msgs) == 1
        assert msgs[0].kind == "button"
        assert msgs[0].selected_id == "id1"
        assert msgs[0].selected_title == "Limpeza"

    def test_empty_payload(self):
        assert self.parse({}) == []
        assert self.parse({"messages": []}) == []

    def test_chat_id_preserved(self):
        msgs = self.parse(self._text_payload("oi"))
        assert msgs[0].chat_id == "5511999@s.whatsapp.net"

    def test_from_name_extracted(self):
        msgs = self.parse(self._text_payload("oi"))
        assert msgs[0].from_name == "Teste"

# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE BUFFER
# ─────────────────────────────────────────────────────────────────────────────
class TestMessageBuffer:
    def _make_msg(self, numero, kind="text", text=None, selected_id=None):
        from app.message_buffer import BufferedMessage
        return BufferedMessage(
            numero=numero, phone=numero, from_name="Teste",
            chat_id=f"{numero}@s.whatsapp.net", message_id=f"msg-{time.time()}",
            timestamp=0, kind=kind, text=text,
            selected_id=selected_id, selected_title=selected_id,
        )

    def test_texts_concatenated(self):
        from app.message_buffer import MessageBuffer
        results = []
        buf = MessageBuffer(handler=lambda w, c, m: results.append(m), whapi=None, crm=None)

        async def run():
            from app.message_buffer import FLUSH_DELAY_SEC
            buf.push(self._make_msg("5511001", text="Oi"))
            buf.push(self._make_msg("5511001", text="quero limpeza"))
            await asyncio.sleep(FLUSH_DELAY_SEC + 0.5)

        asyncio.run(run())
        assert len(results) == 1
        assert results[0].text == "Oi quero limpeza"

    def test_button_wins_over_text(self):
        from app.message_buffer import MessageBuffer
        results = []
        buf = MessageBuffer(handler=lambda w, c, m: results.append(m), whapi=None, crm=None)

        async def run():
            from app.message_buffer import FLUSH_DELAY_SEC
            buf.push(self._make_msg("5511002", kind="text", text="contratar"))
            buf.push(self._make_msg("5511002", kind="button", selected_id="id1"))
            await asyncio.sleep(FLUSH_DELAY_SEC + 0.5)

        asyncio.run(run())
        assert len(results) == 1
        assert results[0].kind == "button"
        assert results[0].selected_id == "id1"

    def test_different_numbers_independent(self):
        from app.message_buffer import MessageBuffer
        results = []
        buf = MessageBuffer(handler=lambda w, c, m: results.append(m), whapi=None, crm=None)

        async def run():
            from app.message_buffer import FLUSH_DELAY_SEC
            buf.push(self._make_msg("5511003", text="A"))
            buf.push(self._make_msg("5511004", text="B"))
            await asyncio.sleep(FLUSH_DELAY_SEC + 0.5)

        asyncio.run(run())
        assert len(results) == 2

    def test_timer_reset_on_new_msg(self):
        from app.message_buffer import MessageBuffer
        results = []
        buf = MessageBuffer(handler=lambda w, c, m: results.append(m), whapi=None, crm=None)

        async def run():
            from app.message_buffer import FLUSH_DELAY_SEC
            buf.push(self._make_msg("5511005", text="msg1"))
            await asyncio.sleep(FLUSH_DELAY_SEC - 0.5)
            buf.push(self._make_msg("5511005", text="msg2"))  # reinicia timer
            await asyncio.sleep(0.3)
            assert len(results) == 0  # ainda não flushou
            await asyncio.sleep(FLUSH_DELAY_SEC)

        asyncio.run(run())
        assert len(results) == 1
        assert "msg1" in results[0].text
        assert "msg2" in results[0].text

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
class TestConfig:
    def test_loads_from_env(self):
        from app.config import load_settings
        s = load_settings()
        assert s.whapi_token, "WHAPI_TOKEN ausente"
        assert s.supabase_api_key, "SUPABASE_API_KEY ausente"
        assert s.pipeline_id, "PIPELINE_ID ausente"
        assert s.stage_id, "STAGE_ID ausente"
        assert s.stage_id_bot == "62d242da-2483-46a7-8d6e-d24043ce05ba"
        assert s.stage_id_emprego == "be979158-7b03-455b-881f-ec493ef4e79f"
        assert s.maton_api_key, "MATON_API_KEY ausente"
        # Garante que MATON_API_KEY não tem URL concatenada
        assert "script.google.com" not in s.maton_api_key, \
            "⚠️ MATON_API_KEY tem URL do Apps Script concatenada!"
        assert "https://" not in s.maton_api_key, \
            "⚠️ MATON_API_KEY contém URL inválida!"

# ─────────────────────────────────────────────────────────────────────────────
# HANDLER — máquina de estado (mocks)
# ─────────────────────────────────────────────────────────────────────────────
class TestHandlerStateMachine:
    """Testa a máquina de estado sem fazer chamadas reais à Whapi/CRM."""

    def setup_method(self):
        import app.handler as h
        import app.handoff as hf
        h._CONVERSATION_STATE.clear()
        hf._HANDOFF_STATE.clear()
        self.h = h
        self.hf = hf

    def _make_msg(self, kind="text", text=None, selected_id=None, selected_title=None,
                  phone="5511888001", chat_id="5511888001@s.whatsapp.net"):
        from unittest.mock import MagicMock
        from app.webhook_parser import InboundMessage
        msg = MagicMock(spec=InboundMessage)
        msg.message_id = f"mock-{time.time()}"
        msg.phone = phone
        msg.from_name = "Teste Mock"
        msg.chat_id = chat_id
        msg.timestamp = int(time.time())
        msg.kind = kind
        msg.text = text
        msg.selected_id = selected_id
        msg.selected_title = selected_title
        msg.raw = {}
        return msg

    def _make_deps(self, card_stage=None):
        from unittest.mock import MagicMock
        whapi = MagicMock()
        whapi.send_text.return_value = {"sent": True}
        whapi.send_buttons.return_value = {"sent": True}
        whapi.get_last_message.return_value = {"from_me": True, "source": "api"}

        crm = MagicMock()
        crm.card_exists.return_value = False
        crm.create_card.return_value = "card-mock-001"
        crm.get_card_stage.return_value = card_stage or "62d242da-2483-46a7-8d6e-d24043ce05ba"
        crm.update_card.return_value = None
        crm.move_card.return_value = None
        return whapi, crm

    def test_new_lead_creates_card(self):
        whapi, crm = self._make_deps()
        msg = self._make_msg(text="Oi")
        self.h.handle_incoming_message(whapi, crm, msg)
        crm.create_card.assert_called_once()

    def test_state_advances_to_welcome(self):
        whapi, crm = self._make_deps()
        msg = self._make_msg(text="Oi")
        self.h.handle_incoming_message(whapi, crm, msg)
        estado = self.h._CONVERSATION_STATE.get("5511888001", {})
        assert estado.get("state") == "welcome"

    def test_button_contratar_advances_to_pj(self):
        whapi, crm = self._make_deps()
        phone = "5511888002"
        self.h._CONVERSATION_STATE[phone] = {"state": "welcome", "card_id": "card-001"}
        crm.get_card_stage.return_value = "62d242da-2483-46a7-8d6e-d24043ce05ba"
        msg = self._make_msg(kind="button", selected_id="id1",
                             selected_title="Quero contratar",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        estado = self.h._CONVERSATION_STATE.get(phone, {})
        assert estado.get("state") == "pj"

    def test_button_emprego_concludes(self):
        whapi, crm = self._make_deps()
        phone = "5511888003"
        self.h._CONVERSATION_STATE[phone] = {"state": "welcome", "card_id": "card-001"}
        msg = self._make_msg(kind="button", selected_id="id2",
                             selected_title="Quero um emprego",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        # estado removido da memória (concluído)
        assert phone not in self.h._CONVERSATION_STATE

    def test_handoff_silences_bot(self):
        whapi, crm = self._make_deps()
        phone = "5511888004"
        self.h._CONVERSATION_STATE[phone] = {"state": "welcome", "card_id": "card-001"}
        self.hf.take(phone, agent="Atendente")
        msg = self._make_msg(kind="button", selected_id="id1",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        whapi.send_text.assert_not_called()
        whapi.send_buttons.assert_not_called()

    def test_wrong_stage_silences_bot(self):
        whapi, crm = self._make_deps(card_stage="outro-stage-qualquer")
        phone = "5511888005"
        self.h._CONVERSATION_STATE[phone] = {"state": "welcome", "card_id": "card-001"}
        msg = self._make_msg(text="oi", phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        whapi.send_text.assert_not_called()

    def test_human_last_message_silences_bot(self):
        whapi, crm = self._make_deps()
        whapi.get_last_message.return_value = {"from_me": True, "source": "web"}  # humano pelo app
        phone = "5511888006"
        self.h._CONVERSATION_STATE[phone] = {"state": "welcome", "card_id": "card-001"}
        msg = self._make_msg(text="oi", phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        whapi.send_text.assert_not_called()
        whapi.send_buttons.assert_not_called()

    def test_pj_sim_advances_to_servicos(self):
        whapi, crm = self._make_deps()
        phone = "5511888007"
        self.h._CONVERSATION_STATE[phone] = {"state": "pj", "card_id": "card-001"}
        msg = self._make_msg(kind="button", selected_id="id1",
                             selected_title="Sim",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        estado = self.h._CONVERSATION_STATE.get(phone, {})
        assert estado.get("state") == "servicos"

    def test_servico_chosen_advances_to_cidade(self):
        whapi, crm = self._make_deps()
        phone = "5511888008"
        self.h._CONVERSATION_STATE[phone] = {"state": "servicos", "card_id": "card-001"}
        msg = self._make_msg(kind="button", selected_id="id1",
                             selected_title="Limpeza",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        estado = self.h._CONVERSATION_STATE.get(phone, {})
        assert estado.get("state") == "cidade"
        assert estado.get("servico") == "Limpeza"

    def test_cidade_text_advances_to_cnpj(self):
        whapi, crm = self._make_deps()
        phone = "5511888009"
        self.h._CONVERSATION_STATE[phone] = {"state": "cidade", "card_id": "card-001"}
        msg = self._make_msg(text="São Paulo - Vila Mariana",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        estado = self.h._CONVERSATION_STATE.get(phone, {})
        assert estado.get("state") == "cnpj"
        assert estado.get("cidade") == "São Paulo - Vila Mariana"

    def test_cnpj_text_advances_to_canal(self):
        whapi, crm = self._make_deps()
        phone = "5511888010"
        self.h._CONVERSATION_STATE[phone] = {"state": "cnpj", "card_id": "card-001"}
        msg = self._make_msg(text="02.617.943/0001-93",
                             phone=phone, chat_id=f"{phone}@s.whatsapp.net")
        self.h.handle_incoming_message(whapi, crm, msg)
        estado = self.h._CONVERSATION_STATE.get(phone, {})
        assert estado.get("state") == "canal"
        assert estado.get("cnpj") == "02.617.943/0001-93"


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE — load_active_states (restore no startup)
# ─────────────────────────────────────────────────────────────────────────────
class TestLoadActiveStates:
    """Testa o restore de estado do SQLite no startup."""

    def setup_method(self):
        from app.database import Database
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.tmp.name)
        self.db.init()

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_returns_empty_when_no_leads(self):
        result = self.db.load_active_states()
        assert result == {}

    def test_restores_active_lead(self):
        ctx = {"state": "cnpj", "card_id": "c-001", "servico": "Limpeza", "cidade": "SP"}
        self.db.upsert_lead("5511001", "Ana", ctx)
        result = self.db.load_active_states()
        assert "5511001" in result
        assert result["5511001"]["state"] == "cnpj"
        assert result["5511001"]["servico"] == "Limpeza"

    def test_excludes_concluido_state(self):
        self.db.upsert_lead("5511002", "Bob", {"state": "concluido", "card_id": "c-002"})
        result = self.db.load_active_states()
        assert "5511002" not in result

    def test_excludes_new_state(self):
        self.db.upsert_lead("5511003", "Cris", {"state": "new"})
        result = self.db.load_active_states()
        assert "5511003" not in result

    def test_restores_multiple_active_leads(self):
        for i, state in enumerate(["welcome", "pj", "servicos", "cidade", "canal"]):
            ctx = {"state": state, "card_id": f"c-{i}"}
            self.db.upsert_lead(f"551100{i}", f"Lead{i}", ctx)
        self.db.upsert_lead("5511099", "Fim", {"state": "concluido", "card_id": "c-99"})
        result = self.db.load_active_states()
        assert len(result) == 5
        assert "5511099" not in result

    def test_restores_ctx_json_faithfully(self):
        ctx = {"state": "canal", "card_id": "c-abc", "cnpj": "00.000.000/0001-00",
               "cidade": "Campinas", "servico": "Portaria", "canal": "WhatsApp"}
        self.db.upsert_lead("5511010", "Duda", ctx)
        result = self.db.load_active_states()
        restored = result["5511010"]
        assert restored["cnpj"] == "00.000.000/0001-00"
        assert restored["cidade"] == "Campinas"
        assert restored["canal"] == "WhatsApp"

    def test_ignores_null_ctx_json(self):
        # Insere diretamente no banco sem ctx_json (simula banco antigo)
        import sqlite3
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute("""
            INSERT INTO leads (numero, nome, state, ciclo, criado_em, atualizado_em)
            VALUES ('5511020', 'Sem CTX', 'cidade', 4, ?, ?)
        """, (now, now))
        self.db.conn.commit()
        # Não deve lançar erro
        result = self.db.load_active_states()
        assert "5511020" not in result  # ctx_json IS NULL → excluído


# ─────────────────────────────────────────────────────────────────────────────
# HANDLER — restore_state_from_db + fix duplicação canal
# ─────────────────────────────────────────────────────────────────────────────
class TestRestoreStateFromDb:
    """Testa que restore_state_from_db recarrega _CONVERSATION_STATE no startup."""

    def setup_method(self):
        import app.handler as h
        import importlib
        importlib.reload(h)  # limpa estado global entre testes
        self.h = h
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        from app.database import Database
        self.db = Database(self.tmp.name)
        self.db.init()
        self.h.set_database(self.db)

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_restore_populates_conversation_state(self):
        ctx = {"state": "cnpj", "card_id": "c-01", "servico": "Limpeza", "cidade": "SP"}
        self.db.upsert_lead("5511901", "Ana", ctx)
        n = self.h.restore_state_from_db()
        assert n == 1
        assert "5511901" in self.h._CONVERSATION_STATE
        assert self.h._CONVERSATION_STATE["5511901"]["state"] == "cnpj"

    def test_restore_does_not_overwrite_live_state(self):
        """Estado em memória tem prioridade sobre o banco."""
        ctx_db  = {"state": "cnpj",   "card_id": "c-01"}
        ctx_mem = {"state": "canal",  "card_id": "c-01"}
        self.db.upsert_lead("5511902", "Bob", ctx_db)
        self.h._CONVERSATION_STATE["5511902"] = ctx_mem  # já existe em memória
        self.h.restore_state_from_db()
        # Memória não foi sobrescrita
        assert self.h._CONVERSATION_STATE["5511902"]["state"] == "canal"

    def test_restore_excludes_concluido(self):
        self.db.upsert_lead("5511903", "Cris", {"state": "concluido", "card_id": "c-02"})
        n = self.h.restore_state_from_db()
        assert n == 0
        assert "5511903" not in self.h._CONVERSATION_STATE

    def test_restore_returns_count(self):
        for i in range(3):
            ctx = {"state": "servicos", "card_id": f"c-{i}"}
            self.db.upsert_lead(f"551191{i}", f"Lead{i}", ctx)
        n = self.h.restore_state_from_db()
        assert n == 3


class TestCanalDuplicacaoFix:
    """Garante que _handle_button_canal não faz chamadas duplicadas ao Sheets/DB."""

    def setup_method(self):
        import app.handler as h
        import importlib
        importlib.reload(h)
        self.h = h

    def _make_msg(self, selected_id, phone="5511700001"):
        class FakeMsg:
            pass
        m = FakeMsg()
        m.kind = "button"
        m.selected_id = selected_id
        m.phone = phone
        m.from_name = "Teste"
        m.chat_id = f"{phone}@s.whatsapp.net"
        m.message_id = f"mid-{selected_id}"
        m.text = None
        m.timestamp = 0
        return m

    def test_canal_whatsapp_sheets_chamado_uma_vez(self):
        """Sheets e DB devem ser chamados exatamente 1 vez ao escolher WhatsApp."""
        from unittest.mock import MagicMock, patch
        sheets_calls = []
        db_calls = []

        with patch.object(self.h, "_update_sheets", side_effect=lambda *a: sheets_calls.append(a)), \
             patch.object(self.h, "_save_db",       side_effect=lambda *a: db_calls.append(a)), \
             patch.object(self.h, "_update_crm",    return_value=None):

            phone = "5511700002"
            ctx = {"state": "canal", "card_id": "c-xyz", "cnpj": "00.000.000/0001-00"}
            self.h._CONVERSATION_STATE[phone] = ctx
            msg = self._make_msg("id1", phone=phone)

            # Chama diretamente o handler interno
            crm_mock = MagicMock()
            self.h._handle_button_canal(
                whapi=MagicMock(), crm=crm_mock, msg=msg, numero=phone, ctx=ctx
            )

        assert len(sheets_calls) == 1, f"Esperado 1 chamada ao Sheets, got {len(sheets_calls)}"
        assert len(db_calls) == 1, f"Esperado 1 chamada ao DB, got {len(db_calls)}"

    def test_canal_email_sheets_chamado_uma_vez(self):
        """Escolher E-mail também deve chamar Sheets/DB exatamente 1 vez."""
        from unittest.mock import MagicMock, patch
        sheets_calls = []
        db_calls = []

        with patch.object(self.h, "_update_sheets", side_effect=lambda *a: sheets_calls.append(a)), \
             patch.object(self.h, "_save_db",       side_effect=lambda *a: db_calls.append(a)), \
             patch.object(self.h, "_update_crm",    return_value=None):

            phone = "5511700003"
            ctx = {"state": "canal", "card_id": "c-xyz"}
            self.h._CONVERSATION_STATE[phone] = ctx
            msg = self._make_msg("id2", phone=phone)
            whapi_mock = MagicMock()

            self.h._handle_button_canal(
                whapi=whapi_mock, crm=MagicMock(), msg=msg, numero=phone, ctx=ctx
            )

        assert len(sheets_calls) == 1
        assert len(db_calls) == 1

