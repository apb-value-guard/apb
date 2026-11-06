"""Run the engine: python -m apb.demo --case a|b, or --text "..." (needs AI)."""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from apb.decide import decide
from apb.explain import with_explanation
from apb.models import DecisionReceipt, PaymentMandate, RouteQuote
from apb.parser import parse_mandate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_CURRENCY = "USD"


def load_mandate(case: str) -> PaymentMandate:
    path = FIXTURES / f"mandate_case_{case}.json"
    return PaymentMandate.model_validate(json.loads(path.read_text("utf-8")))


def load_quotes() -> list[RouteQuote]:
    path = FIXTURES / "us_ke.json"
    return [RouteQuote.model_validate(q) for q in json.loads(path.read_text("utf-8"))]


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
        print(f"  {quote.provider} [{quote.data_mode}]")
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

    print("\nReceipt")
    print(receipt.model_dump_json(indent=2))


def run_mandate(mandate: PaymentMandate) -> None:
    print_report(mandate, with_explanation(decide(mandate, load_quotes()), mandate))


def run_text(text: str) -> int:
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
    run_mandate(result.mandate)
    return 0


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Run APB Value Guard.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", choices=["a", "b"])
    source.add_argument("--text", help="a payment request in plain English")
    args = parser.parse_args()

    if args.text is not None:
        return run_text(args.text)
    run_mandate(load_mandate(args.case))
    return 0


if __name__ == "__main__":
    sys.exit(main())
