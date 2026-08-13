"""Report templates for "+ New report" — a designed starting point, not a
blank one.

Each template is the blank scaffold (docsync/new.py) plus three things: a
starter layout.json full of placed, editable elements; a palette for the
binding (the colours every colour menu offers first); and asset files copied
into the new project (the Hawaiʻi Appleseed logos). The renderer is the SAME
placed-canvas renderer a blank project gets — a template is data, never code —
so everything on the page moves, restyles and deletes like anything the
editor's own tools placed.

The designs are DIGESTED from Hawaiʻi Appleseed's published reports of this
cycle — hiappleseed.org/research, 2025–2026 — not invented. The house style,
measured off the PDFs ("A Fairer Tax Code for a Thriving Hawaiʻi" Jan 2026,
"Keiki Ride Free" Feb 2026, "Pedestrian Head Start" Mar 2026, "Stalled"
Oct 2025, "County Leadership in Combating Food Insecurity" Dec 2025):

  · a full-bleed duotone cover photo over a TOPIC COLOUR field — tax blue
    #1E6194, transit slate #354F52, policy-brief charcoal #232322 — with a
    heavy uppercase Glober title, the date in gold #FDCF21, and the white
    seedling lockup;
  · an inside cover on the same solid field: centred white lockup, gold
    hiappleseed.org, the mission paragraphs, TABLE OF CONTENTS in pale
    #A5CCB4 caps;
  · body pages: huge uppercase Glober headline over a hairline rule, Source
    Sans body with a bold-caps lead-in, a dark rounded callout box, topic-
    colour pull-quotes, "Figure 1." captions in #464646, chart teal #4E9685.

The PDFs set Glober and Source Sans Pro. Neither is on Google Fonts; per the
Appleseed brand guide the web stand-ins are Manrope (≈ Glober) and Source
Sans 3 (the successor of Source Sans Pro) — both in layout.py's FONTS.

The one-pager is digested from the same cycle's web one-pagers (the 2026
Testimony Analysis; the TFC 2027 priorities brief): kicker, Manrope title,
stat strip, argument columns in teal #00907A vs rust #C4602F.

Every coordinate is inches from the page's top-left, like any layout.json.
Keep every element (and every rotated element's bounding box) ON the page —
Layout.check_bounds() refuses a draft that hangs off it, and a template that
scaffolds an invalid draft bricks the very first render.
"""
from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"

# The topic colours a template can be created IN — the published cycle keys
# each report to one ("A Fairer Tax Code" blue, "Keiki Ride Free" slate,
# "Pedestrian Head Start" charcoal; teal is the cycle's chart colour, offered
# as the light option). A scheme recolours the cover fills and the accents
# that echo them; the picker offers these beside the template.
SCHEMES = {
    "blue": {"name": "Tax blue", "color": "#1E6194"},
    "slate": {"name": "Transit slate", "color": "#354F52"},
    "teal": {"name": "Teal", "color": "#4E9685"},
    "charcoal": {"name": "Charcoal", "color": "#232322"},
}

# The 2025–26 house palette, sampled from the published PDFs. Topic colours
# first — swapping a report's colour is one page-fill click plus swatches.
_HOUSE = {
    "blue": "#1E6194",       # taxes & budget (A Fairer Tax Code)
    "slate": "#354F52",      # transportation (Keiki Ride Free)
    "charcoal": "#232322",   # policy briefs (Pedestrian Head Start)
    "gold": "#FDCF21",       # dates, links, TOC accents
    "pale": "#A5CCB4",       # TOC type on dark fields
    "teal": "#4E9685",       # chart series
    "body": "#464646",       # captions, footers
}
_REPORT_PALETTE = ["#1E6194", "#FDCF21", "#A5CCB4", "#4E9685", "#354F52",
                   "#232322", "#464646", "#FFFFFF"]
_BRIEF_PALETTE = ["#232322", "#FDCF21", "#7E6B3B", "#1E6194", "#A5CCB4",
                  "#4E9685", "#464646", "#FFFFFF"]
