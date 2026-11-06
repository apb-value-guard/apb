"""Run a fixture case: python -m apb.demo --case a|b"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from apb.decide import decide
from apb.models import DecisionReceipt, PaymentMandate, RouteQuote

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


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

    print("\nReceipt")
    print(receipt.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an APB Value Guard fixture case.")
    parser.add_argument("--case", choices=["a", "b"], required=True)
    args = parser.parse_args()

    mandate = load_mandate(args.case)
    print_report(mandate, decide(mandate, load_quotes()))


if __name__ == "__main__":
    main()
