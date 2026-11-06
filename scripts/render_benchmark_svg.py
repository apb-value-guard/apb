"""Draw docs/img/benchmark.svg from the benchmark results and latest observed run.

Rerun after either changes: python -m scripts.render_benchmark_svg
"""

import json
from pathlib import Path
from typing import Any

from scripts.render_readme_results import latest_llm_run

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "benchmark.json"
OUTPUT = ROOT / "docs" / "img" / "benchmark.svg"

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
WIDTH = 1000
TRACK_X = 300
TRACK_WIDTH = 420
ROW_HEIGHT = 66
TOP = 78

STYLE = """
    .bg { fill: #ffffff; stroke: #d0d7de; }
    .ink { fill: #1f2328; }
    .muted { fill: #59636e; }
    .track { fill: #e9ebee; }
    .bar { fill: #2f5d8a; }
    .observed { fill: #7fa6cc; }
    @media (prefers-color-scheme: dark) {
      .bg { fill: #0d1117; stroke: #3d444d; }
      .ink { fill: #f0f6fc; }
      .muted { fill: #9198a1; }
      .track { fill: #2c3036; }
      .bar { fill: #7fb0dc; }
      .observed { fill: #47688a; }
    }"""


def _rows(results: dict[str, Any], run: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the bars in display order. Only the observed row is unofficial."""
    agents = {agent["agent"]: agent for agent in results["agents"]}

    def deterministic(name: str, note: str) -> dict[str, Any]:
        agent = agents[name]
        return {
            "name": name,
            "note": note,
            "score": agent["apb_score"],
            "passed": f"{agent['tests_passed']}/{agent['tests_total']}",
            "kind": "bar",
        }

    rows = [deterministic("naive_agent", "shortcut agent, deterministic")]
    if run is not None:
        full = run["variants"][0]
        rows.append(
            {
                "name": "llm_agent",
                "note": f"observed: {run['model']}, {run['date']}",
                "score": full["mean_observed_score"],
                "passed": f"{full['tests_passed_every_run']}/{full['tests_total']}",
                "kind": "observed",
            }
        )
    rows.append(deterministic("guarded_agent", "deterministic"))
    return rows


def render(results: dict[str, Any], run: dict[str, Any] | None = None) -> str:
    rows = _rows(results, run)
    height = TOP + ROW_HEIGHT * len(rows) + 8
    total = results["agents"][0]["tests_total"]
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
        f'  <text class="muted" x="{WIDTH - 28}" y="42" text-anchor="end" '
        f'font-size="14">Method {results["method_version"]}, '
        f'{results["data_mode"]} data, scale 0 to 100</text>\n',
    ]
    for index, row in enumerate(rows):
        y = TOP + index * ROW_HEIGHT
        bar = round(TRACK_WIDTH * row["score"] / 100, 1)
        parts += [
            f'  <text class="ink" x="28" y="{y + 17}" font-size="17">{row["name"]}</text>\n',
            f'  <text class="muted" x="28" y="{y + 37}" font-size="13">{row["note"]}</text>\n',
            f'  <rect class="track" x="{TRACK_X}" y="{y + 4}" width="{TRACK_WIDTH}" '
            'height="20" rx="4"/>\n',
            f'  <rect class="{row["kind"]}" x="{TRACK_X}" y="{y + 4}" width="{bar}" '
            f'height="20" rx="4"><title>{row["name"]}: {row["score"]:.1f}'
            "</title></rect>\n",
            f'  <text class="ink" x="{TRACK_X + TRACK_WIDTH + 16}" y="{y + 20}" '
            f'font-size="19" font-weight="600">{row["score"]:.1f}</text>\n',
            f'  <text class="muted" x="{WIDTH - 28}" y="{y + 20}" text-anchor="end" '
            f'font-size="16">tests passed {row["passed"]}</text>\n',
        ]
    parts.append("</svg>\n")
    return "".join(parts)


def main() -> None:
    results = json.loads(RESULTS.read_text("utf-8"))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as file:
        file.write(render(results, latest_llm_run()))
    print("Wrote docs/img/benchmark.svg")


if __name__ == "__main__":
    main()
