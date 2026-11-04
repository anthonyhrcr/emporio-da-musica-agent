"""Payment rules from the policy manual (sections 3 and 6.2), implemented in code.

Arithmetic and pricing rules live here, not in the prompt: the language model
is never the component that computes a price, a discount or an installment.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

PIX_DISCOUNT = Decimal("0.05")
COMBINED_PAYMENT_MIN = Decimal("2000.00")
CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def brl(value) -> str:
    """Format as Brazilian currency: R$ 1.234,56"""
    text = f"{money(value):,.2f}"
    return "R$ " + text.replace(",", "X").replace(".", ",").replace("X", ".")


def min_installment(n: int) -> Decimal:
    """Minimum value of each interest-free installment when paying in n times (policy 3.1).

    Assumption: "até 3x: sem valor mínimo de parcela (exceto abaixo de R$ 50,00)"
    is read as "each installment must be at least R$ 50,00". The summary table
    says "parcela mínima de R$ 100,00" for credit cards; 3.1 is more specific,
    so it wins. Both are documented in the README.
    """
    if n <= 1:
        return Decimal("0")
    if n <= 3:
        return Decimal("50")
    if n <= 6:
        return Decimal("80")
    return Decimal("100")


def max_installments(amount) -> int:
    amount = money(amount)
    best = 1
    for n in range(2, 13):
        if amount / n >= min_installment(n):
            best = n
    return best


def quote(list_price, promo_percent: int | None = None, promo_label: str | None = None) -> dict:
    """Everything a customer may ask about a price, computed once and returned as text."""
    list_price = money(list_price)
    if promo_percent:
        price = money(list_price * (Decimal(100) - Decimal(promo_percent)) / Decimal(100))
        pix_price = price  # 6.2: PIX discount does not stack on promotional prices
        pix_note = "o desconto de 5% no PIX não se aplica sobre preço promocional (promoções não acumulam)"
    else:
        price = list_price
        pix_price = money(price * (1 - PIX_DISCOUNT))
        pix_note = "5% de desconto no PIX sobre o preço de tabela"
    n = max_installments(price)
    return {
        "preco_tabela": brl(list_price),
        "promocao_ativa": (
            {"campanha": promo_label, "desconto": f"{promo_percent}%", "preco_promocional": brl(price)}
            if promo_percent
            else None
        ),
        "preco_final": brl(price),
        "preco_no_pix": brl(pix_price),
        "regra_pix": pix_note,
        "debito_ou_boleto": brl(price),
        "parcelamento_maximo_sem_juros": (
            f"{n}x de {brl(price / n)} no cartão de crédito"
            if n > 1
            else "somente à vista (valor abaixo do mínimo para parcelar)"
        ),
        "pode_combinar_formas_de_pagamento": bool(price > COMBINED_PAYMENT_MIN),
    }
