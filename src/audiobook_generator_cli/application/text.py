from __future__ import annotations

import html
import re
from dataclasses import dataclass

from lxml import etree

from audiobook_generator_cli.application.models import _HEADING_TAGS, NarrationBlock
from audiobook_generator_cli.domain.errors import EpubReadError
from audiobook_generator_cli.domain.models import AudioSettings
from audiobook_generator_cli.infrastructure.logging.logger_factory import create_logger

logger = create_logger(__name__)

_NARRATABLE_TAGS = (
    _HEADING_TAGS | {"p", "li", "blockquote", "dd", "dt", "figcaption", "td", "th"}
)

_WS_RE = re.compile(r"\s+")
_INLINE_TAG_RE = re.compile(r"</?[^>]+>")

_DIALOGUE_RE = re.compile(
    r"«[^»]*»"
    r'|“[^”]*”'
    r'|"[^"]*"',
)
_DIALOGUE_COMMA_RE = re.compile(
    r",\s*[»”\"]",
)
_COLON_BEFORE_QUOTE_RE = re.compile(
    r":\s*[«“\"]",
)


@dataclass(frozen=True)
class PunctuationHints:
    """Typographic cues extracted from a block of text to guide TTS performance."""

    has_dialogue: bool
    dialogue_ends_with_comma: bool
    has_ellipsis: bool
    has_em_dash: bool
    has_exclamation_in_dialogue: bool
    has_question_in_dialogue: bool
    has_colon_before_quote: bool


def detect_punctuation_hints(text: str) -> PunctuationHints:
    """Return typographic cues present in *text* that inform TTS performance."""
    dialogue_spans = _DIALOGUE_RE.findall(text)
    has_dialogue = bool(dialogue_spans)
    dialogue_text = "".join(dialogue_spans)

    return PunctuationHints(
        has_dialogue=has_dialogue,
        dialogue_ends_with_comma=bool(_DIALOGUE_COMMA_RE.search(text)),
        has_ellipsis="…" in text,
        has_em_dash="—" in text or "–" in text,
        has_exclamation_in_dialogue=has_dialogue and "!" in dialogue_text,
        has_question_in_dialogue=has_dialogue and "?" in dialogue_text,
        has_colon_before_quote=bool(_COLON_BEFORE_QUOTE_RE.search(text)),
    )


def _local_tag_name(tag: str | None) -> str:
    """Return lower-cased local tag name without XML namespace prefix."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _normalise_block_text(elem: etree._Element) -> str:
    """Extract visible text from an XHTML block, preserving nested inline content."""
    raw = " ".join(str(text) for text in elem.itertext())
    cleaned = _WS_RE.sub(" ", raw).strip()
    return re.sub(r"\s+([,.;:!?…])", r"\1", cleaned)


def _has_spoken_text(text: str) -> bool:
    """Return True when text contains at least one alphanumeric character."""
    return any(ch.isalnum() for ch in text)


def _strip_inline_tags_for_tts(text: str) -> str:
    """Remove inline XML/HTML markers from text before TTS synthesis."""
    unescaped = html.unescape(text)
    without_tags = _INLINE_TAG_RE.sub(" ", unescaped)
    collapsed = _WS_RE.sub(" ", without_tags).strip()
    return re.sub(r"\s+([,.;:!?…])", r"\1", collapsed)


def _is_heading_tag(tag: str) -> bool:
    """Check whether tag belongs to heading tag set."""
    return _local_tag_name(tag) in _HEADING_TAGS


_BASE_READING_INSTRUCTION = (
    "Maintain a consistent tone and stable volume across the entire passage. "
    "Strictly follow punctuation for natural narration: apply clear pauses at "
    "commas, full stops, semicolons, colons, question marks, and exclamation marks."
)
_HEADING_READING_INSTRUCTION = (
    "Read headings calmly and clearly, without shouting or abrupt emphasis."
)


def _instruction_for_block(block: NarrationBlock, settings: AudioSettings) -> str:
    """Build narration instructions according to block type and user tone flags."""
    parts = [_BASE_READING_INSTRUCTION]
    if block.is_heading:
        parts.append(_HEADING_READING_INSTRUCTION)
        if settings.heading_tone:
            parts.append(settings.heading_tone)
        return "\n".join(parts)

    if settings.paragraph_tone:
        parts.append(settings.paragraph_tone)

    hints = detect_punctuation_hints(block.text)

    if hints.has_dialogue:
        parts.append(
            "The character is speaking directly; read with a slightly more personal, "
            "engaged tone as someone speaking in first person."
        )
    if hints.dialogue_ends_with_comma:
        parts.append(
            "The speech flows into an attribution tag; do not apply a strong pause at "
            "the trailing comma inside the closing quote — let it flow naturally into "
            "the following narration."
        )
    if hints.has_ellipsis:
        parts.append(
            "Where an ellipsis appears, read with a slight hesitation and gently "
            "trail off, as if the thought is left unfinished."
        )
    if hints.has_em_dash:
        parts.append(
            "Where an em dash or en dash appears mid-sentence, apply a brief pause "
            "without any pitch rise, as if an interruption or parenthetical aside."
        )
    if hints.has_exclamation_in_dialogue:
        parts.append(
            "The dialogue contains an exclamation; deliver it with emphatic energy "
            "while staying natural and not robotic."
        )
    if hints.has_question_in_dialogue:
        parts.append(
            "The dialogue contains a question; use a genuine questioning intonation "
            "that rises naturally at the end."
        )
    if hints.has_colon_before_quote:
        parts.append(
            "The colon before the quoted speech signals a preparatory rise; let your "
            "voice lift slightly before delivering the words that follow."
        )

    return "\n".join(parts)


def extract_narration_blocks(xhtml_bytes: bytes) -> list[NarrationBlock]:
    """Extract narratable blocks preserving tag type for style/pause decisions.

    Raises EpubReadError when the XHTML cannot be parsed.
    """
    try:
        root = etree.fromstring(xhtml_bytes)
    except etree.XMLSyntaxError as exc:
        raise EpubReadError(f"Failed to parse chapter XHTML: {exc}") from exc

    all_elements = list(root.iter())
    narratable_elements = [
        elem for elem in all_elements if _local_tag_name(elem.tag) in _NARRATABLE_TAGS
    ]

    cleaned_by_elem = {
        elem: _normalise_block_text(elem)
        for elem in narratable_elements
    }
    spoken_elements = [
        elem
        for elem in narratable_elements
        if cleaned_by_elem[elem] and _has_spoken_text(cleaned_by_elem[elem])
    ]
    heading_period_appended = sum(
        1
        for elem in spoken_elements
        if _is_heading_tag(elem.tag) and cleaned_by_elem[elem][-1] not in ".!?…:"
    )

    blocks = [
        NarrationBlock(
            tag=_local_tag_name(elem.tag),
            text=(
                f"{cleaned_by_elem[elem]}."
                if _is_heading_tag(elem.tag)
                and cleaned_by_elem[elem][-1] not in ".!?…:"
                else cleaned_by_elem[elem]
            ),
        )
        for elem in spoken_elements
    ]

    logger.debug(
        "Extraction summary | narratable=%s skipped_non_narratable=%s "
        "skipped_empty=%s heading_period_appended=%s",
        len(blocks),
        len(all_elements) - len(narratable_elements),
        len(narratable_elements) - len(spoken_elements),
        heading_period_appended,
    )
    return blocks