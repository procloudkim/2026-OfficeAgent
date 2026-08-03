"""Strict incremental projection of one structured Codex answer."""

from __future__ import annotations

import json
from enum import Enum, auto
from io import StringIO
from typing import Any

STRUCTURED_ANSWER_MAX_CHARACTERS = 65_536
STRUCTURED_JSON_MAX_CHARACTERS = 1_048_576

_JSON_WHITESPACE = frozenset(" \t\r\n")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_SHORT_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class StructuredAnswerError(ValueError):
    """The structured assistant message violated the local answer contract."""


def answer_output_schema() -> dict[str, object]:
    """Return the exact current-turn JSON Schema sent to Codex App Server."""

    return {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }


class _DocumentState(Enum):
    BEFORE_OBJECT = auto()
    BEFORE_KEY = auto()
    IN_KEY = auto()
    AFTER_KEY = auto()
    BEFORE_VALUE = auto()
    IN_VALUE = auto()
    AFTER_VALUE = auto()
    DONE = auto()


class _StringState(Enum):
    TEXT = auto()
    ESCAPE = auto()
    UNICODE = auto()
    LOW_SURROGATE_BACKSLASH = auto()
    LOW_SURROGATE_U = auto()
    LOW_SURROGATE_UNICODE = auto()
    CLOSED = auto()


class _JsonStringDecoder:
    """Decode one JSON string across arbitrary input boundaries."""

    def __init__(self) -> None:
        self._state = _StringState.TEXT
        self._unicode_digits: list[str] = []
        self._high_surrogate: int | None = None

    def consume(self, character: str) -> tuple[str, bool]:
        """Consume one raw JSON character and return decoded text plus close state."""

        if self._state is _StringState.TEXT:
            if character == '"':
                self._state = _StringState.CLOSED
                return "", True
            if character == "\\":
                self._state = _StringState.ESCAPE
                return "", False
            codepoint = ord(character)
            if codepoint < 0x20:
                raise StructuredAnswerError(
                    "Structured answer contains an unescaped control character"
                )
            if 0xD800 <= codepoint <= 0xDFFF:
                raise StructuredAnswerError(
                    "Structured answer contains an unpaired surrogate"
                )
            return character, False

        if self._state is _StringState.ESCAPE:
            if character in _SHORT_ESCAPES:
                self._state = _StringState.TEXT
                return _SHORT_ESCAPES[character], False
            if character == "u":
                self._unicode_digits.clear()
                self._state = _StringState.UNICODE
                return "", False
            raise StructuredAnswerError("Structured answer contains an invalid escape")

        if self._state is _StringState.UNICODE:
            return self._consume_unicode(character, low_surrogate=False)

        if self._state is _StringState.LOW_SURROGATE_BACKSLASH:
            if character != "\\":
                raise StructuredAnswerError(
                    "Structured answer contains an unpaired high surrogate"
                )
            self._state = _StringState.LOW_SURROGATE_U
            return "", False

        if self._state is _StringState.LOW_SURROGATE_U:
            if character != "u":
                raise StructuredAnswerError(
                    "Structured answer contains an invalid surrogate pair"
                )
            self._unicode_digits.clear()
            self._state = _StringState.LOW_SURROGATE_UNICODE
            return "", False

        if self._state is _StringState.LOW_SURROGATE_UNICODE:
            return self._consume_unicode(character, low_surrogate=True)

        raise StructuredAnswerError("Structured answer contains data after a JSON string")

    def _consume_unicode(
        self,
        character: str,
        *,
        low_surrogate: bool,
    ) -> tuple[str, bool]:
        if character not in _HEX_DIGITS:
            raise StructuredAnswerError(
                "Structured answer contains an invalid Unicode escape"
            )
        self._unicode_digits.append(character)
        if len(self._unicode_digits) < 4:
            return "", False

        code_unit = int("".join(self._unicode_digits), 16)
        self._unicode_digits.clear()
        if code_unit == 0:
            raise StructuredAnswerError("Structured answer contains a NUL character")
        if low_surrogate:
            high_surrogate = self._high_surrogate
            if high_surrogate is None or not 0xDC00 <= code_unit <= 0xDFFF:
                raise StructuredAnswerError(
                    "Structured answer contains an invalid surrogate pair"
                )
            codepoint = 0x10000 + (
                ((high_surrogate - 0xD800) << 10) + (code_unit - 0xDC00)
            )
            self._high_surrogate = None
            self._state = _StringState.TEXT
            return chr(codepoint), False

        if 0xD800 <= code_unit <= 0xDBFF:
            self._high_surrogate = code_unit
            self._state = _StringState.LOW_SURROGATE_BACKSLASH
            return "", False
        if 0xDC00 <= code_unit <= 0xDFFF:
            raise StructuredAnswerError(
                "Structured answer contains an unpaired low surrogate"
            )
        self._state = _StringState.TEXT
        return chr(code_unit), False


