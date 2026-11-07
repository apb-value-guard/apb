"""A deterministic fallback for reading a payment request when AI is off.

It reads only unambiguous patterns. Anything missing, unclear or found twice is
left empty, and parser.build_result turns it into a question. The same
scope and "appears in the text" checks as the AI path then apply.
"""

import re

from apb.parser import MandateExtraction, ParseResult, build_result

LABEL = "rule-based parser, no AI"

_NUMBER = r"(\d[\d,]*(?:\.\d{1,2})?)"
_SYMBOLS = {"$": "USD", "us$": "USD", "£": "GBP", "€": "EUR"}
_WORDS = {
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
}
_SYMBOL_FIRST = re.compile(r"(us\$|\$|£|€)\s?" + _NUMBER, re.IGNORECASE)
_CODE_FIRST = re.compile(r"\b(usd|gbp|eur)\s?" + _NUMBER, re.IGNORECASE)
_WORD_AFTER = re.compile(
    _NUMBER + r"\s?(usd|gbp|eur|dollars?|pounds?|euros?)\b", re.IGNORECASE
)

# An M-PESA number written in one block, or in groups of three.
_MPESA = re.compile(
    r"m[\s-]?pesa\b[^\d+]{0,20}(\+?\d{9,13}|\+?\d{3,4}(?:[\s-]\d{3}){2,3})(?!\d)",
    re.IGNORECASE,
)
_PAYOUTS = {
    "mpesa": re.compile(r"m[\s-]?pesa", re.IGNORECASE),
    "bank": re.compile(r"\bbank\b", re.IGNORECASE),
}
_DEADLINE = re.compile(
    r"(\d+(?:\.\d+)?)\s?(minutes?|mins?|hours?|hrs?|h|days?)\b", re.IGNORECASE
)
_UNITS = {"min": "minutes", "hou": "hours", "hrs": "hours", "hr": "hours", "h": "hours", "day": "days"}
_COUNTRIES = {"kenya": "KE", "uganda": "UG", "tanzania": "TZ", "nigeria": "NG", "ghana": "GH"}


def _only(values: set) -> object | None:
    """Return the one value found, or None if there were none or several."""
    return next(iter(values)) if len(values) == 1 else None


def _amount_and_currency(text: str) -> tuple[str | None, str | None]:
    found = set()
    for symbol, number in _SYMBOL_FIRST.findall(text):
        found.add((number.replace(",", ""), _SYMBOLS[symbol.lower()]))
    for code, number in _CODE_FIRST.findall(text):
        found.add((number.replace(",", ""), _WORDS[code.lower()]))
    for number, word in _WORD_AFTER.findall(text):
        found.add((number.replace(",", ""), _WORDS[word.lower()]))
    pair = _only(found)
    return pair if pair else (None, None)


def _deadline(text: str) -> tuple[int | None, str | None]:
    found = set()
    for number, unit in _DEADLINE.findall(text):
        if "." in number:
            return None, None
        key = unit.lower()[:3] if len(unit) > 2 else unit.lower()
        found.add((int(number), _UNITS.get(key, _UNITS.get(unit.lower()[:1]))))
    pair = _only(found)
    return pair if pair else (None, None)


def _country(text: str) -> str | None:
    lowered = text.lower()
    return _only({code for name, code in _COUNTRIES.items() if re.search(rf"\b{name}\b", lowered)})


def extract(text: str) -> MandateExtraction:
    """Read what the text states plainly. Nothing is inferred here."""
    amount, currency = _amount_and_currency(text)
    deadline_amount, deadline_unit = _deadline(text)
    payout = _only({name for name, pattern in _PAYOUTS.items() if pattern.search(text)})
    wallet = _only({match.strip() for match in _MPESA.findall(text)})
    return MandateExtraction(
        send_amount=amount,
        send_currency=currency,
        receive_country=_country(text),
        payout_method=payout,
        recipient_wallet=wallet if payout == "mpesa" else None,
        deadline_amount=deadline_amount,
        deadline_unit=deadline_unit,
        missing_fields=[],
    )


def parse_with_rules(text: str) -> ParseResult:
    return build_result(extract(text), text).model_copy(update={"parser": "rules"})
