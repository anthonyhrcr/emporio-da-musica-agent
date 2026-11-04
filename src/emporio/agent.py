"""The agent loop: model <-> tools, with conversation history kept per session.

Two guardrails are enforced in code. Both were added after reading the example
runs line by line (README, section 6):

1. Looked up? Every store topic the customer asks about (hours, address, payment,
   returns, shipping, prices and stock, orders, plus any instrument or brand they
   name) must have been consulted with a fitting tool somewhere in this
   conversation. A follow-up like "e no PIX?" is covered by the product lookup a
   turn earlier; a new question like "qual o endereço?" is not.
2. Traceable? Every R$ value, full date and phone number in a reply must appear in
   a tool result, the system prompt or the customer's own messages. The model's
   earlier replies don't count as a source: that is how a mis-copied price spreads.

Either problem gets one retry with a specific instruction. Ollama ignores
tool_choice="required", so the retry is an instruction, not a forced call. If the
problem persists, the customer gets an honest "I couldn't confirm that" instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from openai import OpenAI

from .config import Settings, load_settings
from .data import Store, load_store, norm
from .policies import Policies
from .prompts import build_system_prompt
from .tools import Toolbox, catalogue_mentions

MAX_TOOL_ROUNDS = 6
FALLBACK = (
    "Desculpa, me enrolei aqui e não consegui concluir. Posso te passar para alguém da equipe? "
    "É só me dizer o que você precisa."
)
UNGROUNDED_FALLBACK = (
    "Não consegui confirmar essa informação no sistema agora, e prefiro não te passar nada errado. "
    "Quer que eu encaminhe sua pergunta para a equipe?"
)
NUDGE_LOOKUP = (
    "Sua última resposta foi descartada: ela tratava de {topics} sem que isso tivesse sido consultado nesta "
    "conversa. Antes de responder, consulte: {hints}. Se precisar do telefone ou e-mail do cliente para "
    "consultar um pedido e ele ainda não informou, peça ao cliente em vez de inventar."
)
NUDGE_VALUES = (
    "Sua última resposta foi descartada porque citava {values}, que não aparece em nenhuma consulta desta "
    "conversa. Refaça a resposta copiando exatamente os valores, datas e telefones retornados pelas "
    "ferramentas; consulte de novo se precisar. Não faça contas."
)

# topic -> (stems that signal it in the customer's message, tool calls that answer it, hint for the retry)
TOPICS: dict[str, tuple[tuple[str, ...], set[str], str]] = {
    "horário de funcionamento": (
        ("horario", "abre", "abrir", "aberto", "aberta", "fecha", "sabado", "domingo", "feriado", "funciona"),
        {"get_policy:horario_de_funcionamento"}, "get_policy('horario_de_funcionamento')"),
    "endereço e contato da loja": (
        ("endereco", "onde fica", "localiza", "whatsapp", "cnpj"),
        {"get_policy:sobre_a_loja_e_contato"}, "get_policy('sobre_a_loja_e_contato')"),
    "pagamento": (
        ("pagamento", "pagar", "pix", "parcel", "cartao", "credito", "debito", "boleto"),
        {"get_policy:formas_de_pagamento_e_parcelamento", "get_product_details"},
        "get_product_details (para um produto) ou get_policy('formas_de_pagamento_e_parcelamento')"),
    "trocas, devolução e garantia": (
        ("troca", "devol", "reembols", "arrepend", "defeito", "garantia"),
        {"get_policy:trocas_e_devolucoes", "get_policy:garantia", "check_return_options"},
        "check_return_options (para um pedido) ou get_policy('trocas_e_devolucoes')"),
    "frete e entrega": (
        ("frete", "entrega", "envio", "enviar"),
        {"get_policy:frete_e_entregas", "lookup_orders"}, "get_policy('frete_e_entregas')"),
    "preço, estoque e promoções": (
        ("preco", "custa", "valor", "estoque", "disponive", "promoc", "desconto"),
        {"search_products", "get_product_details", "list_active_promotions", "get_policy:promocoes_e_descontos"},
        "search_products ou get_product_details"),
    "pedido": (
        ("pedido", "rastre", "cancel"),
        {"lookup_orders", "check_return_options", "escalate_to_human"},
        "lookup_orders (com o telefone ou e-mail do cliente)"),
}

_THINK = re.compile(r"<think>.*?</think>", re.S)
_MONEY = re.compile(r"R\$\s*(\d[\d.]*(?:,\d{1,2})?)")
_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_PHONE = re.compile(r"\(?\b\d{2}\)?\s?\d{4,5}-\d{4}\b")
# A reply that only asks for the customer's contact is the correct first step for orders.
_ASKS_FOR_CONTACT = re.compile(
    r"(seu|sua|informe|informar|preciso|passar|passa|me diz|me dizer|confirmar)\W.{0,40}(telefone|e-?mail)", re.S
)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: dict


@dataclass
class Turn:
    user: str
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    guardrail: str | None = None


def topics_in(text: str) -> list[str]:
    query = norm(text)
    words = re.findall(r"[a-z0-9]+", query)
    return [
        topic for topic, (stems, _, _) in TOPICS.items()
        if any((st in query) if " " in st else any(w.startswith(st) for w in words) for st in stems)
    ]


def needs_grounding(text: str, store: Store) -> bool:
    return bool(topics_in(text)) or bool(catalogue_mentions(text, store))


def _amount(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def untraceable_values(reply: str, sources: str) -> list[str]:
    """R$ values, full dates and phone numbers in the reply that appear nowhere in the sources."""
    known_money = {_amount(m) for m in _MONEY.findall(sources)}
    known_dates = set(_DATE.findall(sources))
    known_phones = {re.sub(r"\D", "", m) for m in _PHONE.findall(sources)}
    missing = [f"R$ {raw}" for raw in _MONEY.findall(reply) if _amount(raw) not in known_money]
    missing += [d for d in _DATE.findall(reply) if d not in known_dates]
    missing += [p for p in _PHONE.findall(reply) if re.sub(r"\D", "", p) not in known_phones]
    return list(dict.fromkeys(missing))


class Agent:
    def __init__(self, settings: Settings, store: Store, policies: Policies, client: OpenAI | None = None):
        self.settings = settings
        self.store = store
        self.toolbox = Toolbox(store, policies, settings)
        self.client = client or OpenAI(base_url=settings.base_url, api_key=settings.api_key)
        self.history: list[dict] = []
        self.turns: list[Turn] = []

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Agent":
        settings = settings or load_settings()
        store = load_store(settings.data_dir)
        policies = Policies.from_pdf(settings.data_dir / "politicas_da_loja.pdf")
        return cls(settings, store, policies)

    def reset(self) -> None:
        self.history.clear()
        self.turns.clear()

    def _messages(self) -> list[dict]:
        # Rebuilt every call so the clock (open/closed) is always current.
        system = build_system_prompt(self.store, self.settings.now())
        return [{"role": "system", "content": system}, *self.history]

    def _sources(self) -> str:
        """Everything a reply may legitimately quote: system prompt, customer messages, tool results."""
        parts = [self._messages()[0]["content"]]
        parts += [m["content"] for m in self.history if m["role"] in ("user", "tool")]
        return "\n".join(parts)

    def _consulted(self) -> tuple[set[str], str]:
        """Tool calls made in this conversation ('get_policy:<section>' for policies) and their evidence text."""
        keys: set[str] = set()
        evidence: list[str] = []
        for message in self.history:
            if message["role"] == "assistant":
                for call in message.get("tool_calls") or []:
                    name, raw = call["function"]["name"], call["function"]["arguments"]
                    keys.add(name)
                    try:
                        arguments = json.loads(raw or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if name == "get_policy" and isinstance(arguments, dict):
                        keys.add(f"get_policy:{arguments.get('secao')}")
                    evidence.append(raw or "")
            elif message["role"] == "tool":
                evidence.append(message["content"])
        return keys, norm(" ".join(evidence))

    def missing_lookups(self, user_text: str) -> list[tuple[str, str]]:
        """(topic, how to look it up) for everything the message asks about that wasn't consulted yet."""
        keys, evidence = self._consulted()
        missing = [(topic, TOPICS[topic][2]) for topic in topics_in(user_text) if not keys & TOPICS[topic][1]]
        for word, proof in catalogue_mentions(user_text, self.store).items():
            if proof not in evidence:
                missing.append((word, f"search_products('{word}')"))
        return missing

    def _complete(self, extra: list[dict]):
        return self.client.chat.completions.create(
            model=self.settings.model,
            messages=self._messages() + extra,
            tools=self.toolbox.schemas(),
            temperature=self.settings.temperature,
            **({"reasoning_effort": self.settings.reasoning_effort} if self.settings.reasoning_effort else {}),
        )

    def _problem(self, user_text: str, text: str) -> tuple[str, str] | None:
        """(log line, retry instruction) if the reply isn't grounded; None if it is."""
        missing = self.missing_lookups(user_text)
        if missing and not _ASKS_FOR_CONTACT.search(norm(text)):
            topics = ", ".join(topic for topic, _ in missing)
            hints = "; ".join(f"{topic} → {hint}" for topic, hint in missing)
            return f"resposta sem consultar: {topics}", NUDGE_LOOKUP.format(topics=topics, hints=hints)
        values = untraceable_values(text, self._sources())
        if values:
            listed = ", ".join(values)
            return f"valor sem origem em nenhuma consulta ({listed})", NUDGE_VALUES.format(values=listed)
        return None

    def reply(self, user_text: str) -> Turn:
        turn = Turn(user=user_text, reply="")
        self.history.append({"role": "user", "content": user_text})
        extra: list[dict] = []  # transient instruction for this turn only, never stored in history
        retried = False
        events: list[str] = []

        for _ in range(MAX_TOOL_ROUNDS):
            message = self._complete(extra).choices[0].message
            if not message.tool_calls:
                text = _THINK.sub("", message.content or "").strip() or FALLBACK
                problem = self._problem(user_text, text)
                if problem and not retried:
                    retried = True
                    events.append(f"{problem[0]}: descartada e refeita")
                    extra = [{"role": "system", "content": problem[1]}]
                    continue
                if problem:
                    events.append(f"{problem[0]}: resposta substituída por aviso")
                    text = UNGROUNDED_FALLBACK
                turn.reply = text
                break
            extra = []
            self.history.append({
                "role": "assistant",
                "content": _THINK.sub("", message.content or "").strip(),
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in message.tool_calls
                ],
            })
            for call in message.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = self.toolbox.call(call.function.name, arguments)
                turn.tool_calls.append(ToolCall(call.function.name, arguments, result))
                self.history.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            turn.reply = FALLBACK

        turn.guardrail = "; ".join(events) or None
        self.history.append({"role": "assistant", "content": turn.reply})
        self.turns.append(turn)
        return turn
