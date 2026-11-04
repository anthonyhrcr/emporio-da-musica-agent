"""Deterministic rules: pricing, opening hours, policy sections, verification and returns.

These run without a model. They pin down the parts of the agent that must be
exactly right (money, dates, data access) independently of LLM behaviour.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporio.clock import store_status
from emporio.config import STORE_TZ, Settings
from emporio.data import load_store
from emporio.policies import Policies
from emporio.pricing import max_installments, quote
from emporio.tools import Toolbox

DATA = Path(__file__).resolve().parents[1] / "data"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=STORE_TZ)


@pytest.fixture(scope="module")
def policies() -> Policies:
    return Policies.from_pdf(DATA / "politicas_da_loja.pdf")


@pytest.fixture(scope="module")
def tools(policies, tmp_path_factory) -> Toolbox:
    settings = Settings(
        base_url="", api_key="", model="", temperature=0, reasoning_effort=None,
        data_dir=DATA, runtime_dir=tmp_path_factory.mktemp("runtime"), store_now=at("2026-03-25T10:30"),
    )
    return Toolbox(load_store(DATA), policies, settings)


# --- pricing (policy 3 and 6.2) ---------------------------------------------

def test_pix_discount_applies_to_list_price():
    q = quote(Decimal("2199"))
    assert q["preco_no_pix"] == "R$ 2.089,05"
    assert q["parcelamento_maximo_sem_juros"].startswith("12x de R$ 183,25")


def test_pix_discount_does_not_stack_on_promotion():
    q = quote(Decimal("549"), 20, "Desconto Exclusivo")
    assert q["preco_final"] == "R$ 439,20"
    assert q["preco_no_pix"] == "R$ 439,20"


@pytest.mark.parametrize("amount, expected", [
    ("90", 1),      # 2x would be R$ 45 < R$ 50
    ("150", 3),     # 3x of R$ 50
    ("349.90", 4),  # 4x of R$ 87,48; 5x would be < R$ 80
    ("699.90", 6),  # 7x would be < R$ 100
    ("1200", 12),
])
def test_installment_tiers(amount, expected):
    assert max_installments(Decimal(amount)) == expected


def test_combined_payment_only_above_2000():
    assert quote(Decimal("2000"))["pode_combinar_formas_de_pagamento"] is False
    assert quote(Decimal("2000.01"))["pode_combinar_formas_de_pagamento"] is True


# --- opening hours (policy 2) -----------------------------------------------

@pytest.mark.parametrize("moment, open_", [
    ("2026-03-25T09:00", True),   # Wednesday opening
    ("2026-03-25T17:59", True),
    ("2026-03-25T18:00", False),  # closes at 18:00
    ("2026-03-28T12:59", True),   # Saturday until 13:00
    ("2026-03-28T13:00", False),
    ("2026-03-29T10:00", False),  # Sunday
    ("2026-11-02T10:00", False),  # Finados (national holiday, a Monday)
])
def test_store_hours(moment, open_):
    assert ("ABERTA" in store_status(at(moment))) is open_


def test_closed_status_says_when_it_reopens():
    assert "Próxima abertura: segunda-feira (30/03) às 09:00" in store_status(at("2026-03-28T15:00"))


# --- policy sections ----------------------------------------------------------

def test_policy_has_ten_sections_and_keeps_numbered_steps_inside_section_7(policies):
    assert sorted(policies.sections) == list(range(1, 11))
    assert "Saudação" in policies.sections[7].text
    assert "7 (sete) dias corridos" in policies.sections[4].text


def test_curator_note_resolves_whatsapp_conflict(policies):
    assert "(67) 3341-4444" in policies.lookup("diretrizes_de_atendimento")["nota_de_curadoria"]


# --- catalogue ------------------------------------------------------------------

def test_search_respects_budget_category_and_availability(tools):
    result = tools.search_products(termo="violões", preco_max=1000)
    assert result["total_encontrado"] > 0
    for item in result["produtos"]:
        assert item["categoria"] == "Violões"
        assert item["disponibilidade"] == "disponível"
        assert Decimal(item["preco"].replace("R$ ", "").replace(".", "").replace(",", ".")) <= 1000


def test_out_of_stock_product_offers_alternatives(tools):
    details = tools.get_product_details("Giannini GF-3D")
    assert details["disponibilidade"] == "esgotado"
    assert details["alternativas_disponiveis"]


def test_expired_promotion_is_reported_as_ended(tools):
    details = tools.get_product_details("Takamine GD20")
    assert details["promocao_ativa"] is None
    assert any("encerrada" in p for p in details["promocoes_encerradas"])


def test_empty_category_is_explained_not_invented(tools):
    result = tools.search_products(termo="saxofone")
    assert result["total_encontrado"] == 0
    assert "categorias_sem_produtos" in result


# --- orders: verification (LGPD) -------------------------------------------------

def test_order_requires_matching_contact(tools):
    assert tools.lookup_orders("(67) 98888-1234", "7")["verificado"] is False


def test_other_customers_order_is_not_revealed(tools):
    result = tools.lookup_orders("leticia.rocha@jmail.com", "10")  # order 10 belongs to customer 11
    assert "pedidos" not in result


def test_shared_email_is_ambiguous_and_asks_for_phone(tools):
    result = tools.lookup_orders("bruno.martins@jmail.com")
    assert result["verificado"] is False and "telefone" in result["mensagem"]


def test_phone_formats_are_normalised(tools):
    for contact in ("(67) 99789-0123", "67997890123", "+55 67 99789-0123"):
        assert tools.lookup_orders(contact, "7")["pedidos"][0]["numero"] == 7


# --- returns (policy 4 and 8) ------------------------------------------------------

def test_regret_window_expired_but_legal_warranty_still_valid(tools):
    result = tools.check_return_options("leticia.rocha@jmail.com", "7", "arrependimento")
    assert result["elegivel"] is False
    assert result["dias_desde_o_recebimento"] == 36
    assert any("18/05/2026" in option for option in result["outras_opcoes"])  # 17/02 + 90 days


def test_expired_deadline_is_never_offered_as_open(tools):
    # Bug found in the first example run: "troca até 05/03" was offered on 25/03.
    result = tools.check_return_options("leticia.rocha@jmail.com", "7", "arrependimento")
    assert not any("05/03/2026" in option for option in result["outras_opcoes"])
    assert any("encerrou em 05/03/2026" in option for option in result["prazos_ja_encerrados"])


def test_defect_after_30_days_goes_to_warranty(tools):
    result = tools.check_return_options("leticia.rocha@jmail.com", "7", "defeito")
    assert "garantia legal" in result["caminho"]


def test_unshipped_order_is_escalated_not_promised(tools):
    result = tools.check_return_options("(67) 99456-7890", "13", "arrependimento")  # pending order
    assert "escalate_to_human" in result["situacao"]


def test_escalation_returns_protocol(tools):
    result = tools.escalate_to_human("reclamacao", "Cliente reclama de atraso.")
    assert result["protocolo"].startswith("EM-20260325-")
    assert "24 horas úteis" in result["prazo"]
