#!/usr/bin/env python3
"""Verify the design tokens meet WCAG 2.2 AA in both themes.

Parses the oklch() custom properties out of globals.css, converts them to sRGB,
and checks every pairing the UI actually renders — including each activity hue
on its own tinted surface, which is the pairing most likely to drift when
someone nudges a colour.

Usage:
    python3 scripts/check-contrast.py frontend/src/styles/globals.css
"""

from __future__ import annotations

import math
import re
import sys


ACTIVITY_KINDS = ("think", "tool", "browser", "file", "terminal", "error")
# AA: 4.5:1 for text, 3:1 for icons and other non-text graphics.
TEXT_MIN = 4.5
GRAPHIC_MIN = 3.0


def oklch_to_linear_srgb(lightness: float, chroma: float, hue: float) -> tuple:
    hue_radians = math.radians(hue)
    a = chroma * math.cos(hue_radians)
    b = chroma * math.sin(hue_radians)

    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3

    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def relative_luminance(color: tuple) -> float:
    red, green, blue = (max(0.0, min(1.0, c)) for c in oklch_to_linear_srgb(*color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: tuple, background: tuple) -> float:
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def parse_theme(css: str, selector: str) -> dict:
    """Collect the oklch tokens declared in one theme block."""
    start = css.index(selector)
    body = css[start : css.index("}", start)]
    return {
        name: (float(lightness), float(chroma), float(hue))
        for name, lightness, chroma, hue in re.findall(
            r"--([\w-]+):\s*oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)", body
        )
    }


def pairings(tokens: dict) -> list:
    background, card = tokens["background"], tokens["card"]
    checks = [
        ("foreground on background", tokens["foreground"], background, TEXT_MIN),
        (
            "muted-foreground on background",
            tokens["muted-foreground"],
            background,
            TEXT_MIN,
        ),
        ("muted-foreground on card", tokens["muted-foreground"], card, TEXT_MIN),
        (
            "brand-foreground on brand",
            tokens["brand-foreground"],
            tokens["brand"],
            TEXT_MIN,
        ),
        ("brand on card", tokens["brand"], card, GRAPHIC_MIN),
    ]
    for kind in ACTIVITY_KINDS:
        base = tokens[f"activity-{kind}"]
        checks.append((f"{kind} on card", base, card, TEXT_MIN))
        checks.append(
            (
                f"{kind} on own surface",
                base,
                tokens[f"activity-{kind}-surface"],
                TEXT_MIN,
            )
        )
    return checks


def report(tokens: dict, label: str) -> int:
    print(f"\n=== {label} ===")
    failures = 0
    for name, foreground, background, minimum in pairings(tokens):
        ratio = contrast_ratio(foreground, background)
        passed = ratio >= minimum
        failures += 0 if passed else 1
        print(
            f"  {'PASS' if passed else 'FAIL'}  {ratio:5.2f}:1  (need {minimum})  {name}"
        )
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    css = open(sys.argv[1]).read()
    failures = report(parse_theme(css, ":root {"), "LIGHT")
    failures += report(parse_theme(css, ".dark {"), "DARK")

    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
