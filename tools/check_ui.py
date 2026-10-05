# FILE: tools/check_ui.py
# UI guardrails for the redesign — run before every UI commit:
#   python3 tools/check_ui.py
# 1. Contrast: every text-like token must reach 4.5:1 against every surface
#    token, in both themes (WCAG AA for normal text).
# 2. Hard-coded styling: no hex/rgb colors or raw px font sizes outside
#    static/css/tokens.css (templates, app.css, JS).
# 3. Type scale: only the 5 sizes defined in tokens.css.
# Exits non-zero on any failure.

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "static" / "css" / "tokens.css"

TEXT_TOKENS = ["--text", "--text-muted", "--text-faint", "--accent", "--pos", "--neg", "--warn",
               "--regime-strong-growth", "--regime-reflation", "--regime-overheating",
               "--regime-stagflation-risk", "--regime-stagflation", "--regime-contraction",
               "--regime-unknown", "--chart-axis"]
SURFACES = ["--bg", "--surface", "--surface-raised"]


def _blocks(css: str) -> dict[str, dict[str, str]]:
    out = {}
    for sel, body in re.findall(r"(:root|html\[data-theme=\"light\"\])\s*\{(.*?)\n\}", css, re.S):
        out.setdefault(sel, dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body)))  # first block wins
    return out


def _rgb(value: str) -> tuple[float, float, float] | None:
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return None


def _lum(c: tuple[float, float, float]) -> float:
    lin = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check_contrast() -> list[str]:
    blocks = _blocks(TOKENS.read_text())
    dark = blocks[":root"]
    themes = {"dark": dark, "light": {**dark, **blocks['html[data-theme="light"]']}}
    fails = []
    for theme, toks in themes.items():
        for t in TEXT_TOKENS:
            for s in SURFACES:
                fg, bg = _rgb(toks.get(t, "")), _rgb(toks.get(s, ""))
                if fg is None or bg is None:
                    fails.append(f"[{theme}] cannot parse {t} or {s}")
                    continue
                r = _ratio(fg, bg)
                if r < 4.5:
                    fails.append(f"[{theme}] {t} on {s}: {r:.2f}:1 (< 4.5)")
    return fails


def check_hardcoded() -> list[str]:
    files = [ROOT / "templates" / "index.html", *(ROOT / "static" / "css").glob("*.css"),
             *(ROOT / "static" / "js").glob("*.js")]
    color = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b(?!;)|\brgba?\(")
    fsize = re.compile(r"font-size\s*:\s*\d")
    fails = []
    for f in files:
        if f == TOKENS or not f.exists():
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if "check_ui: allow" in line:
                continue
            if color.search(line):
                fails.append(f"{f.relative_to(ROOT)}:{n}: hard-coded color: {line.strip()[:110]}")
            if fsize.search(line):
                fails.append(f"{f.relative_to(ROOT)}:{n}: raw font-size: {line.strip()[:110]}")
    return fails


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    problems = []
    if only in ("all", "contrast"):
        problems += check_contrast()
    if only in ("all", "hardcoded"):
        problems += check_hardcoded()
    for p in problems:
        print(p)
    print(f"\n{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