# The 2026 web one-pagers' scheme: teal/rust data accents over slate/charcoal.
_ONEPAGER_PALETTE = ["#00907A", "#C4602F", "#2F3E46", "#354F52", "#52796F",
                     "#84A98C", "#CAD2C5", "#EDF1EC", "#FFFFFF"]

# Appleseed's own boilerplate, verbatim from every 2025–26 report's inside
# cover — part of the design, so part of the template.
_MISSION_1 = ("Hawaiʻi Appleseed is committed to a more socially and "
              "economically just Hawaiʻi, where everyone has genuine "
              "opportunities to achieve economic security and fulfill their "
              "potential. We change systems to address inequity and foster "
              "greater opportunity by conducting data analysis and research "
              "to address income inequality, educating policymakers and the "
              "public, engaging in collaborative problem solving and "
              "coalition building, and advocating for policy and systems "
              "change.")
_MISSION_2 = ("The work of Hawaiʻi Appleseed is about people. The issues we "
              "work on—housing, food, wages, mobility, the state budget and "
              "taxation, and racial and indigenous equity—are important "
              "because they ensure people have access to shelter, "
              "sustenance, and the means to survive and thrive individually "
              "and collectively.")
_COPYRIGHT = ("Copyright © 20XX Hawaiʻi Appleseed Center for Law & Economic "
              "Justice. All rights reserved.\n\n733 Bishop Street, Suite "
              "1180, Honolulu, HI 96813")

# Shared content.md: templates put their words in placed boxes (layout.json),
# so the source file holds only what every project needs — same as blank.
CONTENT_MD = """<!--
  This report started from the "{label}" template — the Hawaiʻi Appleseed
  house style, digested from the 2025–26 publications on
  hiappleseed.org/research. Everything on the pages is a placed text box or
  shape: click to edit, drag to move, Delete to remove. This file holds only
  what every project needs: the title, and the citation list "Cite" writes
  into.
-->

[[title]]
{name}

[[sources]]
[example]: Replace or delete this placeholder source — https://example.com
"""


