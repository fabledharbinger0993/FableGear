"""Studio Lighting (the booth theme) red-shifts the app by overriding colour variables.

A colour variable the theme forgets, or a colour written straight into a rule, stays
blue/pink/violet in a dark booth. The Chop Shop kept its hot-pink rail and its navy/violet
panels because --accent-b (which --room-accent is built on) was never overridden.
"""
import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "fablegear"
CSS = (ROOT / "static/fablegear.css").read_text()
EXT = (ROOT / "static/shared/ui_extras.css").read_text()


def _root_tokens() -> dict[str, str]:
    tokens = {}
    for block in re.findall(r":root\s*\{(.*?)\n\}", CSS, re.S):
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block):
            tokens.setdefault(m.group(1), m.group(2).strip())
    return tokens


def _booth_tokens() -> set[str]:
    block = re.search(r'body\[data-theme="booth"\]\s*\{(.*?)\n\}', EXT, re.S).group(1)
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", block))


def _rgb(value: str):
    m = re.match(r"#([0-9a-fA-F]{6})\b", value)
    if m:
        return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    m = re.match(r"^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$", value) or re.match(r"rgba?\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value)
    return tuple(int(x) for x in m.groups()[:3]) if m else None


def _off_red(rgb) -> bool:
    h, lightness, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    return s > 0.28 and 0.08 < lightness < 0.97 and not (h * 360 >= 340 or h * 360 <= 48)


def test_booth_overrides_every_colour_variable_that_is_not_already_red():
    missing = []
    for name, value in _root_tokens().items():
        rgb = _rgb(value)
        if rgb and _off_red(rgb) and name not in _booth_tokens():
            missing.append(f"{name}: {value}")
    assert not missing, "colour variables the booth theme never red-shifts:\n  " + "\n  ".join(missing)


def test_booth_values_for_the_chop_shop_tokens_are_red_not_pink_or_blue():
    block = re.search(r'body\[data-theme="booth"\]\s*\{(.*?)\n\}', EXT, re.S).group(1)
    for name in ("--accent-b", "--accent-b-rgb", "--chop-panel-top-rgb", "--chop-card-rgb",
                 "--violet-rgb", "--health-bg-rgb", "--pill-text", "--pill-top-rgb", "--quenched-text", "--text-pale"):
        m = re.search(rf"{re.escape(name)}:\s*([^;]+);", block)
        assert m, f"booth theme must set {name}"
        rgb = _rgb(m.group(1).strip())
        assert rgb and not _off_red(rgb), f"{name} = {m.group(1)} is not red-shifted"


def test_the_normal_theme_keeps_its_original_colours():
    # These variables replaced colours written straight into rules; their defaults must stay
    # exactly what those rules used, so only Studio Lighting changes.
    expected = {
        "--chop-panel-top-rgb": "13, 20, 32", "--chop-panel-bottom-rgb": "7, 10, 18",
        "--chop-card-rgb": "12, 18, 30", "--log-bg": "#050508", "--violet-rgb": "190, 130, 255",
        "--health-bg-rgb": "30, 24, 48", "--health-border-rgb": "180, 130, 255",
        "--pill-text": "#a9eff8", "--pill-text-hi": "#d9fbff",
        "--pill-on-top-rgb": "5, 50, 66", "--pill-on-bottom-rgb": "4, 30, 43",
        "--pill-top-rgb": "4, 34, 46", "--pill-bottom-rgb": "3, 20, 30",
        "--quenched-text": "#86efac", "--text-pale": "#d5f4f8", "--tl-green": "#28c840",
    }
    tokens = _root_tokens()
    assert {k: tokens.get(k) for k in expected} == expected


def test_panels_use_the_variables_not_the_old_literals():
    for literal in ("rgba(30, 24, 48, .6)", "rgba(180,130,255,.18)", "color: #d5f4f8", "color: #a9eff8",
                    "rgba(13, 20, 32, .92)", "rgba(12, 18, 30, .82)", "color: #86efac",
                    "rgba(4, 34, 46, 0.86)"):
        assert literal not in CSS, f"{literal} is written into a rule again; use its variable"


def test_images_keep_the_red_tint_even_where_a_rule_adds_a_glow():
    # Rules that set their own `filter` (a glow on the active tool icon and the pipeline logo)
    # replace the booth tint unless the booth theme re-adds it.
    assert "--img-tint:" in EXT
    for selector in (".step-tab.active .step-icon", "#nav-btn-pipeline-wizard .step-icon"):
        rule = re.search(r'body\[data-theme="booth"\][^{]*' + re.escape(selector) + r"[^{]*\{([^}]*)\}", EXT)
        assert rule and "var(--img-tint)" in rule.group(1), selector


# Every rule that puts its own `filter` on an image-like element. A rule more specific than the
# booth image rule silently drops the red tint (this is how hovering a card brought colour back).
# A new one must get a booth counterpart that keeps `var(--img-tint)`, then be listed here.
_IMAGE_FILTER_RULES = {
    ".brand-logo", ".lp-logo img", ".lp-btn:hover .lp-icon, .lp-btn.active .lp-icon",
    ".step-tab.active .step-icon, .step-tab:hover .step-icon", "#nav-btn-pipeline-wizard .step-icon",
    ".card-icon img", ".card:hover .card-icon img", ".audit-panel-icon",
    ".db-tool-btn.permission-locked:hover img", ".icon-sm-invert", ".icon-btn-sm-invert",
}
_NEEDS_BOOTH_COUNTERPART = (
    ".lp-btn:hover .lp-icon", ".step-tab.active .step-icon", "#nav-btn-pipeline-wizard .step-icon",
    ".card:hover .card-icon img", ".db-tool-btn.permission-locked:hover img", ".icon-sm-invert",
)


def test_no_image_filter_rule_escapes_the_booth_tint():
    stripped = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    found = set()
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", stripped):
        selector, body = " ".join(m.group(1).split()), m.group(2)
        if re.search(r"(?<![-\w])filter\s*:", body) and re.search(
                r"img|icon|art|logo|thumb|cover|mark|avatar|video|svg|waveform|spin", selector, re.I):
            found.add(selector)
    assert found <= _IMAGE_FILTER_RULES, (
        f"new image filter rule(s) {sorted(found - _IMAGE_FILTER_RULES)}: add a booth rule in "
        "ui_extras.css that keeps var(--img-tint), then list it in _IMAGE_FILTER_RULES")
    for selector in _NEEDS_BOOTH_COUNTERPART:
        rules = re.findall(r'body\[data-theme="booth"\][^{]*' + re.escape(selector) + r"[^{]*\{([^}]*)\}", EXT)
        assert rules and all("var(--img-tint)" in r for r in rules), selector
