"""Run the engine: python -m apb.demo --case a|b, or --text "..." (needs AI)."""

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from apb.decide import decide
from apb.execute import execute
from apb.explain import with_explanation
from apb.models import DecisionReceipt, PaymentMandate, RouteQuote
from apb.parser import parse_mandate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_CURRENCY = "USD"


def load_mandate(case: str) -> PaymentMandate:
    path = FIXTURES / f"mandate_case_{case}.json"
    return PaymentMandate.model_validate(json.loads(path.read_text("utf-8")))


def load_quotes(stamp: datetime | None = None) -> list[RouteQuote]:
    """Load the fixtures. `stamp` re-stamps synthetic quotes for a live run."""
    path = FIXTURES / "us_ke.json"
    quotes = [RouteQuote.model_validate(q) for q in json.loads(path.read_text("utf-8"))]
    if stamp is None:
        return quotes
    return [
        quote.model_copy(update={"timestamp": stamp})
        if quote.data_mode == "synthetic"
        else quote
        for quote in quotes
    ]


def money(value: Decimal | None) -> str:
    return "not disclosed" if value is None else f"{value:.2f}"


def print_report(mandate: PaymentMandate, receipt: DecisionReceipt) -> None:
    print(
        f"Mandate {mandate.mandate_id}: send {money(mandate.send_amount)} "
        f"{mandate.send_currency} to {mandate.receive_country} via "
        f"{mandate.payout_method}, deadline {mandate.deadline_minutes} min"
    )

    print("\nRoute cards")
    for quote in receipt.candidate_set:
        sponsored = ", sponsored" if quote.sponsored else ""
        print(f"  {quote.provider} [{quote.data_mode}{sponsored}]")
        print(f"    fee:       {money(quote.fee)} {mandate.send_currency}")
        print(f"    fx rate:   {money(quote.fx_rate)}")
        print(f"    recipient: {money(quote.recipient_amount)}")
        print(f"    delivery:  {quote.delivery_minutes} min")

    print("\nExclusions")
    for route in receipt.excluded_routes:
        print(f"  {route.provider}: {route.reason.value}")
    if not receipt.excluded_routes:
        print("  none")

    print("\nDVS per eligible route")
    for provider, score in receipt.scores.items():
        marker = "  <- selected" if provider == receipt.selected_route else ""
        print(f"  {provider}: {score:.1f}{marker}")
    if not receipt.scores:
        print("  no eligible routes")

    print(f"\nExplanation ({receipt.explanation_source})")
    print(f"  {receipt.explanation}")

    if receipt.execution is not None:
        print("\nExecution")
        print(json.dumps(receipt.execution, indent=2))

    print("\nReceipt")
    print(receipt.model_dump_json(indent=2))


def fresh_mandate(mandate: PaymentMandate, now: datetime) -> PaymentMandate:
    """Return a copy with a new mandate_id, so it can make a new payout."""
    suffix = now.strftime("%Y%m%dT%H%M%SZ")
    return mandate.model_copy(
        update={"mandate_id": f"{mandate.mandate_id}-fresh-{suffix}"}
    )


def run_mandate(
    mandate: PaymentMandate,
    pay: bool = False,
    approve: bool = False,
    fresh: bool = False,
) -> None:
    """Decide and explain, then pay only if `pay` is set."""
    stamp = datetime.now(UTC) if pay else None
    if fresh:
        mandate = fresh_mandate(mandate, stamp)
    receipt = decide(mandate, load_quotes(stamp), now=stamp)
    # The explanation is finished before any payment step begins.
    receipt = with_explanation(receipt, mandate)
    if pay:
        receipt = execute(receipt, mandate, datetime.now(UTC), approve)
    print_report(mandate, receipt)


def run_text(
    text: str, pay: bool = False, approve: bool = False, fresh: bool = False
) -> int:
    """Parse the text and decide. Returns the process exit code."""
    result = parse_mandate(text)

    if result.status == "ai_unavailable":
        print(result.message)
        return 2
    if result.status == "out_of_scope":
        print(f"Out of scope: {result.message}")
        return 0
    if result.status == "missing_fields":
        print("More information is needed:")
        for item in result.missing_fields:
            print(f"  {item.field}: {item.question}")
        return 0

    # Quotes carry no currency, so a GBP mandate would match these USD quotes.
    if result.mandate.send_currency != FIXTURE_CURRENCY:
        print(
            f"Parsed a {result.mandate.send_currency} mandate, but the fixture "
            f"routes are {FIXTURE_CURRENCY} only, so no decision was made."
        )
        return 0

    print("Parsed mandate")
    print(result.mandate.model_dump_json(indent=2))
    print()
    run_mandate(result.mandate, pay, approve, fresh)
    return 0


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Run APB Value Guard.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", choices=["a", "b"])
    source.add_argument("--text", help="a payment request in plain English")
    parser.add_argument(
        "--execute", action="store_true", help="pay via the PayPal sandbox"
    )
    parser.add_argument(
        "--approve", action="store_true", help="approve a fee above the mandate limit"
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="use a new mandate_id, so --execute makes a new sandbox payout",
    )
    args = parser.parse_args()
    if args.fresh and not args.execute:
        parser.error("--fresh needs --execute")

    if args.text is not None:
        return run_text(args.text, args.execute, args.approve, args.fresh)
    run_mandate(load_mandate(args.case), args.execute, args.approve, args.fresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