def _report_layout(color: str | None = None) -> dict:
    """The full report, as published in 2025–26: topic-colour cover, solid
    inside cover with mission + contents, and a body page with the
    executive-summary pattern (headline, rule, lead-in, dark callout,
    pull-quote, figure caption). `color` is the scheme's topic colour —
    default the tax blue the 2026 flagship published in."""
    C = _HOUSE
    topic = color or C["blue"]
    return {
        "positions": {},
        "fill": {"page.1": topic, "page.2": topic},
        "shapes": [
            # Body-page hairline, full text width — as on the published
            # "INTRODUCTION" pages.
            {"id": "tpl-rule3", "page": 3, "kind": "rect",
             "x": 0.7, "y": 1.62, "w": 7.1, "h": 0.02, "fill": topic},
            # Short gold accent under TABLE OF CONTENTS.
            {"id": "tpl-tocrule2", "page": 2, "kind": "rect",
             "x": 3.65, "y": 6.22, "w": 1.2, "h": 0.035, "fill": C["gold"]},
            {"id": "tpl-rule4", "page": 4, "kind": "rect",
             "x": 0.7, "y": 1.62, "w": 7.1, "h": 0.02, "fill": topic},
        ],
        "boxes": [
            # ---- page 1: the cover --------------------------------------
            # A full-bleed placeholder standing where the published covers
            # set a duotone photo — translucent white/black layers, so the
            # page fill tints it and a topic-colour swap re-tints it free.
            {"id": "tpl-photo", "page": 1, "x": 0, "y": 0, "w": 8.5, "z": 1,
             "md": "![Cover photo placeholder]"
                   "(assets/cover-photo-placeholder.svg)"},
            {"id": "tpl-logo", "page": 1, "x": 6.35, "y": 0.6, "w": 1.55, "z": 3,
             "md": "![Hawaiʻi Appleseed](assets/appleseed-logo-white.svg)"},
            {"id": "tpl-title", "page": 1, "x": 0.7, "y": 1.05, "w": 5.9, "z": 3,
             "md": "Your report title here",
             "style": {"font": "Manrope", "weight": 800, "size": 58,
                       "color": "#FFFFFF", "case": "upper", "leading": 1.08}},
            {"id": "tpl-sub", "page": 1, "x": 0.7, "y": 4.7, "w": 3.9, "z": 3,
             "md": "A subtitle in spaced capitals that says what this report "
                   "shows",
             "style": {"font": "Source Sans 3", "size": 19, "color": "#FFFFFF",
                       "case": "upper", "tracking": 1.2, "leading": 1.4}},
            {"id": "tpl-date", "page": 1, "x": 0.7, "y": 6.6, "w": 2.6, "z": 3,
             "md": "MONTH 20XX",
             "style": {"font": "Source Sans 3", "weight": 700, "size": 14,
                       "color": C["gold"], "tracking": 1.5}},
            {"id": "tpl-covernote", "page": 1, "x": 0.7, "y": 9.9, "w": 4.8,
             "z": 3,
             "md": "The backdrop is a placeholder — swap it for a cover "
                   "photo (Insert ▸ Image, size to the page, send to back), "
                   "then delete this note.",
             "style": {"font": "Source Sans 3", "size": 11,
                       "color": "#DCE5E9", "leading": 1.4}},
            # ---- page 2: inside cover / contents ------------------------
            {"id": "tpl-logo2", "page": 2, "x": 2.9, "y": 0.85, "w": 2.7, "z": 2,
             "md": "![Hawaiʻi Appleseed](assets/appleseed-logo-white.svg)"},
            {"id": "tpl-url2", "page": 2, "x": 0.7, "y": 1.75, "w": 7.1, "z": 2,
             "md": "www.hiappleseed.org",
             "style": {"font": "Source Sans 3", "size": 15, "color": C["gold"],
                       "align": "center"}},
            {"id": "tpl-author2", "page": 2, "x": 0.7, "y": 2.12, "w": 7.1,
             "z": 2, "md": "Author: Your Name",
             "style": {"font": "Source Sans 3", "size": 14, "color": "#FFFFFF",
                       "align": "center"}},
            {"id": "tpl-mission2", "page": 2, "x": 0.85, "y": 2.85, "w": 6.8,
             "z": 2, "md": _MISSION_1 + "\n\n" + _MISSION_2,
             "style": {"font": "Source Sans 3", "size": 14.5,
                       "color": "#FFFFFF", "align": "center", "leading": 1.55}},
            {"id": "tpl-tochead2", "page": 2, "x": 0.7, "y": 5.6, "w": 7.1,
             "z": 2, "md": "TABLE OF CONTENTS",
             "style": {"font": "Manrope", "weight": 800, "size": 30,
                       "color": C["pale"], "align": "center"}},
            {"id": "tpl-toc2", "page": 2, "x": 1.5, "y": 6.5, "w": 5.5, "z": 2,
             "md": "Introduction — 3\n\nThe first section — 5\n\n"
                   "The second section — 9\n\nConclusion — 14\n\n"
                   "Endnotes — 15",
             "style": {"font": "Manrope", "weight": 700, "size": 16,
                       "color": C["pale"], "align": "center",
                       "leading": 1.55}},
            {"id": "tpl-copy2", "page": 2, "x": 0.7, "y": 9.7, "w": 7.1, "z": 2,
             "md": _COPYRIGHT,
             "style": {"font": "Source Sans 3", "size": 12, "color": "#FFFFFF",
                       "align": "center", "leading": 1.4}},
            {"id": "tpl-foot2", "page": 2, "x": 0.55, "y": 10.55, "w": 3.4,
             "z": 2, "md": "2 · YOUR REPORT",
             "style": {"font": "Manrope", "weight": 700, "size": 12,
                       "color": "#FFFFFF", "tracking": 0.8}},
            # ---- page 3: the body pattern -------------------------------
            {"id": "tpl-h3", "page": 3, "x": 0.7, "y": 0.7, "w": 7.1, "z": 2,
             "md": "Executive summary",
             "style": {"font": "Manrope", "weight": 800, "size": 44,
                       "color": C["charcoal"], "case": "upper",
                       "leading": 1.05}},
            {"id": "tpl-body3", "page": 3, "x": 0.7, "y": 1.95, "w": 4.35,
             "z": 2,
             "md": "**YOUR FIRST WORDS, IN BOLD CAPITALS,** set the stage the "
                   "way every report in this series opens. Source Sans "
                   "carries the body; keep paragraphs short and the argument "
                   "moving.\n\nA second paragraph continues here — replace "
                   "all of this, or delete the box and lay the page out your "
                   "own way.",
             "style": {"font": "Source Sans 3", "size": 15, "color": "#1F1F1F",
                       "leading": 1.5}},
            {"id": "tpl-callout3", "page": 3, "x": 5.3, "y": 1.95, "w": 2.5,
             "z": 2, "fill": "#303030",
             "md": "- **Key term** is defined in a sentence, white on the "
                   "dark panel.\n- **Second term** — one line each.\n"
                   "- **Third term** — kept tight.",
             "style": {"font": "Source Sans 3", "size": 13, "color": "#FFFFFF",
                       "leading": 1.45}},
            {"id": "tpl-pull3", "page": 3, "x": 0.7, "y": 5.6, "w": 7.1, "z": 2,
             "md": "IN TOTAL, THE HEADLINE FINDING GOES HERE — WITH THE "
                   "NUMBER **IN BOLD**.",
             "style": {"font": "Source Sans 3", "size": 21, "color": topic,
                       "leading": 1.35}},
            # Ghost chart standing where a real one goes — the caption below
            # no longer floats over blank paper.
            {"id": "tpl-chart3", "page": 3, "x": 0.7, "y": 6.45, "w": 7.1,
             "z": 1,
             "md": "![Chart placeholder](assets/chart-placeholder.svg)"},
            {"id": "tpl-fig3", "page": 3, "x": 0.7, "y": 9.5, "w": 7.1, "z": 2,
             "md": "**Figure 1.** A caption for the chart that goes here — "
                   "add one with the Chart tool; the series colours are in "
                   "the swatches.",
             "style": {"font": "Manrope", "size": 15, "color": C["body"]}},
            {"id": "tpl-foot3", "page": 3, "x": 4.9, "y": 10.55, "w": 2.9,
             "z": 2, "md": "3 · YOUR REPORT",
             "style": {"font": "Manrope", "weight": 700, "size": 12,
                       "color": C["body"], "align": "right",
                       "tracking": 0.8}},
            # ---- page 4: the section page, the unit the report repeats ---
            # Headline over the hairline, bold-caps lead-in, body, a
            # second-level heading in the topic colour — every style a
            # build-out needs, placed and copyable (and served as data by
            # style_guide() for a pilot laying out new pages).
            {"id": "tpl-h4", "page": 4, "x": 0.7, "y": 0.7, "w": 7.1, "z": 2,
             "md": "Your first section",
             "style": {"font": "Manrope", "weight": 800, "size": 44,
                       "color": C["charcoal"], "case": "upper",
                       "leading": 1.05}},
            {"id": "tpl-body4a", "page": 4, "x": 0.7, "y": 1.95, "w": 7.1,
             "z": 2,
             "md": "**THE SECTION'S FIRST WORDS, IN BOLD CAPITALS,** open it "
                   "the way every page of this report does. Source Sans "
                   "carries the body over the full text width; keep "
                   "paragraphs to three or four sentences and the argument "
                   "moving.\n\nThis page is the pattern the rest of the "
                   "report repeats — headline, hairline, body. Copy its "
                   "elements onto a new page and replace the words.",
             "style": {"font": "Source Sans 3", "size": 15, "color": "#1F1F1F",
                       "leading": 1.5}},
            {"id": "tpl-sub4", "page": 4, "x": 0.7, "y": 3.7, "w": 7.1,
             "z": 2, "md": "A second-level heading, in the topic colour",
             "style": {"font": "Manrope", "weight": 700, "size": 22,
                       "color": topic}},
            {"id": "tpl-body4b", "page": 4, "x": 0.7, "y": 4.2, "w": 7.1,
             "z": 2,
             "md": "Under a subheading the body simply continues. Numbers "
                   "that matter can step out into a pull-quote (page 3 shows "
                   "one), evidence into a chart with a **Figure** caption — "
                   "every device this report uses is already on these pages, "
                   "styled and movable.",
             "style": {"font": "Source Sans 3", "size": 15, "color": "#1F1F1F",
                       "leading": 1.5}},
            {"id": "tpl-foot4", "page": 4, "x": 4.9, "y": 10.55, "w": 2.9,
             "z": 2, "md": "4 · YOUR REPORT",
             "style": {"font": "Manrope", "weight": 700, "size": 12,
                       "color": C["body"], "align": "right",
                       "tracking": 0.8}},
        ],
        "tables": [],
    }


