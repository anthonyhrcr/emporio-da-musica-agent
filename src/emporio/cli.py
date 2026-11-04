"""Terminal chat with the agent.

    python -m emporio.cli            # chat
    python -m emporio.cli --debug    # also show which tools were called

Commands: /novo (new conversation), /sair (quit). Each conversation is saved
as Markdown under runtime/conversations/ when it ends.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from openai import APIConnectionError, APIStatusError

from .agent import Agent
from .config import load_settings
from .transcript import to_markdown


def _save(agent: Agent) -> None:
    if not agent.turns:
        return
    folder = agent.settings.runtime_dir / "conversations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(to_markdown("Conversa", agent.turns, agent.settings), encoding="utf-8")
    print(f"(conversa salva em {path})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lia — assistente da Empório da Música")
    parser.add_argument("--debug", action="store_true", help="mostra as ferramentas chamadas")
    args = parser.parse_args()

    settings = load_settings()
    agent = Agent.from_settings(settings)
    print(f"Empório da Música · Lia ({settings.model}). Digite /novo para recomeçar ou /sair para sair.\n")

    while True:
        try:
            text = input("você › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/sair", "/exit", "/quit"):
            break
        if text == "/novo":
            _save(agent)
            agent.reset()
            print("— nova conversa —\n")
            continue
        try:
            turn = agent.reply(text)
        except APIConnectionError:
            sys.exit(f"\nNão consegui falar com o modelo em {settings.base_url}. "
                     "Se estiver usando Ollama: `ollama serve` e `ollama pull qwen3:8b`.")
        except APIStatusError as exc:
            sys.exit(f"\nO provedor do modelo respondeu com erro {exc.status_code}: {exc.message}")
        if args.debug:
            for call in turn.tool_calls:
                print(f"  🔧 {call.name}({call.arguments})")
            if turn.guardrail:
                print(f"  🛡️ {turn.guardrail}")
        print(f"\nLia › {turn.reply}\n")
    _save(agent)


if __name__ == "__main__":
    main()
