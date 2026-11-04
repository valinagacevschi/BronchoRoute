"""Generate three BronchoRoute icon concepts as 1024x1024 macOS-style SVGs."""
from pathlib import Path

OUT = Path(__file__).parent

# macOS Big Sur grid: 824px tile at offset 100, ~185px corner radius.
TILE = 'x="100" y="100" width="824" height="824" rx="185"'
SHADOW = """<filter id="sh" x="-20%" y="-20%" width="140%" height="140%">
  <feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#000" flood-opacity="0.28"/></filter>"""

# Viewer-left lung; mirrored for the other side.
LUNG = ("M440 300 C380 300 300 380 275 500 C255 600 250 700 280 760 "
        "C340 790 420 762 478 730 C490 640 486 520 472 420 C466 350 462 300 440 300 Z")
MIRROR = 'transform="translate(1024 0) scale(-1 1)"'


def svg(defs, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
<defs>{SHADOW}{defs}</defs>
{body}
</svg>
"""


# A — Route Tree: bronchial tree with one highlighted route to a target.
TREE = [
    (34, "M512 200 L512 420"),
    (26, "M512 420 C480 445 440 460 410 490"),
    (26, "M512 420 C545 445 585 460 615 490"),
    (16, "M420 482 C395 445 368 410 350 370"),
    (16, "M604 482 C630 445 656 410 674 370"),
    (18, "M410 490 C380 520 350 548 332 595"),
    (18, "M410 490 C410 545 402 600 398 668"),
    (18, "M615 490 C645 520 674 548 692 595"),
    (18, "M615 490 C622 545 640 600 662 650"),
    (10, "M350 370 C335 345 325 330 318 310"), (10, "M350 370 C370 345 385 330 392 312"),
    (10, "M674 370 C689 345 699 330 706 310"), (10, "M674 370 C654 345 639 330 632 312"),
    (10, "M332 595 C312 625 300 650 292 690"), (10, "M332 595 C345 630 352 660 350 700"),
    (10, "M398 668 C392 700 385 720 378 745"), (10, "M398 668 C412 700 425 720 438 738"),
    (10, "M692 595 C712 625 724 650 732 690"), (10, "M692 595 C680 630 674 660 676 700"),
]
ROUTE_A = "M512 200 L512 420 C545 445 585 460 615 490 C622 545 640 600 662 650 L684 690"
a = svg(
    """<linearGradient id="bgA" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#1A2E39"/><stop offset="1" stop-color="#0C1216"/></linearGradient>
<radialGradient id="glowA"><stop offset="0" stop-color="#F08A5D" stop-opacity="0.55"/>
  <stop offset="1" stop-color="#F08A5D" stop-opacity="0"/></radialGradient>""",
    f"""<rect {TILE} fill="url(#bgA)" filter="url(#sh)"/>
<g fill="#5E93A8" fill-opacity="0.10" stroke="#5E93A8" stroke-opacity="0.35" stroke-width="5">
  <path d="{LUNG}"/><path d="{LUNG}" {MIRROR}/></g>
<g fill="none" stroke="#7FB0C4" stroke-linecap="round" stroke-linejoin="round">
{''.join(f'<path d="{d}" stroke-width="{w}"/>' for w, d in TREE)}</g>
<circle cx="684" cy="690" r="90" fill="url(#glowA)"/>
<path d="{ROUTE_A}" fill="none" stroke="#F08A5D" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="684" cy="690" r="44" fill="none" stroke="#F08A5D" stroke-width="6" stroke-opacity="0.6"/>
<circle cx="684" cy="690" r="22" fill="#F08A5D"/>""",
)

# B — Lung Map: coral lungs on a map grid, dashed route to a map pin.
cx, cy, r = 690, 596, 56
PIN = (f"M{cx} {cy + 1.9 * r} C{cx - 0.2 * r} {cy + 1.3 * r} {cx - r} {cy + 0.75 * r} {cx - r} {cy} "
       f"A{r} {r} 0 1 1 {cx + r} {cy} C{cx + r} {cy + 0.75 * r} {cx + 0.2 * r} {cy + 1.3 * r} {cx} {cy + 1.9 * r} Z")
grid = "".join(f'<path d="M{v} 100 V924 M100 {v} H924"/>' for v in range(160, 924, 64))
b = svg(
    """<linearGradient id="bgB" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#E3EAEE"/></linearGradient>
<linearGradient id="lungB" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#E58466"/><stop offset="1" stop-color="#B4522F"/></linearGradient>
<clipPath id="tileB"><rect x="100" y="100" width="824" height="824" rx="185"/></clipPath>""",
    f"""<rect {TILE} fill="url(#bgB)" filter="url(#sh)"/>
<g clip-path="url(#tileB)" stroke="#C7D3DA" stroke-width="3">{grid}</g>
<g fill="url(#lungB)"><path d="{LUNG}"/><path d="{LUNG}" {MIRROR}/></g>
<path d="M512 210 L512 420 M512 420 C480 445 450 462 425 488 M512 420 C545 445 575 462 600 488"
  fill="none" stroke="#17242B" stroke-width="30" stroke-linecap="round"/>
<path d="M600 488 C592 560 588 640 610 690 C630 728 670 724 {cx} {cy + 1.9 * r + 4}" fill="none" stroke="#FFFFFF"
  stroke-width="12" stroke-linecap="round" stroke-dasharray="1 24"/>
<ellipse cx="{cx}" cy="{cy + 1.9 * r + 4}" rx="30" ry="10" fill="#17242B" fill-opacity="0.25"/>
<path d="{PIN}" fill="#2E6E88" stroke="#FFFFFF" stroke-width="8"/>
<circle cx="{cx}" cy="{cy}" r="24" fill="#FFFFFF"/>""",
)

# C — Topo Lungs: lungs as contour lines of a terrain map; route climbs to the summit.
def contours(px, py, turn):
    """Rings shrink toward an off-centre peak and twist slightly, like terrain."""
    rings = []
    for i, s in enumerate((1.0, 0.78, 0.57, 0.38, 0.2)):
        dx, dy = px * (1 - s), py * (1 - s)
        op = 0.40 + 0.14 * i
        rings.append(f'<path d="{LUNG}" transform="rotate({turn * i} {px} {py}) translate({dx:.1f} {dy:.1f}) '
                     f'scale({s})" stroke-opacity="{op:.2f}" stroke-width="{7 / s:.1f}"/>')
    return "".join(rings)


c = svg(
    """<radialGradient id="bgC" cx="0.5" cy="0.35" r="0.8">
  <stop offset="0" stop-color="#1D3440"/><stop offset="1" stop-color="#0A1014"/></radialGradient>""",
    f"""<rect {TILE} fill="url(#bgC)" filter="url(#sh)"/>
<g fill="none" stroke="#6DB3C9" stroke-linejoin="round">
  <g>{contours(345, 650, -4)}</g>
  <g {MIRROR}>{contours(375, 545, 5)}</g></g>
<path d="M512 230 L512 420 C560 450 610 470 630 500 C645 520 648 535 649 545" fill="none" stroke="#F08A5D"
  stroke-width="12" stroke-linecap="round" stroke-dasharray="26 20"/>
<g stroke="#F08A5D" stroke-width="13" stroke-linecap="round">
  <path d="M627 523 L671 567 M671 523 L627 567"/></g>
<circle cx="512" cy="230" r="16" fill="#F08A5D"/>""",
)

for name, doc in (("A-route-tree", a), ("B-lung-map", b), ("C-topo-lungs", c)):
    (OUT / f"{name}.svg").write_text(doc)
    print(name)
