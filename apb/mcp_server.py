"""MCP server so an agent can check before it pays: python -m apb.mcp_server

It runs over stdio and calls the same functions as the Check API. It never
moves money and stores nothing.
"""

from typing import Any

from pydantic import AwareDatetime
from mcp.server.mcpserver import MCPServer

from apb.check_api import (
    NOTICE,
    CheckContext,
    CheckError,
    CheckRequest,
    RecheckRequest,
    check,
    recheck,
)
from apb.models import DecisionReceipt, PaymentMandate, RouteQuote

server = MCPServer(
    name="apb",
    instructions=(
        "Call apb_check before paying, with the user's mandate and every quote. "
        "Pay only when decision is 'pay'. On 'pause', ask the user. On 'stop', "
        "don't pay, whatever the user says. Call apb_recheck with fresh quotes "
        f"just before paying. {NOTICE}"
    ),
)


@server.tool(
    name="apb_check",
    description=(
        "Check a payment before making it. Returns pay, pause or stop, the selected "
        f"route, excluded routes with reason codes, and a receipt. {NOTICE}"
    ),
)
def apb_check(
    mandate: PaymentMandate,
    quotes: list[RouteQuote],
    context: CheckContext | None = None,
    now: AwareDatetime | None = None,
) -> dict[str, Any]:
    try:
        return check(CheckRequest(mandate=mandate, quotes=quotes, context=context, now=now))
    except CheckError as error:
        raise ValueError(str(error)) from None


@server.tool(
    name="apb_recheck",
    description=(
        "Just before paying, check the earlier receipt's route against fresh quotes. "
        f"Returns ok, or re-decide with the reason and a new decision. {NOTICE}"
    ),
)
def apb_recheck(
    receipt: DecisionReceipt,
    mandate: PaymentMandate,
    quotes: list[RouteQuote],
    now: AwareDatetime,
    context: CheckContext | None = None,
) -> dict[str, Any]:
    try:
        return recheck(
            RecheckRequest(
                receipt=receipt, mandate=mandate, quotes=quotes, now=now, context=context
            )
        )
    except CheckError as error:
        raise ValueError(str(error)) from None


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
