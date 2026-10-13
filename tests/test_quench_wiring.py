"""Quench lives inside the Forge card (the old Normalize card is gone).

The page is static HTML plus classic scripts, so a renamed id fails silently in the
browser. These tests pin the ids the Forge/Quench code reads, and that nothing still
points at the removed Normalize card.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORGE_CARD = (ROOT / "templates/partials/physical_library/fingerprinting.html").read_text()
SOURCES = [p for pat in ("static/**/*.js", "templates/**/*.html") for p in ROOT.glob(pat)
           if "vendor" not in p.parts]


def _ids(html: str) -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', html))


def test_forge_card_has_every_id_the_quench_code_reads():
    needed = {"process-normalize-mode", "process-workers", "quench-warning",
              "norm-preview", "norm-preview-status", "norm-sample-list", "process-pills"}
    assert needed <= _ids(FORGE_CARD)


def test_quench_mode_select_offers_off_passive_aggressive_and_defaults_to_off():
    select = re.search(r'<select id="process-normalize-mode".*?</select>', FORGE_CARD, re.S).group(0)
    values = re.findall(r'<option value="(\w+)"( selected)?', select)
    assert [v for v, _ in values] == ["off", "passive", "aggressive"]
    assert [v for v, sel in values if sel] == ["off"]          # tag-only runs stay safe by default


def test_the_preview_is_a_button_not_automatic():
    assert "normPreviewRun()" in FORGE_CARD
    preview_js = (ROOT / "static/chop_shop/normalize_preview.js").read_text()
    assert "MutationObserver" not in preview_js               # no scan the moment a folder appears


def test_nothing_points_at_the_removed_normalize_card():
    assert not (ROOT / "templates/partials/physical_library/normalizer.html").exists()
    gone = ("normalize-pills", "normalize-zone", "normalize-workers", "step-normalize",
            "runNormalize(", "normalizer.html", "normPreviewSetupObserver")
    hits = [(p.relative_to(ROOT).as_posix(), g) for p in SOURCES
            for g in gone if g in p.read_text(errors="ignore")]
    assert not hits, hits


def test_index_page_has_no_duplicate_ids_from_the_merge():
    index = (ROOT / "templates/index.html").read_text()
    includes = re.findall(r"\{% include '([^']+)' %\}", index)
    html = index + "".join((ROOT / "templates" / i).read_text() for i in includes
                           if (ROOT / "templates" / i).exists())
    ids = re.findall(r'\bid="([^"{}]+)"', html)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, dupes