def _brief_layout(color: str | None = None) -> dict:
    """The policy-brief variant (Pedestrian Head Start, Mar 2026): charcoal
    and gold, the title block sitting LOW on the cover, and a body page whose
    headline carries a short gold rule. `color` recolours the cover field;
    the gold accents and charcoal body ink stay — they are the brief's
    identity, not its topic."""
    C = _HOUSE
    return {
        "positions": {},
        "fill": {"page.1": color or C["charcoal"]},
        "shapes": [
            {"id": "tpl-rule2", "page": 2, "kind": "rect",
             "x": 0.7, "y": 1.52, "w": 1.2, "h": 0.05, "fill": C["gold"]},
        ],
        "boxes": [
            # ---- page 1: the cover, title block low as published --------
            # Same tinting placeholder as the report cover — the charcoal
            # fill shows through the translucent layers.
            {"id": "tpl-photo", "page": 1, "x": 0, "y": 0, "w": 8.5, "z": 1,
             "md": "![Cover photo placeholder]"
                   "(assets/cover-photo-placeholder.svg)"},
            {"id": "tpl-kick", "page": 1, "x": 0.7, "y": 8.0, "w": 3.0, "z": 3,
             "md": "POLICY BRIEF",
             "style": {"font": "Source Sans 3", "weight": 700, "size": 14,
                       "color": C["gold"], "tracking": 1.5}},
            {"id": "tpl-title", "page": 1, "x": 0.7, "y": 8.35, "w": 5.2, "z": 3,
             "md": "Your brief title",
             "style": {"font": "Manrope", "weight": 800, "size": 44,
                       "color": "#FFFFFF", "case": "upper", "leading": 1.1}},
            {"id": "tpl-date", "page": 1, "x": 5.6, "y": 8.0, "w": 2.2, "z": 3,
             "md": "MONTH 20XX",
             "style": {"font": "Source Sans 3", "weight": 700, "size": 14,
                       "color": C["gold"], "align": "right",
                       "tracking": 1.5}},
            {"id": "tpl-logo", "page": 1, "x": 6.15, "y": 9.55, "w": 1.65,
             "z": 3,
             "md": "![Hawaiʻi Appleseed](assets/appleseed-logo-white.svg)"},
            {"id": "tpl-covernote", "page": 1, "x": 0.7, "y": 0.7, "w": 4.6,
             "z": 3,
             "md": "The backdrop is a placeholder — swap it for a cover "
                   "photo (Insert ▸ Image, size to the page, send to back), "
                   "then delete this note.",
             "style": {"font": "Source Sans 3", "size": 11,
                       "color": "#9C9C98", "leading": 1.4}},
            # ---- page 2: the body pattern -------------------------------
            {"id": "tpl-h2", "page": 2, "x": 0.7, "y": 0.7, "w": 7.1, "z": 2,
             "md": "Background",
             "style": {"font": "Manrope", "weight": 800, "size": 38,
                       "color": C["charcoal"], "case": "upper"}},
            {"id": "tpl-body2", "page": 2, "x": 0.7, "y": 1.85, "w": 7.1,
             "z": 2,
             "md": "**THE BRIEF'S FIRST WORDS, IN BOLD CAPITALS,** state the "
                   "problem in a sentence. Two or three short paragraphs "
                   "follow — what the policy is, where it works, what it "
                   "costs.\n\nReplace all of this, or delete the box and lay "
                   "the page out your own way.",
             "style": {"font": "Source Sans 3", "size": 15, "color": "#1F1F1F",
                       "leading": 1.5}},
            {"id": "tpl-rec2", "page": 2, "x": 0.7, "y": 4.6, "w": 7.1, "z": 2,
             "fill": "#F4F1E7",
             "md": "**Recommendations for Hawaiʻi**\n\n- The first "
                   "recommendation, one line.\n- The second.\n- The third.",
             "style": {"font": "Source Sans 3", "size": 14,
                       "color": C["charcoal"], "leading": 1.5}},
            {"id": "tpl-foot", "page": 2, "x": 4.9, "y": 10.55, "w": 2.9,
             "z": 2, "md": "2 · YOUR BRIEF",
             "style": {"font": "Manrope", "weight": 700, "size": 12,
                       "color": C["body"], "align": "right",
                       "tracking": 0.8}},
        ],
        "tables": [],
    }


