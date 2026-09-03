"""
test_qualification_flow.py

Testes de integração do fluxo completo de qualificação de leads.
Simula uma conversa real do WhatsApp do início ao fim, verificando
cada etapa da máquina de estado, persistência no banco e chamadas ao CRM.
"""
import os, sys, time, tempfile
import pytest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import Database
from app.webhook_parser import InboundMessage
import app.handler as handler
import app.handoff as handoff


# ─── Fixtures ────────────────────────────────────────────────────────────────

def make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Database(tmp.name)
    db.init()
    return db, tmp.name

def make_whapi(last_source="api", db=None):
    from app.whapi_client import WhapiClient
    w = WhapiClient(token="fake-token")
    if db:
        w.set_database(db)
    # Mock dos métodos HTTP internos
    w._post = MagicMock(return_value={"message": {"id": "mock-out-001"}, "sent": True})
    w.get_last_message = MagicMock(return_value={"from_me": True, "source": last_source})
    # Spy nos métodos de envio para permitir assert_called / reset_mock
    w.send_text    = MagicMock(wraps=w.send_text)
    w.send_buttons = MagicMock(wraps=w.send_buttons)
    w.send_list    = MagicMock(wraps=w.send_list)
    # Adiciona reset_mock helper
    def reset_mock():
        w._post.reset_mock()
        w.send_text.reset_mock()
        w.send_buttons.reset_mock()
        w.send_list.reset_mock()
    w.reset_mock = reset_mock
    return w

def make_crm(stage="62d242da-2483-46a7-8d6e-d24043ce05ba"):
    c = MagicMock()
    c.card_exists.return_value = False
    c.create_card.return_value = "card-test-001"
    c.get_card_stage.return_value = stage
    c.update_card.return_value = None
    c.move_card.return_value = None
    return c

def make_msg(phone, kind="text", text=None, selected_id=None, selected_title=None):
    m = MagicMock(spec=InboundMessage)
    m.message_id = f"mid-{time.time_ns()}"
    m.phone = phone
    m.from_name = "Lead Teste"
    m.chat_id = f"{phone}@s.whatsapp.net"
    m.timestamp = int(time.time())
    m.kind = kind
    m.text = text
    m.selected_id = selected_id
    m.selected_title = selected_title
    m.raw = {}
    return m

def send(whapi, crm, phone, kind="text", text=None, sid=None, stitle=None):
    """Envia uma mensagem para o handler e retorna o estado atual."""
    handler.handle_incoming_message(whapi, crm, make_msg(phone, kind, text, sid, stitle))
    return handler._CONVERSATION_STATE.get(phone, {})


# ─── Setup / Teardown global ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    handler._CONVERSATION_STATE.clear()
    handoff._HANDOFF_STATE.clear()
    yield
    handler._CONVERSATION_STATE.clear()
    handoff._HANDOFF_STATE.clear()


# ─────────────────────────────────────────────────────────────────────────────
# FLUXO A — Caminho feliz completo: WhatsApp
# new → welcome → pj → servicos → cidade → cnpj → canal → concluido
# ─────────────────────────────────────────────────────────────────────────────

