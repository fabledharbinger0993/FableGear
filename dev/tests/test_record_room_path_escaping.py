"""Folder and file paths come from mounted media, so they must never be spliced into inline JS.

An onclick="f('${path}')" string breaks out on a path containing a quote: _escAttr emits &#39;
for ', and the HTML parser turns that back into ' before the JS engine sees the handler.
Paths travel in data-path attributes and handlers read this.dataset.path instead.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "fablegear" / "static" / "record_room" / "library_mode.js"


def test_no_path_is_interpolated_into_an_inline_handler():
    text = SRC.read_text()
    bad = re.findall(r"""on\w+="[^"]*\(\s*'\$\{[^}]*(?:path|parent)[^}]*\}'""", text, flags=re.I)
    assert bad == []


def test_path_and_error_text_in_markup_are_escaped():
    text = SRC.read_text()
    assert "title=\"${data.path}\"" not in text
    assert not re.search(r"innerHTML[^\n]*\$\{e\.message\}", text)
