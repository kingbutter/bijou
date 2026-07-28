#!/usr/bin/env python3
"""
Build the demo-mode posters.

Six original SVG posters, inlined into app/static/index.html so ?demo=1 needs
neither Plex nor a network. Run this after editing the art, then paste the
DEMO_POSTERS array it prints into index.html.

    python3 tools/gen_posters.py            # print the array
    python3 tools/gen_posters.py --preview  # also write PNGs to /tmp (needs cairosvg)
"""

W, H = 200, 300

# A shared vocabulary so the set reads as one design language:
# heavy condensed title, a credit line above, a billing block below.
DEFS = """<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/>
</linearGradient>
<radialGradient id="glow" cx="50%" cy="{gy}%" r="55%">
  <stop offset="0" stop-color="{g1}" stop-opacity=".95"/>
  <stop offset="1" stop-color="{g1}" stop-opacity="0"/>
</radialGradient>
</defs>"""


def grain(seed=0, n=48):
    """Faint speckle so the flats don't read as flat vector art."""
    import random
    r = random.Random(seed)
    d = "".join(f"M{r.randint(0,W)} {r.randint(0,H)}h.6" for _ in range(n))
    return f'<path d="{d}" stroke="#fff" stroke-width=".9" stroke-linecap="round" opacity=".055"/>'


def billing(y=272, color="#fff", op=".5"):
    """The unreadable credit block every poster has along the bottom."""
    rows = [(28, 144, 2.4), (46, 108, 1.6), (34, 132, 1.6), (52, 96, 1.6), (40, 120, 1.3)]
    out = []
    for i, (x, w, h) in enumerate(rows):
        out.append(f'<rect x="{x}" y="{y + i * 5}" width="{w}" height="{h}" '
                   f'fill="{color}" opacity="{op}" rx="{h/2:.1f}"/>')
    return "".join(out)


def credit(text, y=26, color="#fff", op=".55", size=5.4, ls=1.6):
    return (f'<text x="100" y="{y}" text-anchor="middle" '
            f'font-family="&#39;Saira Condensed&#39;,Helvetica,Arial,sans-serif" '
            f'font-size="{size}" letter-spacing="{ls}" fill="{color}" opacity="{op}">{text}</text>')


def title(lines, y, color="#fff", size=27, ls=.5, weight="bold", family=None, extra=""):
    fam = family or ("&#39;Big Shoulders Display&#39;,Haettenschweiler,&#39;Arial Narrow&#39;,"
                     "&#39;Liberation Sans Narrow&#39;,&#39;DejaVu Sans Condensed&#39;,Impact,sans-serif")
    out = []
    for i, ln in enumerate(lines):
        out.append(f'<text x="100" y="{y + i * (size * .88):.0f}" text-anchor="middle" '
                   f'font-family="{fam}" font-size="{size}" font-weight="{weight}" '
                   f'letter-spacing="{ls}" fill="{color}" {extra}>{ln}</text>')
    return "".join(out)


# ── The six ───────────────────────────────────────────────────────────

def spiral():
    """Saul Bass by way of Vertigo: a real logarithmic spiral."""
    import math
    pts = []
    t = 0.0
    while t < 8.6 * math.pi:
        r = 2.2 * math.exp(0.135 * t)
        pts.append((100 + r * math.cos(t), 130 + r * math.sin(t)))
        t += 0.14
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
{DEFS.format(a="#1b0b0b", b="#000", gy=42, g1="#e8452c")}
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<rect width="{W}" height="{H}" fill="url(#glow)" opacity=".55"/>
<path d="{d}" fill="none" stroke="#e8452c" stroke-width="4.2" stroke-linecap="round" opacity=".95"/>
<path d="{d}" fill="none" stroke="#f2b134" stroke-width="1.4" stroke-linecap="round"
      opacity=".8" transform="rotate(12 100 130)"/>