def _onepager_layout() -> dict:
    """The 2026 web one-pager pattern (Testimony Analysis; the TFC brief):
    kicker, big Manrope title, bold lede, a three-tile stat strip, two
    argument columns under coloured headings, hairline rules, the wordmark
    as a footer."""
    teal, rust, ink, slate = "#00907A", "#C4602F", "#2F3E46", "#354F52"
    grey, hair = "#7C8A80", "#CAD2C5"
    tiles, boxes = [], []
    stats = [("1,234", "lead statistic"), ("56", "second statistic"),
             ("7", "third statistic")]
    for i, (num, lab) in enumerate(stats):
        x = 0.62 + i * 2.45
        tiles.append({"id": f"tpl-tbar{i + 1}", "page": 1, "kind": "rect",
                      "x": x, "y": 2.42, "w": 0.045, "h": 0.78, "fill": teal})
        boxes += [
            {"id": f"tpl-num{i + 1}", "page": 1, "x": x + 0.14, "y": 2.40,
             "w": 2.1, "z": 2, "md": num,
             "style": {"font": "Manrope", "weight": 800, "size": 26,
                       "color": ink}},
            {"id": f"tpl-lab{i + 1}", "page": 1, "x": x + 0.14, "y": 2.86,
             "w": 2.1, "z": 2, "md": lab,
             "style": {"font": "Poppins", "size": 11, "color": grey}},
        ]
    return {
        "positions": {},
        "fill": {},
        "shapes": tiles + [
            {"id": "tpl-rule1", "page": 1, "kind": "rect",
             "x": 0.62, "y": 3.52, "w": 7.26, "h": 0.018, "fill": hair},
            {"id": "tpl-rule2", "page": 1, "kind": "rect",
             "x": 0.62, "y": 9.92, "w": 7.26, "h": 0.018, "fill": hair},
        ],
        "boxes": boxes + [
            {"id": "tpl-kick", "page": 1, "x": 0.62, "y": 0.62, "w": 7.2, "z": 2,
             "md": "HAWAIʻI APPLESEED · KICKER LINE",
             "style": {"font": "Manrope", "weight": 800, "size": 11,
                       "color": "#52796F", "tracking": 1.8}},
            {"id": "tpl-title", "page": 1, "x": 0.62, "y": 0.92, "w": 7.26,
             "z": 2, "md": "What this one-pager says",
             "style": {"font": "Manrope", "weight": 800, "size": 30,
                       "color": ink, "leading": 1.1}},
            {"id": "tpl-lede", "page": 1, "x": 0.62, "y": 1.55, "w": 7.26,
             "z": 2,
             "md": "**The single most important sentence goes first, in "
                   "bold.** Two or three supporting lines follow in Poppins "
                   "— who this is for, what it shows, what to do about it.",
             "style": {"font": "Poppins", "size": 12.5, "color": slate,
                       "leading": 1.5}},
            {"id": "tpl-colh1", "page": 1, "x": 0.62, "y": 3.75, "w": 3.5,
             "z": 2, "md": "What supporters argued",
             "style": {"font": "Manrope", "weight": 700, "size": 15,
                       "color": teal}},
            {"id": "tpl-col1", "page": 1, "x": 0.62, "y": 4.12, "w": 3.5,
             "z": 2,
             "md": "- **The first argument** — and the evidence for it.\n"
                   "- **The second argument**, one line each.\n"
                   "- **The third**, kept tight.",
             "style": {"font": "Poppins", "size": 12, "color": slate,
                       "leading": 1.45}},
            {"id": "tpl-colh2", "page": 1, "x": 4.38, "y": 3.75, "w": 3.5,
             "z": 2, "md": "What opponents argued",
             "style": {"font": "Manrope", "weight": 700, "size": 15,
                       "color": rust}},
            {"id": "tpl-col2", "page": 1, "x": 4.38, "y": 4.12, "w": 3.5,
             "z": 2,
             "md": "- **The counter-argument** — stated fairly.\n"
                   "- **The second**, with its source.\n"
                   "- **The third.**",
             "style": {"font": "Poppins", "size": 12, "color": slate,
                       "leading": 1.45}},
            {"id": "tpl-logo", "page": 1, "x": 0.62, "y": 10.12, "w": 1.9,
             "z": 2, "md": "![Hawaiʻi Appleseed](assets/appleseed-logo.svg)"},
            {"id": "tpl-foot", "page": 1, "x": 5.4, "y": 10.25, "w": 2.48,
             "z": 2, "md": "hiappleseed.org",
             "style": {"font": "Poppins", "size": 10, "color": grey,
                       "align": "right"}},
        ],
        "tables": [],
    }


