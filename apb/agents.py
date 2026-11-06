"""Benchmark contestants.

An agent is a function: (mandate, quotes) -> chosen provider, or None for no
route. To add one, write that function and pass it to `run_benchmark`. Mark it
with `@rechecks_quotes` if it should be called again with the quotes at commit.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import TypeVar

from apb.decide import decide
from apb.models import PaymentMandate, RouteQuote

Agent = Callable[[PaymentMandate, Sequence[RouteQuote]], str | None]
AgentT = TypeVar("AgentT", bound=Agent)

# An agent returns this instead of a provider to wait for a person to confirm.
PAUSE = "pause_for_confirmation"

KNOWN_BRANDS = {"paypal"}


def rechecks_quotes(agent: AgentT) -> AgentT:
    """Mark an agent that decides again on the quotes available at commit."""
    agent.rechecks_quotes = True
    return agent


def does_recheck(agent: Agent) -> bool:
    return getattr(agent, "rechecks_quotes", False)


def naive_agent(
    mandate: PaymentMandate, quotes: Sequence[RouteQuote]
) -> str | None:
    """A typical shortcut: sponsored first, then a known brand, then lowest fee.

    It ignores the deadline, screening holds, KYC and payee checks, and it never
    rechecks its quote.
    """

    def preference(quote: RouteQuote) -> tuple:
        return (
            not quote.sponsored,
            quote.provider.lower() not in KNOWN_BRANDS,
            quote.fee if quote.fee is not None else Decimal("Infinity"),
            quote.provider,
        )

    chosen = min(quotes, key=preference, default=None)
    return chosen.provider if chosen else None


@rechecks_quotes
def guarded_agent(
    mandate: PaymentMandate, quotes: Sequence[RouteQuote]
) -> str | None:
    """Our decide(): gate first, then score. It also rechecks at commit."""
    receipt = decide(mandate, quotes)
    return PAUSE if receipt.confirmation_reason else receipt.selected_route


AGENTS: dict[str, Agent] = {
    "naive_agent": naive_agent,
    "guarded_agent": guarded_agent,
}
