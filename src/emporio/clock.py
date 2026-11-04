"""Opening hours (policy section 2) evaluated against the store's local clock."""
from __future__ import annotations

from datetime import date, datetime, timedelta

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# Monday=0 ... Saturday=5. Sunday is closed.
HOURS = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 13)}

# Fixed-date national holidays only. Movable (Carnaval, Sexta-feira Santa,
# Corpus Christi), state and municipal holidays are a known limitation.
FIXED_HOLIDAYS = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}


def is_holiday(day: date) -> bool:
    return (day.month, day.day) in FIXED_HOLIDAYS


def opening_hours(day: date) -> tuple[int, int] | None:
    if is_holiday(day):
        return None
    return HOURS.get(day.weekday())


def store_status(now: datetime) -> str:
    """One line for the system prompt, e.g. 'Agora ... A loja está ABERTA (fecha hoje às 18:00).'"""
    stamp = f"{WEEKDAYS_PT[now.weekday()]}, {now:%d/%m/%Y %H:%M}"
    today = opening_hours(now.date())
    if today and today[0] <= now.hour < today[1]:
        return f"Agora em Campo Grande: {stamp}. A loja está ABERTA (fecha hoje às {today[1]:02d}:00)."
    reason = " (feriado)" if is_holiday(now.date()) else ""
    for delta in range(0, 8):
        day = now.date() + timedelta(days=delta)
        hours = opening_hours(day)
        if not hours or (delta == 0 and now.hour >= hours[0]):
            continue
        when = "hoje" if delta == 0 else "amanhã" if delta == 1 else WEEKDAYS_PT[day.weekday()]
        return (
            f"Agora em Campo Grande: {stamp}. A loja está FECHADA{reason}. "
            f"Próxima abertura: {when} ({day:%d/%m}) às {hours[0]:02d}:00."
        )
    return f"Agora em Campo Grande: {stamp}. A loja está FECHADA{reason}."
