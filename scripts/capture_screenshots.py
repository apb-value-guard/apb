"""Capture the README screenshots: python -m scripts.capture_screenshots

Needs Playwright, which isn't a project dependency. Set CHROMIUM_PATH to use a
specific browser. The server runs with AI off and execution off, so the pictures
use template text and nothing is paid. Port 8000 must be free.
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
URL = "http://localhost:8000/"
CONFIG_URL = "http://127.0.0.1:8000/api/config"

# Ends the crop at the explanation, above the execution and receipt sections.
CONTENT_BOX = """() => {
  const page = document.querySelector('.page').getBoundingClientRect();
  const heading = [...document.querySelectorAll('h2')]
    .find((item) => item.textContent.startsWith('Explanation'));
  const end = heading.nextElementSibling.getBoundingClientRect();
  return {
    x: page.left + window.scrollX,
    y: window.scrollY,
    width: page.width,
    height: end.bottom + window.scrollY + 16,
  };
}"""


def wait_for_server() -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(CONFIG_URL, timeout=1).close()
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise RuntimeError("the web server did not start")


def show_case(page: Page, button: str, route: str) -> dict[str, float]:
    """Run a case and return the box around its content."""
    page.click(button)
    page.wait_for_function(
        "(route) => document.querySelector('.selected-line')?.textContent.includes(route)",
        arg=route,
    )
    return page.evaluate(CONTENT_BOX)


def capture_cases(page: Page) -> None:
    """Capture both cases at one height, so they line up side by side."""
    cases = [
        ("#run-a", "paypal", "decide-case-a.png"),
        ("#run-b", "alternative_provider", "decide-case-b.png"),
    ]
    height = max(show_case(page, button, route)["height"] for button, route, _ in cases)
    for button, route, name in cases:
        box = show_case(page, button, route)
        page.screenshot(path=IMG / name, full_page=True, clip={**box, "height": height})
        print(f"Wrote docs/img/{name}")


def main() -> None:
    environment = {**os.environ, "AI_BACKEND": "none", "ALLOW_EXECUTE": "false"}
    server = subprocess.Popen([sys.executable, "-m", "apb.web"], cwd=ROOT, env=environment)
    try:
        wait_for_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_PATH") or None
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                device_scale_factor=2,
                color_scheme="light",
            )
            page = context.new_page()
            page.goto(URL)
            # The shorter case would otherwise show part of this panel.
            page.add_style_tag(content="#receipt-section { visibility: hidden; }")
            capture_cases(page)
            page.click("#tab-benchmark")
            page.wait_for_selector("#benchmark table")
            page.locator(".page").screenshot(path=IMG / "benchmark-tab.png")
            print("Wrote docs/img/benchmark-tab.png")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
