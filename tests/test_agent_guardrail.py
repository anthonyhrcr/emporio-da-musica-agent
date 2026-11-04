"""The guardrails, tested with a scripted fake model (no LLM needed).

Every case here replays a failure seen in the example runs (README, section 6):
saxophones confirmed without a search, an invented address, Saturday hours
answered without the hours section, a correct follow-up wrongly blocked, and a
mis-copied price.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from emporio.agent import UNGROUNDED_FALLBACK, Agent, absent_instruments, needs_grounding, untraceable_values
from emporio.config import STORE_TZ, Settings
from emporio.data import load_store
from emporio.policies import Policies

DATA = Path(__file__).resolve().parents[1] / "data"


def text(content):
    return SimpleNamespace(content=content, tool_calls=None)


def call(name, **arguments):
    fn = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return SimpleNamespace(content="", tool_calls=[SimpleNamespace(id=f"call_{name}", function=fn)])


class ScriptedModel:
    """Returns the scripted messages in order and records what it was sent."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.messages.pop(0))])

    def saw(self, request: int, fragment: str) -> bool:
        return any(fragment in str(m.get("content")) for m in self.requests[request]["messages"])


@pytest.fixture(scope="module")
def store():
    return load_store(DATA)


@pytest.fixture(scope="module")
def policies():
    return Policies.from_pdf(DATA / "politicas_da_loja.pdf")


def agent_with(model, store, policies, tmp_path):
    settings = Settings(base_url="", api_key="", model="fake", temperature=0, reasoning_effort=None,
                        data_dir=DATA, runtime_dir=tmp_path,
                        store_now=datetime(2026, 3, 25, 10, 30, tzinfo=STORE_TZ))
    return Agent(settings, store, policies, client=model)


# --- 1. Looked up? -----------------------------------------------------------------