<circle cx="100" cy="130" r="4.5" fill="#f7f3e8"/>
{grain(1)}
{credit("A ROYAL ANTHEM PICTURE")}
{title(["THE","SPIRAL"], 232, "#f7f3e8", 34, 1.2)}
{billing()}
</svg>"""


def noir():
    """Venetian blinds, a hat, a long shadow."""
    blinds = "".join(
        f'<rect x="0" y="{y}" width="{W}" height="6" fill="#000" opacity=".55"/>'
        for y in range(34, 210, 15))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
{DEFS.format(a="#2a2e35", b="#07080a", gy=38, g1="#9fb2c4")}
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<rect width="{W}" height="{H}" fill="url(#glow)" opacity=".65"/>
{blinds}
<g fill="#04060a">
  <path d="M52 214c0-26 20-42 48-42s48 16 48 42z"/>
  <rect x="90" y="150" width="20" height="26" rx="8"/>
  <ellipse cx="100" cy="132" rx="22" ry="25"/>
  <ellipse cx="100" cy="112" rx="44" ry="7"/>
  <path d="M76 112c0-16 10-26 24-26s24 10 24 26z"/>
  <rect x="76" y="104" width="48" height="6" rx="2" fill="#0a0e14"/>
</g>
<rect x="0" y="120" width="200" height="5" fill="#7d8fa3" opacity=".16"/>
{grain(2, 70)}
{credit("PRODUCED FOR THE SCREEN")}
{title(["CELLAR","DOOR"], 230, "#e6ecf2", 33, 1.4)}
{billing(272, "#cfd8e2", ".45")}
</svg>"""


def scifi():
    """Ringed planet over a hard horizon."""
    stars = grain(3, 120)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
{DEFS.format(a="#0d1030", b="#020310", gy=40, g1="#4a6bd6")}
<rect width="{W}" height="{H}" fill="url(#sky)"/>
{stars}
<rect width="{W}" height="{H}" fill="url(#glow)" opacity=".5"/>
<circle cx="112" cy="112" r="46" fill="#11162e"/>
<circle cx="112" cy="112" r="46" fill="none" stroke="#7f9dff" stroke-width="1.4" opacity=".8"/>
<path d="M66 112a46 46 0 0 1 92 0z" fill="#243a86" opacity=".55"/>
<ellipse cx="112" cy="118" rx="72" ry="15" fill="none" stroke="#8fa9ff"
         stroke-width="2.6" opacity=".75" transform="rotate(-16 112 118)"/>
<ellipse cx="112" cy="118" rx="62" ry="11" fill="none" stroke="#c9d6ff"
         stroke-width="1" opacity=".5" transform="rotate(-16 112 118)"/>
<path d="M0 208h200v92H0z" fill="#05060f"/>
<path d="M0 208c40-14 62 6 98-2s58-16 102-6v10H0z" fill="#0a0e22"/>
<g stroke="#7f9dff" stroke-width=".8" opacity=".35">
  <path d="M20 208v-12M180 208v-12"/>
</g>
{credit("IN SPECTACULAR WIDESCREEN", 26, "#aebcff")}
{title(["SIGNAL","FROM AFAR"], 240, "#dfe6ff", 27, 1.1)}
{billing(276, "#9fb0ff", ".45")}
</svg>"""


def western():
    """Low sun, mesas, a rider."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
{DEFS.format(a="#f2a341", b="#7a2c14", gy=52, g1="#ffd88a")}
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<circle cx="100" cy="150" r="62" fill="#ffcf6b" opacity=".9"/>
<g fill="#f2a341" opacity=".55">
  <rect x="0" y="112" width="200" height="4"/><rect x="0" y="124" width="200" height="5"/>
  <rect x="0" y="138" width="200" height="6"/>
</g>
<path d="M0 176h44l10-26h26l8 26h34l12-34h22l10 34h34v42H0z" fill="#5d2110"/>
<path d="M0 200c30-8 44 4 74 0s54-10 126-2v34H0z" fill="#3a1409"/>
<g fill="#230c05">
  <ellipse cx="100" cy="212" rx="17" ry="3.5" opacity=".5"/>
  <ellipse cx="100" cy="146" rx="17" ry="3.4"/>
  <path d="M91 146c0-7 4-11 9-11s9 4 9 11z"/>
  <rect x="97" y="152" width="6" height="6"/>
  <path d="M88 158h24l5 30-6 2-4-18-2 38h-6l-3-24-3 24h-6l-2-38-4 18-6-2z"/>
  <path d="M86 210h11l-1 4H85zM103 210h11l1 4h-12z"/>
</g>
{grain(4, 50)}
{credit("A MOTION PICTURE", 26, "#4a1a0c", ".65")}
{title(["THE LONG","WAY DOWN"], 250, "#2a0e06", 25, .8)}
{billing(280, "#2a0e06", ".4")}
</svg>"""


def horror():
    """House, moon, bare tree."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
{DEFS.format(a="#14202a", b="#03060a", gy=32, g1="#8fd0c0")}
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<rect width="{W}" height="{H}" fill="url(#glow)" opacity=".55"/>
<circle cx="100" cy="92" r="40" fill="#dff3ec" opacity=".92"/>
<circle cx="88" cy="82" r="6" fill="#c3ddd6" opacity=".5"/>
<circle cx="110" cy="102" r="9" fill="#c3ddd6" opacity=".35"/>
<circle cx="106" cy="76" r="4" fill="#c3ddd6" opacity=".4"/>
<g fill="#040709">
  <path d="M62 214V150l38-26 38 26v64z"/>
  <path d="M54 152l46-32 46 32-6 6-40-28-40 28z"/>
  <path d="M84 214v-26h14v26zM112 168h14v14h-14z"/>
