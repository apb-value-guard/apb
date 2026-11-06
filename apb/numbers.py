"""Find the numbers written in a piece of text."""

import re
from decimal import Decimal

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def numbers_in(text: str) -> set[Decimal]:
    """Return numeric values, so that 25,709 and 25709.00 compare equal."""
    return {Decimal(match.replace(",", "")) for match in _NUMBER.findall(text)}
