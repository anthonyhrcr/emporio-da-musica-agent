"""Run the scripted example conversations against the real agent and save them unedited.

    python scripts/run_examples.py

The store clock is frozen at 2026-03-25 10:30 (a Wednesday, store open) unless
STORE_NOW is already set: the data is a snapshot whose latest order is from
22/03/2026, so date rules (7-day returns, 30-day exchanges) are evaluated on
the data's own timeline. See README, "Assumptions".
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("STORE_NOW", "2026-03-25T10:30")

from emporio.agent import Agent  # noqa: E402
from emporio.config import load_settings  # noqa: E402
from emporio.transcript import to_markdown  # noqa: E402

SCENARIOS = [
    ("01_catalogo_violoes_ate_1000", "Catálogo: violões até R$ 1.000 e recomendação", [
        "Oi! Quais opções de violões disponíveis custando até R$1000?",
        "Sou iniciante e prefiro corda de nylon. Qual você indicaria?",
    ]),
    ("02_preco_promocao_encerrada_pagamento", "Preço, promoção encerrada e formas de pagamento", [
        "Quanto custa o Takamine GD20?",
        "Vi que ele tava com 12% de desconto, ainda vale?",
        "E no PIX sai quanto? Dá pra parcelar em 12x?",
    ]),
    ("03_devolucao_com_verificacao_lgpd", "Devolução: verificação de identidade, prazos e garantia (não trivial)", [
        "Me arrependi da minha compra, posso devolver meu pedido?",
        "É o pedido 7. Meu telefone é (67) 98888-1234",
        "Ah, desculpa, comprei com o e-mail leticia.rocha@jmail.com",
        "Na verdade o ukulele veio com um defeito no traste. O que eu faço?",
    ]),
    ("04_fora_do_escopo_e_acessorios", "Fora do escopo: acessórios, assunto aleatório e categoria vazia", [
        "Vocês vendem cordas e amplificador?",
        "E quem ganhou o jogo do Flamengo ontem?",
        "Beleza. Vocês têm saxofone?",
    ]),
    ("05_endereco_horario_produto_esgotado", "Informações da loja e produto esgotado", [
        "Qual o endereço da loja? Vocês abrem sábado?",
        "Tem o Giannini GF-3D?",
    ]),
]


def main() -> None:
    settings = load_settings()
    agent = Agent.from_settings(settings)
    out = ROOT / "examples"
    out.mkdir(exist_ok=True)
    only = set(sys.argv[1:])
    for slug, title, messages in SCENARIOS:
        if only and not any(slug.startswith(o) for o in only):
            continue
        agent.reset()
        started = time.time()
        for message in messages:
            agent.reply(message)
        note = (f"Gerado por `scripts/run_examples.py` em {time.time() - started:.0f}s. "
                "Respostas do modelo sem nenhuma edição.")
        (out / f"{slug}.md").write_text(to_markdown(title, agent.turns, settings, note), encoding="utf-8")
        print(f"ok  {slug}  ({time.time() - started:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
