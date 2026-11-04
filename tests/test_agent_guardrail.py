"""The grounding guardrail, tested with a scripted fake model (no LLM needed).

These cases reproduce the failures seen in the first example run:
qwen3:8b confirmed saxophones the store doesn't have, and invented an address.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from emporio.agent import UNGROUNDED_FALLBACK, Agent, needs_grounding, untraceable_values
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


def test_ungrounded_product_claim_is_discarded_and_redone(store, policies, tmp_path):
    model = ScriptedModel(
        text("Tem sim! Temos vários modelos de saxofone."),       # the real failure, no tool called
        call("search_products", termo="saxofone"),
        text("No momento não temos saxofones cadastrados."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Vocês têm saxofone?")
    assert turn.reply == "No momento não temos saxofones cadastrados."
    assert [c.name for c in turn.tool_calls] == ["search_products"]
    assert turn.guardrail is not None
    assert "descartada" in model.requests[1]["messages"][-1]["content"]


def test_miscopied_price_is_caught_and_redone(store, policies, tmp_path):
    # Real failure from the second example run: R$ 849,00 in the tool, R$ 849,90 in the reply.
    model = ScriptedModel(
        call("search_products", termo="violão nylon", preco_max=1000),
        text("Recomendo o Yamaha C70 Nylon Natural por R$ 849,90."),
        text("Recomendo o Yamaha C70 Nylon Natural por R$ 849,00."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Qual violão de nylon até R$1000 você indica?")
    assert turn.reply.endswith("R$ 849,00.")
    assert "849,90" in turn.guardrail
    assert "R$ 849,90" in model.requests[2]["messages"][-1]["content"]


def test_wrong_value_that_persists_is_never_shown(store, policies, tmp_path):
    model = ScriptedModel(
        call("get_product_details", produto="Takamine GD20"),
        text("Sai por R$ 1.999,00 no PIX."),
        text("Sai por R$ 1.999,00 no PIX, confirmado."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Quanto custa o Takamine GD20 no PIX?")
    assert turn.reply == UNGROUNDED_FALLBACK


def test_follow_up_answered_from_an_earlier_lookup_is_accepted(store, policies, tmp_path):
    # Run 2 failure: "e no PIX?" was already answered by turn 1's lookup, yet was discarded.
    model = ScriptedModel(
        call("get_product_details", produto="Takamine GD20"),
        text("O Takamine GD20 sai por R$ 2.199,00."),
        text("No PIX fica R$ 2.089,05, e dá para fazer 12x de R$ 183,25."),
    )
    agent = agent_with(model, store, policies, tmp_path)
    agent.reply("Quanto custa o Takamine GD20?")
    turn = agent.reply("E no PIX sai quanto? Dá pra parcelar em 12x?")
    assert turn.guardrail is None
    assert turn.reply.startswith("No PIX fica R$ 2.089,05")


def test_every_topic_asked_must_be_looked_up(store, policies, tmp_path):
    # Run 2 failure: address looked up, Saturday hours answered from memory (9h-18h; real 9h-13h).
    model = ScriptedModel(
        call("get_policy", secao="sobre_a_loja_e_contato"),
        text("Fica na Rua 14 de Maio, 3200. Sábado abrimos das 9h às 18h."),
        call("get_policy", secao="horario_de_funcionamento"),
        text("Fica na Rua 14 de Maio, 3200. Sábado abrimos das 9h às 13h."),
    )
    turn = agent_with(model, store, policies, tmp_path).reply("Qual o endereço da loja? Vocês abrem sábado?")
    assert turn.reply.endswith("das 9h às 13h.")
    assert "horario_de_funcionamento" in model.requests[2]["messages"][-1]["content"]


def test_new_topic_mid_conversation_still_requires_a_lookup(store, policies, tmp_path):
    model = ScriptedModel(
        call("get_product_details", produto="Takamine GD20"),
        text("O Takamine GD20 sai por R$ 2.199,00."),
        text("Fica na Rua das Flores, 123."),
        text("Rua das Flores, 123!"),
    )
    agent = agent_with(model, store, policies, tmp_path)
    agent.reply("Quanto custa o Takamine GD20?")
    assert agent.reply("E qual o endereço da loja?").reply == UNGROUNDED_FALLBACK


def test_invented_phone_number_is_caught():
    sources = 'get_policy: "Telefone / WhatsApp (67) 3341-4444"'
    assert untraceable_values("Chama no WhatsApp (67) 3341-4444!", sources) == []
    assert untraceable_values("Chama no WhatsApp (67) 3333-0000!", sources) == ["(67) 3333-0000"]


def test_values_from_tools_customer_and_clock_are_traceable():
    sources = 'tool: {"preco_no_pix": "R$ 2.089,05"}\ncliente: até R$1000\nAgora: 25/03/2026 10:30'
    assert untraceable_values("PIX: R$ 2.089,05, até R$ 1.000,00, hoje 25/03/2026", sources) == []
    assert untraceable_values("PIX: R$ 2.089,50 até 26/03/2026", sources) == ["R$ 2.089,50", "26/03/2026"]


def test_model_that_insists_gets_an_honest_fallback(store, policies, tmp_path):
    model = ScriptedModel(text("Fica na Rua das Flores, 123."), text("Rua das Flores, 123!"))
    turn = agent_with(model, store, policies, tmp_path).reply("Qual o endereço da loja?")
    assert turn.reply == UNGROUNDED_FALLBACK
    assert "Flores" not in turn.reply


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
