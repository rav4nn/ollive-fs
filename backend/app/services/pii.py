"""PII redaction for stored inference logs.

Applied at the storage boundary only: the original user message and assistant
output are still what we send to and receive from the LLM. We just don't keep
the raw values in the database — we keep the redacted form. This means if the
log store is leaked, the impact is bounded.

Detectors are intentionally simple regex. They will:
  - catch the common shapes (email, phone, credit card, SSN-like, IPv4)
  - miss some, and false-positive on others
A real production system would layer this with a proper PII model and an
allowlist for domain-specific exemptions. See README "what I'd improve".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings

# Order matters — credit card matches first so the digit run doesn't get
# eaten by the phone-number rule.
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"
)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass(frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]
    placeholder: str


_RULES: list[_Rule] = [
    _Rule("email", EMAIL_RE, "[REDACTED:EMAIL]"),
    _Rule("credit_card", CREDIT_CARD_RE, "[REDACTED:CREDIT_CARD]"),
    _Rule("ssn", SSN_RE, "[REDACTED:SSN]"),
    _Rule("phone", PHONE_RE, "[REDACTED:PHONE]"),
    _Rule("ipv4", IPV4_RE, "[REDACTED:IP]"),
]


def redact(text: str) -> str:
    """Return text with all configured PII patterns replaced by placeholders."""
    if not text:
        return text
    out = text
    for rule in _RULES:
        out = rule.pattern.sub(rule.placeholder, out)
    return out


def maybe_redact(text: str) -> str:
    """Redact only when REDACT_PII=true. Cheap no-op otherwise."""
    if get_settings().redact_pii:
        return redact(text)
    return text
