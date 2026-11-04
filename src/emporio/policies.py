"""Policy manual (PDF) -> numbered sections, looked up by the agent through a tool.

Retrieval is deterministic section lookup, not vector search. The manual is
~8 pages and already split by numbered headings, so returning a whole section
keeps every rule together with its exceptions (a return rule and the items
excluded from it are never split across chunks). See README for the trade-off.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

SECTION_KEYS = {
    1: "sobre_a_loja_e_contato",
    2: "horario_de_funcionamento",
    3: "formas_de_pagamento_e_parcelamento",
    4: "trocas_e_devolucoes",
    5: "frete_e_entregas",
    6: "promocoes_e_descontos",
    7: "diretrizes_de_atendimento",
    8: "garantia",
    9: "privacidade_e_dados",
    10: "disposicoes_finais",
}

# Inconsistencies found in the manual (v2.1). They travel with the section text
# so the agent never quotes one of two contradictory values at random.
CURATOR_NOTES = {
    1: (
        "Nota de curadoria: o e-mail aparece com acento na tabela (contato@empóriodamusica.com.br) e sem "
        "acento na capa (contato@emporiodamusica.com.br). Informe a versão sem acento, da capa: domínio com "
        "acento é quase certamente erro de digitação. Telefone/WhatsApp oficial: (67) 3341-4444."
    ),
    3: (
        "Nota de curadoria: a tabela diz 'parcela mínima de R$ 100,00' para cartão de crédito, mas a seção 3.1 "
        "detalha faixas (até 3x: parcela mínima R$ 50,00; 4x a 6x: R$ 80,00; 7x a 12x: R$ 100,00). Vale a regra "
        "mais específica (3.1). Os valores de parcelamento das ferramentas de catálogo já seguem essa regra."
    ),
    7: (
        "Nota de curadoria: esta seção cita o WhatsApp (67) 3321-4500, que conflita com o número oficial "
        "(67) 3341-4444 da seção 1. Informe sempre (67) 3341-4444. O manual não lista lojas parceiras: "
        "não invente indicações."
    ),
}

_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
_HEADING = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
_PAGE = re.compile(r"^Página \d+$")


@dataclass(frozen=True)
class PolicySection:
    number: int
    key: str
    title: str
    text: str


class Policies:
    def __init__(self, sections: dict[int, PolicySection]):
        self.sections = sections
        self.by_key = {s.key: s for s in sections.values()}

    @classmethod
    def from_pdf(cls, path: Path) -> "Policies":
        reader = PdfReader(str(path))
        lines: list[str] = []
        for page in reader.pages[1:]:  # page 1 is the cover
            text = unicodedata.normalize("NFC", page.extract_text(extraction_mode="layout") or "")
            for raw in text.splitlines():
                line = " ".join(raw.split())
                if line and line != _HEADER and not _PAGE.match(line):
                    lines.append(line)

        # Only accept the *next expected* section number as a heading, so the
        # numbered steps inside 7.2 ("1. Saudação", "2. Entendimento"...) stay
        # inside section 7 instead of being read as new sections.
        sections: dict[int, tuple[str, list[str]]] = {}
        current, expected = None, 1
        for line in lines:
            match = _HEADING.match(line)
            if match and int(match.group(1)) == expected:
                current, expected = expected, expected + 1
                sections[current] = (match.group(2), [])
            elif current is not None:
                sections[current][1].append(line)

        return cls(
            {
                n: PolicySection(n, SECTION_KEYS.get(n, f"secao_{n}"), title, "\n".join(body))
                for n, (title, body) in sections.items()
            }
        )

    def lookup(self, key: str) -> dict:
        section = self.by_key.get(key)
        if section is None:
            return {"erro": f"seção desconhecida: {key}", "secoes_validas": list(self.by_key)}
        result = {"secao": section.number, "titulo": section.title, "texto": section.text}
        if section.number in CURATOR_NOTES:
            result["nota_de_curadoria"] = CURATOR_NOTES[section.number]
        return result
