"""Render a conversation as Markdown, including which tools were consulted and what they returned."""
from __future__ import annotations

import json

from .agent import Turn
from .config import Settings

MAX_RESULT_CHARS = 1800


def to_markdown(title: str, turns: list[Turn], settings: Settings, note: str | None = None) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Modelo: `{settings.model}` via `{settings.base_url}`",
        f"- Relógio da loja: {settings.now():%d/%m/%Y %H:%M} (Campo Grande)",
    ]
    if note:
        lines.append(f"- {note}")
    lines.append("")
    for turn in turns:
        lines += [f"**Cliente:** {turn.user}", ""]
        if turn.tool_calls:
            lines.append("<details><summary>🔧 Ferramentas consultadas: "
                         + ", ".join(f"<code>{c.name}</code>" for c in turn.tool_calls) + "</summary>\n")
            for call in turn.tool_calls:
                result = json.dumps(call.result, ensure_ascii=False, indent=1)
                if len(result) > MAX_RESULT_CHARS:
                    result = result[:MAX_RESULT_CHARS] + "\n... (truncado)"
                origin = " — *feita automaticamente pelo guardrail*" if getattr(call, "automatic", False) else ""
                lines += [f"`{call.name}({json.dumps(call.arguments, ensure_ascii=False)})`{origin}", "",
                          "```json", result, "```", ""]
            lines += ["</details>", ""]
        if turn.guardrail:
            lines += [f"🛡️ *Guardrail: {turn.guardrail}.*", ""]
        reply = turn.reply.replace("\n", "\n> ")
        lines += [f"**Lia:**", "", f"> {reply}", ""]
    return "\n".join(lines)
