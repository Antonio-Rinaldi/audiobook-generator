from __future__ import annotations

from audiobook_generator_cli.application.services.audiobook_orchestrator import _extract_text


def test_extract_text_collects_paragraphs_and_headings() -> None:
    xhtml = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
        b"<h1>Title</h1>"
        b"<p>Hello <em>world</em> !</p>"
        b"<div>Ignored</div>"
        b"<p>Second paragraph</p>"
        b"</body></html>"
    )

    text = _extract_text(xhtml)

    assert text == "Title.\n\nHello world !\n\nSecond paragraph"