</g>
<rect x="86" y="190" width="10" height="24" fill="#ffd98a" opacity=".9"/>
<rect x="113" y="170" width="12" height="11" fill="#ffd98a" opacity=".75"/>
<g stroke="#04080b" stroke-width="3" fill="none" stroke-linecap="round">
  <path d="M28 240V150"/><path d="M28 186c-8-10-14-12-20-22M28 170c9-9 15-10 22-20M28 204c-7-8-13-9-18-16"/>
</g>
<path d="M0 236c46-10 70 6 104 2s58-12 96-4v66H0z" fill="#03060a"/>
{grain(5, 60)}
{credit("YOU HAVE BEEN WARNED", 26, "#a9cfc6")}
{title(["STATIC","AND SNOW"], 250, "#e8f5f1", 26, 1)}
{billing(280, "#9fc2ba", ".45")}
</svg>"""


def jazz():
    """Piano keys in perspective, one struck."""
    keys = "".join(
        f'<rect x="{18 + i * 16.4:.1f}" y="150" width="13" height="76" rx="1.5" '
        f'fill="{"#f5efe0" if i != 5 else "#e8452c"}" opacity=".95"/>' for i in range(10))
    blacks = "".join(
        f'<rect x="{28 + i * 16.4:.1f}" y="150" width="8" height="46" rx="1" fill="#16120e"/>'
        for i in [0, 1, 3, 4, 5, 7, 8])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
{DEFS.format(a="#2a1633", b="#0a0510", gy=36, g1="#c86ad0")}
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<rect width="{W}" height="{H}" fill="url(#glow)" opacity=".6"/>
<g opacity=".22" stroke="#f2c14e" stroke-width="1.2" fill="none">
  <path d="M100 148L14 40M100 148L54 26M100 148l46-122M100 148l86 108M100 148L186 40"/>
</g>
<g transform="skewY(-6) translate(0 14)">{keys}{blacks}</g>
<circle cx="100" cy="80" r="26" fill="none" stroke="#f2c14e" stroke-width="1.6" opacity=".7"/>
<circle cx="100" cy="80" r="17" fill="none" stroke="#f2c14e" stroke-width="1" opacity=".45"/>
{grain(6, 70)}
{credit("WITH SOUND BY WESTERN ELECTRIC", 26, "#f0cf8d", ".6", 4.8, 1.2)}
{title(["EIGHTY-EIGHT","KEYS"], 254, "#f7e9c8", 24, .9)}
{billing(282, "#e6c98f", ".45")}
</svg>"""


POSTERS = [
    ("The Spiral",      "1958", "NR",    "1h 42m", spiral),
    ("Cellar Door",     "1949", "NR",    "1h 28m", noir),
    ("Signal From Afar","1961", "PG",    "2h 07m", scifi),
    ("The Long Way Down","1955", "PG-13","1h 55m", western),
    ("Static and Snow", "1972", "R",     "1h 36m", horror),
    ("Eighty-Eight Keys","1964", "NR",   "2h 21m", jazz),
]

def build():
    import re
    out = []
    for title_, year, rating, run, fn in POSTERS:
        svg = re.sub(r"\s*\n\s*", " ", fn()).strip()
        svg = svg.replace('<svg xmlns="http://www.w3.org/2000/svg" ',
                          '<svg preserveAspectRatio="xMidYMid meet" ')
        out.append((title_, [year, rating, run], svg))
    return out


def emit(rows):
    import json
    body = ",\n".join(
        f"    {{t:{json.dumps(t)}, m:{json.dumps(m)}, s:{json.dumps(sv)}}}"
        for t, m, sv in rows)
    return "  const DEMO_POSTERS = [\n" + body + "\n  ];"


if __name__ == "__main__":
    import sys

    rows = build()

    if "--preview" in sys.argv:
        import cairosvg
        for i, (title_, _meta, svg) in enumerate(rows):
            cairosvg.svg2png(
                bytestring=svg.replace(
                    "<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1).encode(),
                write_to=f"/tmp/poster{i}.png", output_width=300, output_height=450)
            print(f"{title_:20} {len(svg):5} chars  -> /tmp/poster{i}.png",
                  file=sys.stderr)
        print(f"total {sum(len(sv) for _, _, sv in rows)} chars", file=sys.stderr)

    print(emit(rows))
