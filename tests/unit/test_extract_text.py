from __future__ import annotations

from audiobook_generator_cli.application.services.audiobook_orchestrator import _extract_paragraphs


def test_extract_paragraphs_collects_headings_and_paragraphs() -> None:
    xhtml = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
        b"<h1>Title</h1>"
        b"<p>Hello <em>world</em> !</p>"
        b"<div>Ignored</div>"
        b"<p>Second paragraph</p>"
        b"</body></html>"
    )

    paragraphs = _extract_paragraphs(xhtml)

    assert paragraphs == ["Title.", "Hello world!", "Second paragraph"]


def test_extract_paragraphs_handles_nested_wrapper_structure_like_chapter_files() -> None:
    xhtml = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body><div>"
        b"<h1>ACCURSED BRIDE</h1>"
        b"<h2>CHAPTER 1</h2>"
        b"<p class='whitespace'>.</p>"
        b"<p>Il sole al tramonto <em>tingeva</em> le distanze.</p>"
        b"</div></body></html>"
    )

    paragraphs = _extract_paragraphs(xhtml)

    assert paragraphs == ["ACCURSED BRIDE.", "CHAPTER 1.", "Il sole al tramonto tingeva le distanze."]


def test_extract_paragraphs_includes_list_and_quote_blocks() -> None:
    xhtml = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
        b"<ul><li>Primo punto</li><li>Secondo <em>punto</em></li></ul>"
        b"<blockquote>Citazione importante</blockquote>"
        b"</body></html>"
    )

    paragraphs = _extract_paragraphs(xhtml)

    assert paragraphs == ["Primo punto", "Secondo punto", "Citazione importante"]


def test_extract_paragraphs_keeps_real_text_and_skips_dot_placeholder() -> None:
    xhtml = (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body>"
        b"<h2>ABOUT THE AUTHOR</h2>"
        b"<p class='first'>Ha scritto il suo primo<em> romanzo, Demon City</em> Shinjuku.</p>"
        b"<p class='whitespace'>.</p>"
        b"</body></html>"
    )

    paragraphs = _extract_paragraphs(xhtml)

    assert paragraphs == [
        "ABOUT THE AUTHOR.",
        "Ha scritto il suo primo romanzo, Demon City Shinjuku.",
    ]