class TestFluxoCompletoWhatsApp:

    def setup_method(self):
        self.db, self.tmp = make_db()
        self.whapi = make_whapi(db=self.db)
        self.crm = make_crm()
        handler.set_database(self.db)
        handler._CONVERSATION_STATE.clear()
        self.phone = "5511700001"

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp)

    def test_etapa1_boas_vindas_cria_card(self):
        ctx = send(self.whapi, self.crm, self.phone, text="Oi")
        assert ctx["state"] == "welcome"
        self.crm.create_card.assert_called_once()
        assert ctx.get("card_id") == "card-test-001"

    def test_etapa1_salva_lead_no_banco(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        lead = self.db.get_lead(self.phone)
        assert lead is not None
        assert lead["state"] == "welcome"
        assert lead["ciclo"] == 1

    def test_etapa2_contratar_vai_para_pj(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        ctx = send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Quero contratar")
        assert ctx["state"] == "pj"

    def test_etapa3_pj_sim_vai_para_servicos(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Contratar")
        ctx = send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        assert ctx["state"] == "servicos"

    def test_etapa4_escolhe_servico_vai_para_cidade(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        ctx = send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Limpeza")
        assert ctx["state"] == "cidade"
        assert ctx["servico"] == "Limpeza"

    def test_etapa5_cidade_vai_para_cnpj(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Limpeza")
        ctx = send(self.whapi, self.crm, self.phone, text="São Paulo - Vila Mariana")
        assert ctx["state"] == "cnpj"
        assert ctx["cidade"] == "São Paulo - Vila Mariana"

    def test_etapa6_cnpj_vai_para_canal(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Limpeza")
        send(self.whapi, self.crm, self.phone, text="São Paulo - Vila Mariana")
        ctx = send(self.whapi, self.crm, self.phone, text="02.617.943/0001-93")
        assert ctx["state"] == "canal"
        assert ctx["cnpj"] == "02.617.943/0001-93"

    def test_etapa7_whatsapp_conclui_fluxo(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Limpeza")
        send(self.whapi, self.crm, self.phone, text="São Paulo - Vila Mariana")
        send(self.whapi, self.crm, self.phone, text="02.617.943/0001-93")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="WhatsApp")
        lead = self.db.get_lead(self.phone)
        assert lead["state"] == "concluido"
        assert lead["ciclo"] == 7
        assert lead["servico"] == "Limpeza"
        assert lead["cnpj"] == "02.617.943/0001-93"
        assert lead["canal"] == "WhatsApp"

    def test_etapa7_crm_atualizado_em_cada_etapa(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Portaria")
        send(self.whapi, self.crm, self.phone, text="Campinas Centro")
        send(self.whapi, self.crm, self.phone, text="02.617.943/0001-93")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="WhatsApp")
        assert self.crm.create_card.call_count == 1
        assert self.crm.update_card.call_count >= 5  # uma por etapa

    def test_historico_completo_salvo(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Contratar")
        conv = self.db.get_conversation(self.phone)
        assert len(conv) >= 4  # 2 msgs recebidas + 2 enviadas
        directions = [m["direction"] for m in conv]
        assert "in" in directions
        assert "out" in directions

    def test_from_name_salvo_no_historico(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        conv = self.db.get_conversation(self.phone)
        recebidas = [m for m in conv if m["direction"] == "in"]
        assert recebidas[0]["from_name"] == "Lead Teste"
        enviadas = [m for m in conv if m["direction"] == "out"]
        assert enviadas[0]["from_name"] == "Bot"


# ─────────────────────────────────────────────────────────────────────────────
# FLUXO B — Caminho feliz completo: E-mail
# ─────────────────────────────────────────────────────────────────────────────

class TestFluxoCompletoEmail:

    def setup_method(self):
        self.db, self.tmp = make_db()
        self.whapi = make_whapi()
        self.crm = make_crm()
        handler.set_database(self.db)
        handler._CONVERSATION_STATE.clear()
        self.phone = "5511700002"

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp)

    def _run_until_canal(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        send(self.whapi, self.crm, self.phone, "button", sid="id2", stitle="Portaria")
        send(self.whapi, self.crm, self.phone, text="Rio de Janeiro - Centro")
        send(self.whapi, self.crm, self.phone, text="02.617.943/0001-93")

    def test_canal_email_pede_endereco(self):
        self._run_until_canal()
        ctx = send(self.whapi, self.crm, self.phone, "button", sid="id2", stitle="E-mail")
        assert ctx["state"] == "aguarda_email"
        assert ctx["canal"] == "E-mail"

    def test_email_digitado_conclui_fluxo(self):
        self._run_until_canal()
        send(self.whapi, self.crm, self.phone, "button", sid="id2", stitle="E-mail")
        send(self.whapi, self.crm, self.phone, text="contato@empresa.com.br")
        lead = self.db.get_lead(self.phone)
        assert lead["state"] == "concluido"
        assert lead["email"] == "contato@empresa.com.br"
        assert lead["canal"] == "E-mail"


# ─────────────────────────────────────────────────────────────────────────────
# FLUXO C — Desvios e casos de borda
# ─────────────────────────────────────────────────────────────────────────────

class TestFluxosDesvio:

    def setup_method(self):
        self.db, self.tmp = make_db()
        self.whapi = make_whapi()
        self.crm = make_crm()
        handler.set_database(self.db)
        handler._CONVERSATION_STATE.clear()
        self.phone = "5511700003"

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp)

    def test_emprego_conclui_sem_qualificacao(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id2", stitle="Emprego")
        assert self.phone not in handler._CONVERSATION_STATE  # estado removido
        lead = self.db.get_lead(self.phone)
        assert lead["state"] == "concluido"
        assert lead["interesse"] == "emprego"

    def test_emprego_move_card_para_stage_emprego(self):
        handler.set_stage_ids(emprego="stage-emprego-test", bot="62d242da-2483-46a7-8d6e-d24043ce05ba")
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id2", stitle="Emprego")
        self.crm.move_card.assert_called_once_with("card-test-001", "stage-emprego-test")

    def test_nao_pj_conclui_com_mensagem_encerramento(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id2", stitle="Não")
        assert self.phone not in handler._CONVERSATION_STATE
        lead = self.db.get_lead(self.phone)
        assert lead["state"] == "concluido"

    def test_todos_servicos_sao_aceitos(self):
        servicos = [
            ("id1", "Limpeza"), ("id2", "Portaria"), ("id3", "Recepção"),
            ("id4", "Copa"), ("id5", "Vigilância desarmada"),
        ]
        for sid, titulo in servicos:
            handler._CONVERSATION_STATE.clear()
            p = f"551170000{sid}"
            self.crm.create_card.return_value = f"card-{sid}"
            send(self.whapi, self.crm, p, text="Oi")
            send(self.whapi, self.crm, p, "button", sid="id1")
            send(self.whapi, self.crm, p, "button", sid="id1", stitle="Sim")
            ctx = send(self.whapi, self.crm, p, "button", sid=sid, stitle=titulo)
            assert ctx["state"] == "cidade", f"Falhou para serviço {titulo}"
            assert ctx["servico"] == titulo

    def test_mensagem_duplicada_ignorada(self):
        """Mesmo message_id enviado duas vezes não deve criar dois cards."""
        from app.dedup import MessageDedup
        d = MessageDedup(ttl=60)
        mid = "msg-dup-001"
        assert d.seen(mid) is False   # primeira vez
        assert d.seen(mid) is True    # segunda vez — duplicata

    def test_lead_reinicia_se_estado_new(self):
        """Lead sem estado prévio sempre começa do zero."""
        assert self.phone not in handler._CONVERSATION_STATE
        ctx = send(self.whapi, self.crm, self.phone, text="qualquer coisa")
        assert ctx["state"] == "welcome"

    def test_texto_ignorado_em_estado_servicos(self):
        """Texto livre em estado que espera botão deve ser ignorado silenciosamente."""
        send(self.whapi, self.crm, self.phone, text="Oi")
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        send(self.whapi, self.crm, self.phone, "button", sid="id1", stitle="Sim")
        # estado = servicos, envia texto em vez de botão
        ctx_antes = dict(handler._CONVERSATION_STATE.get(self.phone, {}))
        send(self.whapi, self.crm, self.phone, text="limpeza por favor")
        ctx_depois = handler._CONVERSATION_STATE.get(self.phone, {})
        assert ctx_depois.get("state") == ctx_antes.get("state"), \
            "Estado não deve mudar com texto em etapa de botão"

    def test_card_nao_criado_duas_vezes(self):
        """Re-envio de mensagem no state=welcome não recria o card."""
        send(self.whapi, self.crm, self.phone, text="Oi")
        self.crm.card_exists.return_value = True  # card já existe agora
        send(self.whapi, self.crm, self.phone, text="Oi de novo")
        assert self.crm.create_card.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# FLUXO D — Handoff e silenciamento
# ─────────────────────────────────────────────────────────────────────────────

class TestFluxoHandoff:

    def setup_method(self):
        self.db, self.tmp = make_db()
        self.whapi = make_whapi()
        self.crm = make_crm()
        handler.set_database(self.db)
        handler._CONVERSATION_STATE.clear()
        handoff._HANDOFF_STATE.clear()
        self.phone = "5511700010"

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp)

    def test_handoff_manual_silencia_bot(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        handoff.take(self.phone, agent="Atendente João")
        self.whapi.reset_mock()
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        self.whapi.send_text.assert_not_called()
        self.whapi.send_buttons.assert_not_called()

    def test_handoff_release_retoma_bot(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        handoff.take(self.phone)
        handoff.release(self.phone)
        self.whapi.reset_mock()
        send(self.whapi, self.crm, self.phone, "button", sid="id1")
        # Bot deve ter respondido após release
        assert self.whapi.send_buttons.called or self.whapi.send_text.called

    def test_stage_errado_silencia_bot(self):
        self.crm.get_card_stage.return_value = "outro-stage-qualquer"
        send(self.whapi, self.crm, self.phone, text="Oi")
        self.whapi.reset_mock()
        send(self.whapi, self.crm, self.phone, text="segunda mensagem")
        self.whapi.send_text.assert_not_called()

    def test_humano_no_chat_silencia_bot(self):
        whapi_humano = make_whapi(last_source="web")  # humano usou WhatsApp Web
        send(whapi_humano, self.crm, self.phone, text="Oi")
        whapi_humano.reset_mock()
        send(whapi_humano, self.crm, self.phone, text="segunda msg do lead")
        whapi_humano.send_text.assert_not_called()
        whapi_humano.send_buttons.assert_not_called()

    def test_handoff_loga_mensagens_mesmo_silenciado(self):
        send(self.whapi, self.crm, self.phone, text="Oi")
        handoff.take(self.phone)
        send(self.whapi, self.crm, self.phone, text="mensagem durante handoff")
        conv = self.db.get_conversation(self.phone)
        textos = [m["content"] for m in conv if m["direction"] == "in"]
        assert "mensagem durante handoff" in textos


# ─────────────────────────────────────────────────────────────────────────────
# FLUXO E — Múltiplos leads simultâneos
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiplosLeads:

    def setup_method(self):
        self.db, self.tmp = make_db()
        self.whapi = make_whapi()
        self.crm = make_crm()
        handler.set_database(self.db)
        handler._CONVERSATION_STATE.clear()

    def teardown_method(self):
        self.db.close()
        os.unlink(self.tmp)

    def test_estados_independentes_por_numero(self):
        p1, p2, p3 = "5511800001", "5511800002", "5511800003"
        self.crm.create_card.side_effect = ["card-p1", "card-p2", "card-p3"]

        send(self.whapi, self.crm, p1, text="Oi")  # p1: welcome
        send(self.whapi, self.crm, p2, text="Oi")  # p2: welcome
        send(self.whapi, self.crm, p2, "button", sid="id1")  # p2: pj
        send(self.whapi, self.crm, p3, text="Oi")  # p3: welcome
        send(self.whapi, self.crm, p3, "button", sid="id1")  # p3: pj
        send(self.whapi, self.crm, p3, "button", sid="id1", stitle="Sim")  # p3: servicos

        assert handler._CONVERSATION_STATE[p1]["state"] == "welcome"
        assert handler._CONVERSATION_STATE[p2]["state"] == "pj"
        assert handler._CONVERSATION_STATE[p3]["state"] == "servicos"

    def test_handoff_de_um_nao_afeta_outro(self):
        p1, p2 = "5511800010", "5511800011"
        self.crm.create_card.side_effect = ["card-a", "card-b"]

        send(self.whapi, self.crm, p1, text="Oi")
        send(self.whapi, self.crm, p2, text="Oi")

        handoff.take(p1)  # só p1 em handoff
        self.whapi.reset_mock()

        send(self.whapi, self.crm, p2, "button", sid="id1")  # p2 deve responder
        assert self.whapi.send_buttons.called, "p2 deveria receber resposta"

    def test_banco_registra_leads_separados(self):
        phones = [f"551190000{i}" for i in range(5)]
        self.crm.create_card.side_effect = [f"card-{i}" for i in range(5)]
        for p in phones:
            send(self.whapi, self.crm, p, text="Oi")
        leads = self.db.get_all_leads()
        numeros = [l["numero"] for l in leads]
        for p in phones:
            assert p in numeros

