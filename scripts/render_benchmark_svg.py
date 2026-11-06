"""Draw docs/img/benchmark.svg from results/benchmark.json.

Rerun after the benchmark changes: python -m scripts.render_benchmark_svg
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "benchmark.json"
OUTPUT = ROOT / "docs" / "img" / "benchmark.svg"

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
WIDTH = 1000
TRACK_X = 190
TRACK_WIDTH = 520
ROW_HEIGHT = 62
TOP = 78

STYLE = """
    .bg { fill: #ffffff; stroke: #d0d7de; }
    .ink { fill: #1f2328; }
    .muted { fill: #59636e; }
    .track { fill: #e9ebee; }
    .bar { fill: #2f5d8a; }
    @media (prefers-color-scheme: dark) {
      .bg { fill: #0d1117; stroke: #3d444d; }
      .ink { fill: #f0f6fc; }
      .muted { fill: #9198a1; }
      .track { fill: #2c3036; }
      .bar { fill: #7fb0dc; }
    }"""


def render(results: dict[str, Any]) -> str:
    agents = results["agents"]
    height = TOP + ROW_HEIGHT * len(agents) + 12
    total = agents[0]["tests_total"]
    title = f"APB score across {total} tests"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{title}" '
        f'font-family="{FONT}">\n',
        f"  <title>{title}</title>\n",
        f"  <style>{STYLE}\n  </style>\n",
        f'  <rect class="bg" x="0.5" y="0.5" width="{WIDTH - 1}" '
        f'height="{height - 1}" rx="12"/>\n',
        f'  <text class="ink" x="28" y="42" font-size="21" font-weight="600">{title}</text>\n',
        f'  <text class="muted" x="{TRACK_X + TRACK_WIDTH}" y="42" text-anchor="end" '
        f'font-size="14">Method {results["method_version"]}, '
        f'{results["data_mode"]} data, scale 0 to 100</text>\n',
    ]
    for index, agent in enumerate(agents):
        y = TOP + index * ROW_HEIGHT
        bar = round(TRACK_WIDTH * agent["apb_score"] / 100, 1)
        parts += [
            f'  <text class="ink" x="28" y="{y + 19}" font-size="17">{agent["agent"]}</text>\n',
            f'  <rect class="track" x="{TRACK_X}" y="{y + 4}" width="{TRACK_WIDTH}" '
            'height="20" rx="4"/>\n',
            f'  <rect class="bar" x="{TRACK_X}" y="{y + 4}" width="{bar}" '
            f'height="20" rx="4"><title>{agent["agent"]}: {agent["apb_score"]:.1f}'
            "</title></rect>\n",
            f'  <text class="ink" x="{TRACK_X + TRACK_WIDTH + 18}" y="{y + 20}" '
            f'font-size="19" font-weight="600">{agent["apb_score"]:.1f}</text>\n',
            f'  <text class="muted" x="{WIDTH - 28}" y="{y + 20}" text-anchor="end" '
            f'font-size="16">tests passed {agent["tests_passed"]}/{agent["tests_total"]}'
            "</text>\n",
        ]
    parts.append("</svg>\n")
    return "".join(parts)


def main() -> None:
    results = json.loads(RESULTS.read_text("utf-8"))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as file:
        file.write(render(results))
    print("Wrote docs/img/benchmark.svg")


if __name__ == "__main__":
    main()
