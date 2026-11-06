"""Write the README banner and diagram: python -m scripts.render_diagrams"""

from pathlib import Path

from scripts.render_readme_results import hero_lines, latest_llm_run

IMG = Path(__file__).resolve().parent.parent / "docs" / "img"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

LIGHT = {
    "bg": "#ffffff",
    "line": "#d0d7de",
    "ink": "#1f2328",
    "muted": "#59636e",
    "arrow": "#59636e",
    "user_fill": "#f1f2f4",
    "user_edge": "#8a9099",
    "ai_fill": "#efe9fb",
    "ai_edge": "#6b46c1",
    "code_fill": "#e6f0fa",
    "code_edge": "#2f5d8a",
    "pay_fill": "#e4f4ea",
    "pay_edge": "#1c6b43",
}
DARK = {
    "bg": "#0d1117",
    "line": "#3d444d",
    "ink": "#f0f6fc",
    "muted": "#9198a1",
    "arrow": "#9198a1",
    "user_fill": "#21262d",
    "user_edge": "#6e7681",
    "ai_fill": "#2a2140",
    "ai_edge": "#a78bfa",
    "code_fill": "#16283c",
    "code_edge": "#7fb0dc",
    "pay_fill": "#14301f",
    "pay_edge": "#6fcf9d",
}


def _rules(theme: dict[str, str]) -> str:
    return f"""
    .bg {{ fill: {theme['bg']}; stroke: {theme['line']}; }}
    .ink {{ fill: {theme['ink']}; }}
    .muted {{ fill: {theme['muted']}; }}
    .wire {{ stroke: {theme['arrow']}; fill: none; }}
    .head {{ fill: {theme['arrow']}; }}
    .user {{ fill: {theme['user_fill']}; stroke: {theme['user_edge']}; }}
    .ai {{ fill: {theme['ai_fill']}; stroke: {theme['ai_edge']}; }}
    .code {{ fill: {theme['code_fill']}; stroke: {theme['code_edge']}; }}
    .pay {{ fill: {theme['pay_fill']}; stroke: {theme['pay_edge']}; }}"""


def _open(width: int, height: int, title: str, style: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}" '
        f'font-family="{FONT}">\n'
        f"  <title>{title}</title>\n"
        f"  <style>{style}\n  </style>\n"
        '  <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path class="head" d="M0 0 L10 5 L0 10 z"/></marker></defs>\n'
        f'  <rect class="bg" x="0.5" y="0.5" width="{width - 1}" '
        f'height="{height - 1}" rx="14"/>\n'
    )


def _box(x, y, w, h, kind, title, detail, tag=None) -> str:
    centre = x + w / 2
    parts = [f'  <rect class="{kind}" x="{x}" y="{y}" width="{w}" height="{h}" rx="10" stroke-width="1.5"/>\n']
    title_y = y + 30
    if tag:
        parts.append(
            f'  <text class="muted" x="{centre}" y="{y + 24}" text-anchor="middle" '
            f'font-size="12" font-weight="600" letter-spacing="1">{tag}</text>\n'
        )
        title_y = y + 50
    parts.append(
        f'  <text class="ink" x="{centre}" y="{title_y + 6}" text-anchor="middle" '
        f'font-size="19" font-weight="600">{title}</text>\n'
    )
    parts.append(
        f'  <text class="muted" x="{centre}" y="{title_y + 30}" text-anchor="middle" '
        f'font-size="14">{detail}</text>\n'
    )
    return "".join(parts)


def _wire(path: str) -> str:
    return (
        f'  <path class="wire" d="{path}" stroke-width="2" '
        'marker-end="url(#arrow)"/>\n'
    )


def hero(theme: dict[str, str], lines: tuple[str, str]) -> str:
    first, second = (line.replace("'", "&apos;") for line in lines)
    svg = _open(1200, 360, "APB Value Guard", _rules(theme))
    svg += (
        '  <text class="ink" x="60" y="98" font-size="54" font-weight="700">'
        "APB Value Guard</text>\n"
        f'  <text class="muted" x="60" y="140" font-size="23">{first}</text>\n'
        f'  <text class="muted" x="60" y="172" font-size="23">{second}</text>\n'
    )
    steps = [
        ("user", "Request", "What the user asked for"),
        ("code", "Eligibility gate", "Excludes with reason codes"),
        ("code", "Score (DVS)", "What the recipient receives"),
        ("pay", "Pay or hand off", "PayPal sandbox, or logged"),
    ]
    width, gap, top, height = 240, 40, 215, 100
    for index, (kind, title, detail) in enumerate(steps):
        x = 60 + index * (width + gap)
        svg += _box(x, top, width, height, kind, title, detail)
        if index < len(steps) - 1:
            middle = top + height / 2
            svg += _wire(f"M{x + width + 4} {middle} H{x + width + gap - 4}")
    return svg + "</svg>\n"


def how_it_works() -> str:
    dark = "\n    @media (prefers-color-scheme: dark) {" + _rules(DARK) + "\n    }"
    svg = _open(1200, 520, "How APB Value Guard works", _rules(LIGHT) + dark)

    legend = [("ai", "AI step"), ("code", "Code step"), ("pay", "PayPal step")]
    x = 30
    for kind, label in legend:
        svg += (
            f'  <rect class="{kind}" x="{x}" y="22" width="20" height="20" rx="4" '
            'stroke-width="1.5"/>\n'
            f'  <text class="ink" x="{x + 28}" y="37" font-size="15">{label}</text>\n'
        )
        x += 130

    width, gap, top, height = 255, 40, 66, 110
    row = [
        ("user", "USER", "Your sentence", "Send $200 home in 2 hours"),
        ("ai", "AI", "Reads the mandate", "Code checks every field"),
        ("code", "CODE", "Eligibility gate", "Excluded routes get reason codes"),
        ("code", "CODE", "Scores DVS", "Eligible routes only"),
    ]
    for index, (kind, tag, title, detail) in enumerate(row):
        x = 30 + index * (width + gap)
        svg += _box(x, top, width, height, kind, title, detail, tag)
        if index < len(row) - 1:
            middle = top + height / 2
            svg += _wire(f"M{x + width + 4} {middle} H{x + width + gap - 4}")

    # From the score, back to the left edge and down to the second row.
    svg += _wire("M1042 180 V208 H157 V236")

    svg += _box(30, 240, 255, 110, "ai", "Explains the decision", "Grounding-checked, or template", "AI")
    svg += _box(470, 240, 700, 110, "pay", "PayPal sandbox pays", "Only if PayPal is the best eligible route and every check passes", "PAYPAL")
    svg += _box(470, 380, 700, 110, "code", "Hand-off logged", "Another provider is the best eligible route, so no PayPal call", "CODE")

    svg += _wire("M289 295 H466")
    svg += _wire("M340 295 V435 H466")
    return svg + "</svg>\n"


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    lines = hero_lines(latest_llm_run())
    files = {
        "hero-light.svg": hero(LIGHT, lines),
        "hero-dark.svg": hero(DARK, lines),
        "how-it-works.svg": how_it_works(),
    }
    for name, svg in files.items():
        with (IMG / name).open("w", encoding="utf-8", newline="\n") as file:
            file.write(svg)
        print(f"Wrote docs/img/{name}")


if __name__ == "__main__":
    main()
