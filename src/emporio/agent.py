"""The agent loop: model <-> tools, with conversation history kept per session."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from openai import OpenAI

from .config import Settings, load_settings
from .data import Store, load_store
from .policies import Policies
from .prompts import build_system_prompt
from .tools import Toolbox

MAX_TOOL_ROUNDS = 6
FALLBACK = (
    "Desculpa, me enrolei aqui e não consegui concluir. Posso te passar para alguém da equipe? "
    "É só me dizer o que você precisa."
)
_THINK = re.compile(r"<think>.*?</think>", re.S)


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

    def reply(self, user_text: str) -> Turn:
        turn = Turn(user=user_text, reply="")
        self.history.append({"role": "user", "content": user_text})

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.chat.completions.create(
                model=self.settings.model,
                messages=self._messages(),
                tools=self.toolbox.schemas(),
                temperature=self.settings.temperature,
                **({"reasoning_effort": self.settings.reasoning_effort} if self.settings.reasoning_effort else {}),
            )
            message = response.choices[0].message
            if not message.tool_calls:
                turn.reply = _THINK.sub("", message.content or "").strip() or FALLBACK
                break
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
