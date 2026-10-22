"""Removing contact details from listing text.

Sellers often write "call/WhatsApp 0772 123 456", an email or a social
handle into titles and descriptions. None of that is needed to describe a
product, so it is replaced with a placeholder before anything is stored.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.]{3,}")
# A run that could be a phone number: +256 772 123 456, 0772-123456, (0772) 123 456, 0 7 7 2 1 2 3 4 5 6.
_NUMBER_RUN = re.compile(r"(?<![\w.])\(?\+?\d[\d\s().-]{7,}\d(?![\w])")
_PHONE_MIN_DIGITS = 9  # 772123456; prices like 12,500,000 have 8
_PLACEHOLDER_REPEATS = re.compile(r"(\[(?:number|email|link|handle)\])(?:[\s,/;|&-]*(?:or|and)?[\s,/;|&-]*\1)+", re.I)


def scrub(text: str | None) -> str | None:
    """Replace phone numbers, emails, links and @handles with placeholders."""
    if not text:
        return text
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _HANDLE.sub("[handle]", text)
    text = _NUMBER_RUN.sub(_phone_or_keep, text)
    return _PLACEHOLDER_REPEATS.sub(r"\1", text)


def _phone_or_keep(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[number]" if len(digits) >= _PHONE_MIN_DIGITS else match.group(0)
