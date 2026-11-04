"""The agent loop: model <-> tools, with conversation history kept per session.

It includes one guardrail enforced in code. If the customer asks about store facts
(products, prices, hours, address, payment, returns, shipping, orders...) and the
model answers without consulting any tool in that turn, the answer is discarded and
the model is asked to consult first. If it still won't, the customer gets an honest
"I couldn't confirm that" instead of an invented fact. This was added after the
example runs showed qwen3:8b inventing a store address and confirming saxophones
the store doesn't have. See README, section 6.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from openai import OpenAI

from .config import Settings, load_settings
from .data import Store, load_store, norm
from .policies import Policies
from .prompts import build_system_prompt
from .tools import Toolbox, mentions_catalogue

MAX_TOOL_ROUNDS = 6
FALLBACK = (
    "Desculpa, me enrolei aqui e não consegui concluir. Posso te passar para alguém da equipe? "
    "É só me dizer o que você precisa."
)
UNGROUNDED_FALLBACK = (
    "Não consegui confirmar essa informação no sistema agora, e prefiro não te passar nada errado. "
    "Quer que eu encaminhe sua pergunta para a equipe?"
)
NUDGE = (
    "Sua última resposta trazia informação da loja sem consultar nenhuma ferramenta, e foi descartada. "
    "Consulte a ferramenta adequada (catálogo, get_policy ou pedidos) antes de responder. "
    "Se precisar do telefone ou e-mail do cliente para consultar um pedido, peça ao cliente em vez de inventar."
)
_THINK = re.compile(r"<think>.*?</think>", re.S)
# Topics that are facts about the store and must come from a tool, never from the model's memory.
_STORE_FACTS = re.compile(
    r"\b(endereco|onde fica|localiza\w*|horario\w*|abre\w*|aberto|aberta|fecha\w*|sabado|domingo|feriado\w*|"
    r"funciona\w*|pagamento|pagar|pix|parcel\w*|cartao|credito|debito|boleto|troca\w*|devol\w*|reembols\w*|"
    r"arrepend\w*|frete|entrega\w*|envio|prazo\w*|garantia|defeito\w*|promoc\w*|desconto\w*|preco\w*|custa\w*|"
    r"valor|estoque|disponive\w*|telefone|whatsapp|e-?mail|contato|cnpj|pedido\w*|rastre\w*|cancel\w*)\b"
)
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


def needs_grounding(text: str, store: Store) -> bool:
    return bool(_STORE_FACTS.search(norm(text))) or mentions_catalogue(text, store)


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

    def _complete(self, extra: list[dict]):
        return self.client.chat.completions.create(
            model=self.settings.model,
            messages=self._messages() + extra,
            tools=self.toolbox.schemas(),
            temperature=self.settings.temperature,
            **({"reasoning_effort": self.settings.reasoning_effort} if self.settings.reasoning_effort else {}),
        )

    def reply(self, user_text: str) -> Turn:
        turn = Turn(user=user_text, reply="")
        self.history.append({"role": "user", "content": user_text})
        must_ground = needs_grounding(user_text, self.store)
        extra: list[dict] = []  # transient instructions for this turn only, never stored in history

        for _ in range(MAX_TOOL_ROUNDS):
            message = self._complete(extra).choices[0].message
            if not message.tool_calls:
                text = _THINK.sub("", message.content or "").strip() or FALLBACK
                ungrounded = must_ground and not turn.tool_calls and not _ASKS_FOR_CONTACT.search(norm(text))
                if ungrounded and not extra:
                    turn.guardrail = "resposta sobre a loja sem consulta a ferramentas: descartada e refeita"
                    extra = [{"role": "system", "content": NUDGE}]
                    continue
                if ungrounded:
                    turn.guardrail = "o modelo insistiu sem consultar ferramentas: resposta substituída por aviso"
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

        self.history.append({"role": "assistant", "content": turn.reply})
        self.turns.append(turn)
        return turn
