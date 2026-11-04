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

from emporio.agent import UNGROUNDED_FALLBACK, Agent, needs_grounding
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
