#!/usr/bin/env python3
"""Exploratory script to find icon-only buttons/links without aria-label."""

import glob
import re

files = glob.glob("templates/**/*.html", recursive=True)
count = 0
exs = []

for f in files:
    with open(f) as fh:
        content = fh.read()

    # Match button or a tags that contain ONLY an SVG (with optional whitespace)
    pattern = r"(<(?:button|a)\b[^>]*>)\s*(<svg\b[^>]*>.*?</svg>)\s*(</(?:button|a)>)"
    matches = re.findall(pattern, content, re.DOTALL)
    for m in matches:
        opening = m[0]
        if "aria-label" not in opening:
            count += 1
            if len(exs) < 30:
                exs.append((f, opening[:200]))

print(f"Total: {count}")
for e in exs:
    print(f"  {e[0]}")
    print(f"    {e[1]}")
    print()
