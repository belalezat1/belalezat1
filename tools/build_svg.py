"""Assemble dark_mode.svg and light_mode.svg for the belalezat1 profile README.

Personal / static fields live in the PROFILE block below. Dynamic totals are
seeded in STATS and overwritten from an existing SVG when rebuilding so a
refresh is not wiped by the next card build.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CARD_W, CARD_H = 985, 545

# Left column: wide-frame coarse ASCII portrait + language bar + legend
# Grid must match tools/ascii_portrait.py (COLS/ROWS) and stay left of X_RIGHT.
COLS, ROWS = 120, 112
ART_X, ART_Y0 = 14, 28
ART_FS, ART_ADVANCE, ART_LINE_H = 2.4, 1.44, 2.4
ART_W = COLS * ART_ADVANCE

BAR_X, BAR_Y, BAR_W, BAR_H = ART_X, 318, 260, 9
LEGEND_FS = 10.5
LEGEND_COLS = (ART_X, ART_X + 135)
LEGEND_ROWS = (346, 366, 386)
LEGEND_SLOTS = len(LEGEND_COLS) * len(LEGEND_ROWS)

# Right column: neofetch-style readout
WIDTH = 63
X_RIGHT = 318
FS = 17
LINE_H = 21
Y0 = 30

# GitHub Linguist colours (https://github.com/github-linguist/linguist)
LANGUAGE_COLORS = {
    "Jupyter Notebook": "#DA5B0B",
    "Python": "#3572A5",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "HTML": "#e34c26",
    "CSS": "#663399",
    "PHP": "#4F5D95",
    "C++": "#f34b7d",
    "C": "#555555",
    "Shell": "#89e051",
    "Rust": "#dea584",
    "PLpgSQL": "#336790",
    "Dockerfile": "#384d54",
    "Hack": "#878787",
    "Procfile": "#3B2F63",
    "Ruby": "#701516",
    "Go": "#00ADD8",
    "Kotlin": "#A97BFF",
    "Swift": "#F05138",
}
OTHER_COLOR = "#8b949e"

# Accent drawn from banner.svg warm sunset oranges (#ffb066 / #ff9152).
DARK = dict(
    bg="#161b22",
    fg="#c9d1d9",
    art="#c9d1d9",
    key="#ffb066",
    value="#a5d6ff",
    add="#3fb950",
    dele="#f85149",
    cc="#616e7f",
    track="#21262d",
)
LIGHT = dict(
    bg="#fffefe",
    fg="#24292f",
    # Slightly darker than GitHub muted gray so sparse ASCII holds up.
    art="#3d444d",
    key="#b35900",
    value="#0a3069",
    add="#1a7f37",
    dele="#cf222e",
    cc="#6e7781",
    track="#eaeef2",
)

# ---- static identity (evidence: prior README, portfolio site, public GitHub) ----
PROFILE = {
    "prompt": "belal@njit",
    "role": "CS student @ NJIT '27",
    "location": "New York City Metropolitan Area",
    "orgs": "NICC · United Mission Relief",
    "focus": "software engineering, cloud, AI/ML",
    "languages": "Python, Java, C++, TypeScript",
    "frameworks": "React Native, Spring Boot, Node",
    "email": "belal.ezat@protonmail.com",
    "website": "belalezat.me",
    "linkedin": "linkedin.com/in/belal-ezat",
    "devpost": "devpost.com/bte5",
}

# Seeded from the public GitHub API (2026-09-08). Updater rewrites these ids.
STATS = {
    "repos": "16",
    "prs": "0",
    "contributions": "0",
    "followers": "6",
    "loc": "0",
    "loc_add": "0",
    "loc_del": "0",
}

ID_TO_STAT = {
    "repo_data": "repos",
    "pr_data": "prs",
    "contribution_data": "contributions",
    "follower_data": "followers",
    "loc_data": "loc",
    "loc_add": "loc_add",
    "loc_del": "loc_del",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--art-dark", type=Path, default=HERE / "ascii_art_dark.json"
    )
    parser.add_argument(
        "--art-light", type=Path, default=HERE / "ascii_art_light.json"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--stats-from", type=Path, default=ROOT / "dark_mode.svg")
    parser.add_argument("--languages", type=Path, default=ROOT / "language_stats.json")
    return parser.parse_args()


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def read_art_runs(path: Path) -> list[list[dict]]:
    """Load colored ASCII runs from ascii_portrait.py JSON (or legacy .txt)."""
    if not path.exists():
        # Allow calling with .txt path → prefer sibling .json
        alt = path.with_suffix(".json")
        if alt.exists():
            path = alt
        else:
            raise SystemExit(
                f"missing ASCII art {path}; run tools/ascii_portrait.py first"
            )
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        runs = payload.get("runs") or []
        assert len(runs) <= ROWS, (path, len(runs))
        while len(runs) < ROWS:
            runs.append([{"ch": " ", "color": "", "n": COLS}])
        return runs

    # Legacy plain text → monochrome runs (palette art color applied later)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= ROWS, (path, len(lines))
    assert max((len(line) for line in lines), default=0) <= COLS, path
    lines = lines + [""] * (ROWS - len(lines))
    out: list[list[dict]] = []
    for line in lines:
        padded = line.ljust(COLS)[:COLS]
        out.append([{"ch": padded, "color": "", "n": 1}])
    return out


def art_line_markup(runs: list[dict], fallback_fill: str) -> str:
    parts: list[str] = []
    for run in runs:
        ch = str(run.get("ch") or " ")
        n = int(run.get("n") or 1)
        color = str(run.get("color") or "")
        if ch == " " and not color:
            parts.append(" " * n)
            continue
        text = xml_escape(ch * n if len(ch) == 1 else ch)
        fill = color if color.startswith("#") else fallback_fill
        parts.append(f'<tspan fill="{fill}">{text}</tspan>')
    return "".join(parts)

def load_stats_from_svg(path: Path, stats: dict[str, str]) -> None:
    if not path.exists():
        return
    for element in ET.parse(path).getroot().iter():
        key = ID_TO_STAT.get(element.attrib.get("id", ""))
        if key and element.text is not None:
            stats[key] = element.text


def load_languages(path: Path) -> list[tuple[str, float, str]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("languages") or []
    total = sum(entry["bytes"] for entry in entries)
    if total <= 0:
        return []
    ranked = sorted(entries, key=lambda entry: -entry["bytes"])
    head, tail = ranked[: LEGEND_SLOTS - 1], ranked[LEGEND_SLOTS - 1 :]
    shown = [
        (
            entry["name"],
            entry["bytes"] / total * 100.0,
            LANGUAGE_COLORS.get(entry["name"], OTHER_COLOR),
        )
        for entry in head
    ]
    tail_bytes = sum(entry["bytes"] for entry in tail)
    if tail_bytes:
        shown.append(("Other", tail_bytes / total * 100.0, OTHER_COLOR))
    return shown


def header(label: str) -> str:
    rule = "─" * (WIDTH - len(label) - 1)
    return f'<tspan x="{X_RIGHT}" y="{{y}}">{xml_escape(label)}</tspan> {rule}'


def key_markup(name: str) -> tuple[str, int]:
    segments = name.split(".")
    markup = ".".join(f'<tspan class="key">{xml_escape(part)}</tspan>' for part in segments)
    return markup, len(name)


def kv(pairs: list[tuple[str, int, str, int, str | None]]) -> str:
    """Build one `. Key: … Value` line ending exactly at WIDTH characters."""
    fixed = 2  # leading ". "
    for index, (_, key_len, _, value_len, _) in enumerate(pairs):
        fixed += key_len + 1 + 2 + value_len
        if index < len(pairs) - 1:
            fixed += 3  # " | "
    remaining_dots = max(len(pairs), WIDTH - fixed)
    parts = [f'<tspan x="{X_RIGHT}" y="{{y}}" class="cc">. </tspan>']
    for index, (key_mk, _key_len, value_mk, _value_len, dots_id) in enumerate(pairs):
        if index < len(pairs) - 1:
            dots = min(4, remaining_dots - (len(pairs) - 1 - index))
        else:
            dots = remaining_dots
        remaining_dots -= dots
        dots_attr = f' id="{dots_id}_dots"' if dots_id else ""
        value_attr = f' id="{dots_id}"' if dots_id else ""
        parts.append(
            f'{key_mk}:<tspan class="cc"{dots_attr}> {"." * dots} </tspan>'
            f'<tspan class="value"{value_attr}>{value_mk}</tspan>'
        )
        if index < len(pairs) - 1:
            parts.append(" | ")
    return "".join(parts)


def line_kv(name: str, value: str, dots_id: str | None = None) -> str:
    key_mk, key_len = key_markup(name)
    return kv([(key_mk, key_len, xml_escape(value), len(value), dots_id)])


def line_kv2(
    name_a: str,
    value_a: str,
    id_a: str,
    name_b: str,
    value_b: str,
    id_b: str,
) -> str:
    key_a, len_a = key_markup(name_a)
    key_b, len_b = key_markup(name_b)
    return kv(
        [
            (key_a, len_a, xml_escape(value_a), len(value_a), id_a),
            (key_b, len_b, xml_escape(value_b), len(value_b), id_b),
        ]
    )


LOC_LABEL = "Lines of Code"


def loc_line(stats: dict[str, str]) -> str:
    key = f'<tspan class="key">{LOC_LABEL}</tspan>'
    tail = (
        f'<tspan class="value" id="loc_data">{stats["loc"]}</tspan> '
        f'(<tspan class="addColor" id="loc_add">{stats["loc_add"]}</tspan>'
        f'<tspan class="addColor">++</tspan>, '
        f'<tspan class="delColor" id="loc_del">{stats["loc_del"]}</tspan>'
        f'<tspan class="delColor">--</tspan>)'
    )
    tail_len = (
        len(stats["loc"])
        + 2
        + len(stats["loc_add"])
        + 4
        + len(stats["loc_del"])
        + 3
    )
    fixed = 2 + len(LOC_LABEL) + 1 + 2 + tail_len
    dots = WIDTH - fixed
    assert dots >= 1, f"lines-of-code row needs {fixed + 1} columns, have {WIDTH}"
    return (
        f'<tspan x="{X_RIGHT}" y="{{y}}" class="cc">. </tspan>{key}:'
        f'<tspan class="cc" id="loc_data_dots"> {"." * dots} </tspan>{tail}'
    )


def build_rows(stats: dict[str, str]) -> list[str | None]:
    p = PROFILE
    return [
        header(p["prompt"]),
        line_kv("Role", p["role"]),
        line_kv("Location", p["location"]),
        line_kv("Orgs", p["orgs"]),
        line_kv("Focus", p["focus"]),
        None,
        line_kv("Stack.Languages", p["languages"]),
        line_kv("Stack.Frameworks", p["frameworks"]),
        None,
        header("─ Contact"),
        line_kv("Email", p["email"]),
        line_kv("Website", p["website"]),
        line_kv("LinkedIn", p["linkedin"]),
        line_kv("Devpost", p["devpost"]),
        None,
        header("─ GitHub Stats"),
        line_kv2(
            "Repos",
            stats["repos"],
            "repo_data",
            "PRs Merged",
            stats["prs"],
            "pr_data",
        ),
        line_kv2(
            "Contributions (1y)",
            stats["contributions"],
            "contribution_data",
            "Followers",
            stats["followers"],
            "follower_data",
        ),
        loc_line(stats),
    ]


def language_markup(
    languages: list[tuple[str, float, str]], palette: dict[str, str]
) -> list[str]:
    if not languages:
        return []
    out = [
        f'<clipPath id="barClip"><rect x="{BAR_X}" y="{BAR_Y}" '
        f'width="{BAR_W}" height="{BAR_H}" rx="{BAR_H / 2}"/></clipPath>',
        f'<rect x="{BAR_X}" y="{BAR_Y}" width="{BAR_W}" height="{BAR_H}" '
        f'rx="{BAR_H / 2}" fill="{palette["track"]}"/>',
        '<g clip-path="url(#barClip)">',
    ]
    offset = 0.0
    for _name, percent, color in languages:
        span = BAR_W * percent / 100.0
        out.append(
            f'<rect x="{BAR_X + offset:.2f}" y="{BAR_Y}" '
            f'width="{span:.2f}" height="{BAR_H}" fill="{color}"/>'
        )
        offset += span
    out.append("</g>")
    out.append(f'<g font-size="{LEGEND_FS}px" fill="{palette["fg"]}">')
    for index, (name, percent, color) in enumerate(languages):
        x = LEGEND_COLS[index % len(LEGEND_COLS)]
        y = LEGEND_ROWS[index // len(LEGEND_COLS)]
        out.append(f'<circle cx="{x + 4}" cy="{y - 4}" r="4" fill="{color}"/>')
        out.append(
            f'<text x="{x + 14}" y="{y}">{xml_escape(name)} '
            f'<tspan class="cc">{percent:.1f}%</tspan></text>'
        )
    out.append("</g>")
    return out


def build_card(
    palette: dict[str, str],
    art_path: Path,
    rows: list[str | None],
    languages: list[tuple[str, float, str]],
) -> str:
    chunks = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'font-family="ConsolasFallback,Consolas,monospace" '
            f'width="{CARD_W}px" height="{CARD_H}px" '
            f'viewBox="0 0 {CARD_W} {CARD_H}" font-size="{FS}px">'
        ),
        f"""<style>