def test_product_claim_without_search_triggers_an_automatic_search(store, policies, tmp_path):
    # Run 1: "Tem sim! Temos vários modelos de saxofone." with no tool call. The store has none.
    model = ScriptedModel(
        text("Tem sim! Temos vários modelos de saxofone."),
        text("No momento não temos saxofones à venda."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Vocês têm saxofone?")
    assert turn.reply == "No momento não temos saxofones à venda."
    assert [(c.name, c.automatic) for c in turn.tool_calls] == [("search_products", True)]
    assert model.saw(1, "Nenhum produto à venda")  # the model answered with the search result in context


def test_every_topic_asked_must_be_looked_up(store, policies, tmp_path):
    # Run 2: address looked up, Saturday hours answered from memory (9h-18h; real 9h-13h).
    model = ScriptedModel(
        call("get_policy", secao="sobre_a_loja_e_contato"),
        text("Fica na Rua 14 de Maio, 3200. Sábado abrimos das 9h às 18h."),
        text("Fica na Rua 14 de Maio, 3200. Sábado abrimos das 9h às 13h."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Qual o endereço da loja? Vocês abrem sábado?")
    assert turn.reply.endswith("das 9h às 13h.")
    assert ("get_policy", {"secao": "horario_de_funcionamento"}) in [
        (c.name, c.arguments) for c in turn.tool_calls if c.automatic]
    assert model.saw(2, "Sábado 09:00 às 13:00")


def test_follow_up_answered_from_an_earlier_lookup_is_accepted(store, policies, tmp_path):
    # Run 2: "e no PIX?" was already answered by turn 1's lookup, yet was blocked.
    model = ScriptedModel(
        call("get_product_details", produto="Takamine GD20"),
        text("O Takamine GD20 sai por R$ 2.199,00."),
        text("No PIX fica R$ 2.089,05, e dá para fazer 12x de R$ 183,25."),
    )
    agent = agent_with(model, store, policies, tmp_path)
    agent.reply("Quanto custa o Takamine GD20?")
    turn = agent.reply("E no PIX sai quanto? Dá pra parcelar em 12x?")
    assert turn.guardrail is None and not turn.tool_calls
    assert turn.reply.startswith("No PIX fica R$ 2.089,05")


def test_order_question_without_contact_gets_a_specific_instruction(store, policies, tmp_path):
    # No automatic lookup is possible without the customer's contact, so the model is told what to do.
    model = ScriptedModel(text("Seu pedido já saiu para entrega!"), text("Claro! Me passa o seu telefone com DDD?"))
    turn = agent_with(model, store, policies, tmp_path).reply("Cadê meu pedido?")
    assert not turn.tool_calls
    assert model.saw(1, "lookup_orders")
    assert "telefone" in turn.reply


def test_asking_for_contact_before_an_order_lookup_is_allowed(store, policies, tmp_path):
    model = ScriptedModel(text("Claro! Me passa o seu telefone com DDD ou o e-mail usado na compra?"))
    turn = agent_with(model, store, policies, tmp_path).reply("Posso devolver meu pedido?")
    assert turn.guardrail is None and "telefone" in turn.reply


def test_small_talk_is_not_forced_through_tools(store, policies, tmp_path):
    model = ScriptedModel(text("Só consigo ajudar com assuntos da Empório da Música!"))
    turn = agent_with(model, store, policies, tmp_path).reply("Quem ganhou o jogo do Flamengo ontem?")
    assert turn.guardrail is None and not turn.tool_calls


@pytest.mark.parametrize("message, expected", [
    ("Vocês têm saxofone?", True),
    ("Tem o Giannini GF-3D?", True),
    ("Qual o endereço? Abre sábado?", True),
    ("Quanto custa?", True),
    ("Vocês vendem cordas e amplificador?", False),  # scope rule in the prompt, no store fact to look up
    ("Obrigado, valeu!", False),
])
def test_what_counts_as_a_store_fact(store, message, expected):
    assert needs_grounding(message, store) is expected


# --- 2. Traceable? -------------------------------------------------------------------

def test_miscopied_price_is_caught_and_redone(store, policies, tmp_path):
    # Run 2: R$ 849,00 in the tool, R$ 849,90 in the reply.
    model = ScriptedModel(
        call("search_products", termo="violão nylon", preco_max=1000),
        text("Recomendo o Yamaha C70 Nylon Natural por R$ 849,90."),
        text("Recomendo o Yamaha C70 Nylon Natural por R$ 849,00."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Qual violão de nylon até R$1000 você indica?")
    assert turn.reply.endswith("R$ 849,00.")
    assert "849,90" in turn.guardrail
    assert model.saw(2, "R$ 849,90")  # the retry instruction names the wrong value


def test_invented_address_never_reaches_the_customer(store, policies, tmp_path):
    # Run 1: "Rua das Flores, 123". Even after the automatic lookup, an insisting model is stopped.
    model = ScriptedModel(
        text("Fica na Rua das Flores, 123."),
        text("Fica na Rua das Flores, 123!"),
        text("Rua das Flores, 123, pode confiar."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Qual o endereço da loja?")
    assert turn.reply == UNGROUNDED_FALLBACK
    assert [c.name for c in turn.tool_calls] == ["get_policy"]


def test_wrong_value_that_persists_is_never_shown(store, policies, tmp_path):
    model = ScriptedModel(
        call("get_product_details", produto="Takamine GD20"),
        text("Sai por R$ 1.999,00 no PIX."),
        text("Sai por R$ 1.999,00 no PIX, confirmado."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Quanto custa o Takamine GD20 no PIX?")
    assert turn.reply == UNGROUNDED_FALLBACK


def test_values_from_tools_customer_and_clock_are_traceable():
    sources = 'tool: {"preco_no_pix": "R$ 2.089,05"}\ncliente: até R$1000\nAgora: 25/03/2026 10:30'
    assert untraceable_values("PIX: R$ 2.089,05, até R$ 1.000,00, hoje 25/03/2026", sources) == []
    assert untraceable_values("PIX: R$ 2.089,50 até 26/03/2026", sources) == ["R$ 2.089,50", "26/03/2026"]


def test_phone_numbers_and_addresses_must_be_traceable():
    sources = "Endereço Rua 14 de Maio, 3200 — Centro, Campo Grande. Telefone / WhatsApp (67) 3341-4444"
    assert untraceable_values("Fica na Rua 14 de Maio, nº 3200. WhatsApp (67) 3341-4444.", sources) == []
    assert untraceable_values("Fica na Rua das Flores, 123. WhatsApp (67) 3333-0000.", sources) == [
        "(67) 3333-0000", "Rua das Flores, 123"]
    # A real street number at the start of the name must not let a different street through.
    assert untraceable_values("Fica na Rua 14 de Julho, 10.", sources) == ["Rua 14 de Julho, 10"]


def test_offering_instruments_the_store_has_none_of_is_caught(store, policies, tmp_path):
    # Runs 2-4: "não temos saxofones, mas posso te ajudar com trompetes ou trombones" right after the
    # search said both wind-instrument categories are empty.
    model = ScriptedModel(
        call("search_products", termo="saxofone"),
        text("Poxa, não temos saxofones. Posso te ajudar com trompetes ou trombones!"),
        text("Poxa, não temos saxofones à venda no momento. Posso te ajudar com outro instrumento?"),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Vocês têm saxofone?")
    assert "trompetes" not in turn.reply and "saxofones" in turn.reply
    assert "trompetes, trombones" in turn.guardrail


def test_echoing_the_customers_own_word_in_a_negation_is_fine():
    absent = {"saxofone", "saxofones", "trompete", "trompetes", "clarinete", "clarinetes"}
    assert absent_instruments("Não temos saxofones no momento.", "Vocês têm saxofone?", absent) == []
    assert absent_instruments("Mas temos clarinetes!", "Vocês têm saxofone?", absent) == ["clarinetes"]