class StructuredAnswerStreamDecoder:
    """Project a strict ``{"answer": string}`` JSON stream to plain text deltas."""

    def __init__(self) -> None:
        self._state = _DocumentState.BEFORE_OBJECT
        self._raw = StringIO()
        self._raw_character_count = 0
        self._key_index = 0
        self._answer = StringIO()
        self._answer_character_count = 0
        self._string_decoder: _JsonStringDecoder | None = None

    def feed(self, fragment: str) -> str:
        """Consume one raw model delta and return at most one plain answer delta."""

        self._raw_character_count += len(fragment)
        if self._raw_character_count > STRUCTURED_JSON_MAX_CHARACTERS:
            raise StructuredAnswerError("Structured answer JSON exceeds the size limit")
        self._raw.write(fragment)

        visible: list[str] = []
        for character in fragment:
            self._consume(character, visible)
        decoded = "".join(visible)
        if decoded:
            self._answer.write(decoded)
        return decoded

    def finish(self) -> str:
        """Validate the complete JSON document and return its exact answer string."""

        if self._state is not _DocumentState.DONE:
            raise StructuredAnswerError("Structured answer JSON is incomplete")
        raw = self._raw.getvalue()
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, StructuredAnswerError) as error:
            raise StructuredAnswerError("Structured answer JSON is invalid") from error
        if not isinstance(parsed, dict) or set(parsed) != {"answer"}:
            raise StructuredAnswerError(
                "Structured answer must contain only the answer field"
            )
        answer = parsed["answer"]
        if not isinstance(answer, str):
            raise StructuredAnswerError("Structured answer field must be a string")
        if not answer.strip():
            raise StructuredAnswerError("Structured answer field must not be blank")
        if len(answer) > STRUCTURED_ANSWER_MAX_CHARACTERS:
            raise StructuredAnswerError("Structured answer exceeds the size limit")
        decoded = self._answer.getvalue()
        if decoded != answer:
            raise StructuredAnswerError(
                "Incremental structured answer differs from the final JSON value"
            )
        return answer

    def _consume(self, character: str, visible: list[str]) -> None:
        if self._state is _DocumentState.BEFORE_OBJECT:
            if character in _JSON_WHITESPACE:
                return
            if character != "{":
                raise StructuredAnswerError("Structured answer must be a JSON object")
            self._state = _DocumentState.BEFORE_KEY
            return

        if self._state is _DocumentState.BEFORE_KEY:
            if character in _JSON_WHITESPACE:
                return
            if character != '"':
                raise StructuredAnswerError(
                    "Structured answer object must start with the answer key"
                )
            self._string_decoder = _JsonStringDecoder()
            self._state = _DocumentState.IN_KEY
            return

        if self._state is _DocumentState.IN_KEY:
            decoded, closed = self._active_string_decoder().consume(character)
            if decoded:
                expected_key = "answer"
                if (
                    self._key_index >= len(expected_key)
                    or decoded != expected_key[self._key_index]
                ):
                    raise StructuredAnswerError(
                        "Structured answer object has an unexpected key"
                    )
                self._key_index += 1
            if closed:
                if self._key_index != len("answer"):
                    raise StructuredAnswerError(
                        "Structured answer object has an unexpected key"
                    )
                self._state = _DocumentState.AFTER_KEY
            return

        if self._state is _DocumentState.AFTER_KEY:
            if character in _JSON_WHITESPACE:
                return
            if character != ":":
                raise StructuredAnswerError(
                    "Structured answer key is not followed by a colon"
                )
            self._state = _DocumentState.BEFORE_VALUE
            return

        if self._state is _DocumentState.BEFORE_VALUE:
            if character in _JSON_WHITESPACE:
                return
            if character != '"':
                raise StructuredAnswerError("Structured answer field is not a string")
            self._string_decoder = _JsonStringDecoder()
            self._state = _DocumentState.IN_VALUE
            return

        if self._state is _DocumentState.IN_VALUE:
            decoded, closed = self._active_string_decoder().consume(character)
            if decoded:
                self._answer_character_count += len(decoded)
                if self._answer_character_count > STRUCTURED_ANSWER_MAX_CHARACTERS:
                    raise StructuredAnswerError(
                        "Structured answer exceeds the size limit"
                    )
                visible.append(decoded)
            if closed:
                self._state = _DocumentState.AFTER_VALUE
            return

        if self._state is _DocumentState.AFTER_VALUE:
            if character in _JSON_WHITESPACE:
                return
            if character != "}":
                raise StructuredAnswerError(
                    "Structured answer object contains additional data"
                )
            self._state = _DocumentState.DONE
            return

        if self._state is _DocumentState.DONE:
            if character not in _JSON_WHITESPACE:
                raise StructuredAnswerError(
                    "Structured answer JSON contains trailing data"
                )
            return

        raise StructuredAnswerError("Structured answer decoder entered an invalid state")

    def _active_string_decoder(self) -> _JsonStringDecoder:
        if self._string_decoder is None:
            raise StructuredAnswerError("Structured answer string decoder is unavailable")
        return self._string_decoder


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredAnswerError("Structured answer JSON contains duplicate keys")
        result[key] = value
    return result