# What "+ New report" offers beyond the blank canvas. `assets` names files in
# ASSETS to copy into the new project's assets dir; `page` fixes the sheet
# (a designed layout is measured against one size, so the size picker stands
# down when a template is chosen). `schemes` lists the SCHEMES ids the
# template can be created in, its own default first — the layout callable
# takes the scheme's colour as its one optional argument.
TEMPLATES = {
    "appleseed-report": {
        "name": "Hawaiʻi Appleseed report",
        "blurb": "Cover, contents and body page in the published 2025–26 "
                 "house style",
        "page": (8.5, 11.0),
        "pages": 4,
        "palette": _REPORT_PALETTE,
        "layout": _report_layout,
        "schemes": ["blue", "slate", "teal", "charcoal"],
        "assets": ["appleseed-logo.svg", "appleseed-logo-white.svg",
                   "cover-photo-placeholder.svg", "chart-placeholder.svg"],
    },
    "appleseed-brief": {
        "name": "Hawaiʻi Appleseed policy brief",
        "blurb": "Charcoal-and-gold cover and body page, as the 2026 briefs "
                 "publish",
        "page": (8.5, 11.0),
        "pages": 2,
        "palette": _BRIEF_PALETTE,
        "layout": _brief_layout,
        "schemes": ["charcoal", "slate", "blue", "teal"],
        "assets": ["appleseed-logo.svg", "appleseed-logo-white.svg",
                   "cover-photo-placeholder.svg"],
    },
    "appleseed-onepager": {
        "name": "Hawaiʻi Appleseed one-pager",
        "blurb": "Kicker, stat strip and argument columns — the 2026 web "
                 "one-pager pattern",
        "page": (8.5, 11.0),
        "pages": 1,
        "palette": _ONEPAGER_PALETTE,
        "layout": _onepager_layout,
        "assets": ["appleseed-logo.svg", "appleseed-logo-white.svg"],
    },
}