@font-face {{
src: local('Consolas'), local('Consolas Bold');
font-family: 'ConsolasFallback';
font-display: swap;
-webkit-size-adjust: 109%;
size-adjust: 109%;
}}
.key {{fill: {palette['key']};}}
.value {{fill: {palette['value']};}}
.addColor {{fill: {palette['add']};}}
.delColor {{fill: {palette['dele']};}}
.cc {{fill: {palette['cc']};}}
text, tspan {{white-space: pre;}}
</style>""",
        f'<rect width="{CARD_W}px" height="{CARD_H}px" fill="{palette["bg"]}" rx="15"/>',
    ]

    art_runs = read_art_runs(art_path)
    chunks.append(
        f'<text x="{ART_X}" y="{ART_Y0}" fill="{palette["art"]}" '
        f'font-size="{ART_FS}px" class="ascii">'
    )
    for index in range(ROWS):
        y = ART_Y0 + index * ART_LINE_H
        chunks.append(
            f'<tspan x="{ART_X}" y="{y}">'
            f'{art_line_markup(art_runs[index], palette["art"])}'
            f"</tspan>"
        )
    chunks.append("</text>")
    chunks.extend(language_markup(languages, palette))

    chunks.append(f'<text x="{X_RIGHT}" y="{Y0}" fill="{palette["fg"]}">')
    for index, row in enumerate(rows):
        if row is None:
            continue
        y = Y0 + index * LINE_H
        chunks.append(row.replace("{y}", str(y)))
    chunks.append("</text>")
    chunks.append("</svg>")
    return "\n".join(chunks) + "\n"


def assert_layout(rows: list[str | None]) -> None:
    assert ART_X + ART_W <= X_RIGHT, (ART_X + ART_W, X_RIGHT)
    assert X_RIGHT + WIDTH * (FS * 0.6) <= CARD_W
    assert ART_Y0 + (ROWS - 1) * ART_LINE_H < BAR_Y
    last_row_index = max(i for i, row in enumerate(rows) if row is not None)
    assert max(LEGEND_ROWS) < CARD_H
    assert Y0 + last_row_index * LINE_H < CARD_H

    strip_tags = re.compile(r"<[^>]+>")
    for index, row in enumerate(rows):
        if row is None:
            continue
        plain = strip_tags.sub("", row.replace("{y}", "0"))
        plain = (
            plain.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
        )
        if plain != ". ":
            assert len(plain) == WIDTH, (index, len(plain), plain)


def main() -> None:
    args = parse_args()
    stats = dict(STATS)
    load_stats_from_svg(args.stats_from, stats)
    languages = load_languages(args.languages)
    rows = build_rows(stats)
    assert_layout(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    dark = {**DARK, "art_source": args.art_dark}
    light = {**LIGHT, "art_source": args.art_light}
    for filename, palette in (("dark_mode.svg", dark), ("light_mode.svg", light)):
        svg = build_card(palette, palette["art_source"], rows, languages)
        ET.fromstring(svg)  # parse check
        (args.output_dir / filename).write_text(svg, encoding="utf-8")
        print(f"wrote {args.output_dir / filename}")

    for name, percent, color in languages:
        print(f"lang {name:20s} {percent:6.2f}%  {color}")


if __name__ == "__main__":
    main()
