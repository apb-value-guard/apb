import json
import re
from pathlib import Path

from scripts import render_benchmark_svg, render_diagrams, render_readme_results

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text("utf-8")
RESULTS = json.loads((ROOT / "results" / "benchmark.json").read_text("utf-8"))
RERUN = "out of date: rerun the render scripts in scripts/"


def test_benchmark_svg_matches_the_results():
    current = (ROOT / "docs" / "img" / "benchmark.svg").read_text("utf-8")
    assert current == render_benchmark_svg.render(RESULTS), RERUN


def test_benchmark_svg_shows_each_agent():
    svg = render_benchmark_svg.render(RESULTS)

    for agent in RESULTS["agents"]:
        assert agent["agent"] in svg
        assert f"{agent['apb_score']:.1f}" in svg
        assert f"tests passed {agent['tests_passed']}/{agent['tests_total']}" in svg


def test_changed_results_would_be_caught():
    changed = json.loads(json.dumps(RESULTS))
    changed["agents"][0]["apb_score"] = 1.0

    assert render_benchmark_svg.render(changed) != render_benchmark_svg.render(RESULTS)
    assert render_readme_results.render(changed) != render_readme_results.render(RESULTS)


def test_readme_results_match_the_results():
    assert README == render_readme_results.updated_readme(README, RESULTS), RERUN


def test_readme_table_covers_every_test_once():
    scenarios = {scenario["name"] for scenario in RESULTS["scenarios"]}
    listed = [name for name, _, _ in render_readme_results.TESTS]

    assert len(listed) == len(set(listed))
    assert set(listed) == scenarios


def test_diagrams_are_up_to_date():
    images = ROOT / "docs" / "img"

    assert (images / "hero-light.svg").read_text("utf-8") == render_diagrams.hero(
        render_diagrams.LIGHT
    ), RERUN
    assert (images / "hero-dark.svg").read_text("utf-8") == render_diagrams.hero(
        render_diagrams.DARK
    ), RERUN
    assert (images / "how-it-works.svg").read_text("utf-8") == render_diagrams.how_it_works(), RERUN


def test_every_local_readme_image_and_link_exists():
    targets = re.findall(r'(?:src|srcset)="([^"]+)"', README)
    targets += re.findall(r"\]\(((?!https?://|#)[^)]+)\)", README)

    assert targets
    for target in targets:
        assert (ROOT / target).exists(), target


def test_readme_has_no_logo_images():
    images = re.findall(r'!\[[^\]]*\]\(([^)]+)\)|src="([^"]+)"', README)
    paths = [first or second for first, second in images]

    assert not [path for path in paths if "logo" in path.lower()]
    assert "logo=" not in README


def test_readme_keeps_the_required_wording():
    for text in (
        "APB Value Guard checks an AI agent's payment against the user's mandate",
        "best eligible route observed within the candidate set and quote window",
        "**The sponsor doesn't win by design.**",
        "No real money moves.",
        "Recipient amounts are rounded down to whole KES.",
    ):
        assert text in README