def style_guide() -> dict:
    """The house style as DATA, for a pilot — an AI driving the editor, a
    script — to build out a report that matches the templates. Every "style"
    dict is exactly what a text box's layout.json "style" (and the pilot's
    addTextBox({style:…}) / setStyle) accepts; "shape" dicts are addShape()
    arguments. Served with /__templates and by the MCP server's style_guide
    tool, so the recipe travels with the running editor."""
    C = _HOUSE
    return {
        "fonts": {"display": "Manrope",
                  "body": "Source Sans 3",
                  "note": "the print house fonts are Glober + Source Sans "
                          "Pro; these are the brand guide's web stand-ins"},
        "schemes": [{"id": k, **v} for k, v in SCHEMES.items()],
        "accents": {"gold": C["gold"], "pale": C["pale"], "chart": C["teal"],
                    "captions": C["body"]},
        "page": {"w": 8.5, "h": 11.0, "margin": 0.7, "text_w": 7.1,
                 "footer_y": 10.55},
        "patterns": {
            "section_heading": {
                "at": {"x": 0.7, "y": 0.7, "w": 7.1},
                "style": {"font": "Manrope", "weight": 800, "size": 44,
                          "case": "upper", "color": C["charcoal"],
                          "leading": 1.05}},
            "hairline": {
                "shape": {"kind": "rect", "x": 0.7, "y": 1.62, "w": 7.1,
                          "h": 0.02},
                "note": "fill = the report's topic colour (schemes)"},
            "subheading": {
                "style": {"font": "Manrope", "weight": 700, "size": 22},
                "note": "color = the topic colour"},
            "body": {
                "at": {"x": 0.7, "w": 7.1},
                "style": {"font": "Source Sans 3", "size": 15,
                          "color": "#1F1F1F", "leading": 1.5},
                "note": "a section's first body box opens with a "
                        "**BOLD CAPITALS** lead-in"},
            "callout": {
                "fill": "#303030",
                "style": {"font": "Source Sans 3", "size": 13,
                          "color": "#FFFFFF", "leading": 1.45},
                "note": "dark definitions panel, bulleted, ~2.5in wide "
                        "beside a narrowed body column"},
            "pull_quote": {
                "style": {"font": "Source Sans 3", "size": 21,
                          "leading": 1.35},
                "note": "color = the topic colour; the number in **bold**"},
            "figure_caption": {
                "style": {"font": "Manrope", "size": 15, "color": C["body"]},
                "note": "starts \"**Figure N.**\"; sits under the chart"},
            "footer": {
                "at": {"x": 4.9, "y": 10.55, "w": 2.9},
                "style": {"font": "Manrope", "weight": 700, "size": 12,
                          "color": C["body"], "align": "right",
                          "tracking": 0.8},
                "note": "\"<page> · <REPORT TITLE>\"; white, left-aligned "
                        "at x 0.55 on solid colour pages"},
        },
        "recipe": [
            "Scaffold: POST /__scaffold {name, slug, template: "
            "'appleseed-report' | 'appleseed-brief', scheme} (or python3 -m "
            "docsync.new --template … --scheme …). Page 4 of the report IS "
            "the section pattern, already placed.",
            "Each further section: pilot addPage, then ONE batch of "
            "addTextBox/addShape ops per the patterns above — heading, "
            "hairline (addShape rect, fill = topic colour, z 2), body "
            "boxes, footer.",
            "Prose into the placed boxes via setBoxText; citations via "
            "addSource + [^id] tokens in the text.",
            "Charts: the editor's Chart tool, where the template's chart "
            "placeholder sits; caption per figure_caption.",
            "audit() after laying out a page — it reports overflow, "
            "off-sheet and overlaps as data; save() when clean.",
        ],
    }


def listing() -> list[dict]:
    """What a picker needs, in registry order — no layout payloads. Each
    template's schemes come resolved (id, name, swatch colour), its own
    default first, so a picker can draw them without knowing SCHEMES."""
    return [{"id": tid, "name": t["name"], "blurb": t["blurb"],
             "page": {"w": t["page"][0], "h": t["page"][1]},
             "schemes": [{"id": s, **SCHEMES[s]}
                         for s in t.get("schemes", [])]}
            for tid, t in TEMPLATES.items()]
