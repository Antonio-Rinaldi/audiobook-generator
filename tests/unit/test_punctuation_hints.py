from __future__ import annotations

import pytest

from audiobook_generator_cli.application.models import NarrationBlock
from audiobook_generator_cli.application.text import (
    _instruction_for_block,
    detect_punctuation_hints,
)
from audiobook_generator_cli.domain.models import AudioSettings

# ---------------------------------------------------------------------------
# detect_punctuation_hints — flag coverage
# ---------------------------------------------------------------------------


def test_has_dialogue_true_for_guillemets() -> None:
    hints = detect_punctuation_hints("«Ciao», disse lei.")
    assert hints.has_dialogue is True


def test_has_dialogue_true_for_curly_double_quotes() -> None:
    hints = detect_punctuation_hints("“Hello,” she said.")
    assert hints.has_dialogue is True


def test_has_dialogue_true_for_straight_quotes() -> None:
    hints = detect_punctuation_hints('"Well, half of it is true," she said.')
    assert hints.has_dialogue is True


def test_has_dialogue_false_for_plain_narration() -> None:
    hints = detect_punctuation_hints("The sun set over the mountains.")
    assert hints.has_dialogue is False


def test_dialogue_ends_with_comma_true_for_guillemet_comma() -> None:
    hints = detect_punctuation_hints("«Beh, almeno metà è vero,» disse lei.")
    assert hints.dialogue_ends_with_comma is True


def test_dialogue_ends_with_comma_true_for_curly_quote_comma() -> None:
    hints = detect_punctuation_hints("“Well, half of it is true,” she said.")
    assert hints.dialogue_ends_with_comma is True


def test_dialogue_ends_with_comma_true_for_straight_quote_comma() -> None:
    hints = detect_punctuation_hints('"True enough," he replied.')
    assert hints.dialogue_ends_with_comma is True


def test_dialogue_ends_with_comma_false_when_no_trailing_comma() -> None:
    hints = detect_punctuation_hints("«Ciao.» disse lei.")
    assert hints.dialogue_ends_with_comma is False


def test_dialogue_ends_with_comma_false_when_no_dialogue() -> None:
    hints = detect_punctuation_hints("The river ran fast.")
    assert hints.dialogue_ends_with_comma is False


def test_has_ellipsis_true() -> None:
    hints = detect_punctuation_hints("I don’t know… maybe.")
    assert hints.has_ellipsis is True


def test_has_ellipsis_false() -> None:
    hints = detect_punctuation_hints("Clear statement with no hesitation.")
    assert hints.has_ellipsis is False


def test_has_em_dash_true_for_em_dash() -> None:
    hints = detect_punctuation_hints("He stopped—or did he?")
    assert hints.has_em_dash is True


def test_has_em_dash_true_for_en_dash() -> None:
    hints = detect_punctuation_hints("Pages 10–15 of the book.")
    assert hints.has_em_dash is True


def test_has_em_dash_false() -> None:
    hints = detect_punctuation_hints("Simple sentence without dashes.")
    assert hints.has_em_dash is False


def test_has_exclamation_in_dialogue_true() -> None:
    hints = detect_punctuation_hints("«Attento!» gridò.")
    assert hints.has_exclamation_in_dialogue is True


def test_has_exclamation_in_dialogue_false_outside_dialogue() -> None:
    hints = detect_punctuation_hints("She ran away! Nobody followed.")
    assert hints.has_exclamation_in_dialogue is False


def test_has_question_in_dialogue_true() -> None:
    hints = detect_punctuation_hints("“Are you sure?” he asked.")
    assert hints.has_question_in_dialogue is True


def test_has_question_in_dialogue_false_outside_dialogue() -> None:
    hints = detect_punctuation_hints("Where did it go? Nobody knew.")
    assert hints.has_question_in_dialogue is False


def test_has_colon_before_quote_true() -> None:
    hints = detect_punctuation_hints("He said: “Enough.”")
    assert hints.has_colon_before_quote is True


def test_has_colon_before_quote_false_when_colon_not_before_quote() -> None:
    hints = detect_punctuation_hints("Items: bread, milk, eggs.")
    assert hints.has_colon_before_quote is False


def test_has_colon_before_quote_false_when_no_colon() -> None:
    hints = detect_punctuation_hints("“Just talking.”")
    assert hints.has_colon_before_quote is False


def test_hints_are_frozen() -> None:
    hints = detect_punctuation_hints("test")
    with pytest.raises((AttributeError, TypeError)):
        hints.has_dialogue = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _instruction_for_block — punctuation hints extend the base instruction
# ---------------------------------------------------------------------------

_PLAIN_SETTINGS = AudioSettings(model="m")


def _para_block(text: str) -> NarrationBlock:
    return NarrationBlock(tag="p", text=text)


def _heading_block(text: str) -> NarrationBlock:
    return NarrationBlock(tag="h1", text=text)


def test_instruction_base_always_present() -> None:
    block = _para_block("Simple narration.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "consistent tone" in instr


def test_instruction_dialogue_clause_added_for_dialogue_text() -> None:
    block = _para_block("«Ciao», disse lei.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "character" in instr.lower() or "speaking" in instr.lower()


def test_instruction_no_dialogue_clause_for_plain_narration() -> None:
    block = _para_block("The mountain stood still.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "character" not in instr.lower()


def test_instruction_attribution_clause_added_when_dialogue_ends_with_comma() -> None:
    block = _para_block("«Beh, almeno metà è vero,» disse lei.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "attribution" in instr.lower() or "flow" in instr.lower()


def test_instruction_ellipsis_clause_added() -> None:
    block = _para_block("I don’t know… maybe.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "hesitation" in instr.lower() or "trailing" in instr.lower()


def test_instruction_em_dash_clause_added() -> None:
    block = _para_block("He stopped—or did he?")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "pause" in instr.lower() or "interruption" in instr.lower() or "dash" in instr.lower()


def test_instruction_exclamation_in_dialogue_clause_added() -> None:
    block = _para_block("«Attento!» gridò.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "emphatic" in instr.lower() or "exclamation" in instr.lower()


def test_instruction_question_in_dialogue_clause_added() -> None:
    block = _para_block("“Are you sure?” he asked.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "question" in instr.lower() or "questioning" in instr.lower()


def test_instruction_colon_before_quote_clause_added() -> None:
    block = _para_block("He said: “Enough.”")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "rise" in instr.lower() or "preparatory" in instr.lower() or "colon" in instr.lower()


def test_instruction_heading_not_affected_by_punctuation_hints() -> None:
    block = _heading_block("«Titolo»")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "without shouting" in instr
    assert "consistent tone" in instr


def test_instruction_clauses_are_additive_not_replacing_base() -> None:
    block = _para_block("«Beh,» disse.… Poi tacque—.")
    instr = _instruction_for_block(block, _PLAIN_SETTINGS)
    assert "consistent tone" in instr
    assert "character" in instr.lower() or "speaking" in instr.lower()


def test_instruction_user_paragraph_tone_preserved_alongside_hints() -> None:
    settings = AudioSettings(model="m", paragraph_tone="calm and warm")
    block = _para_block("«Ciao», disse lei.")
    instr = _instruction_for_block(block, settings)
    assert "calm and warm" in instr
    assert "consistent tone" in instr