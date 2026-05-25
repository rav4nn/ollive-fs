from app.services.pii import redact


def test_email_redacted():
    assert redact("contact me at alice@example.com please") == \
        "contact me at [REDACTED:EMAIL] please"


def test_phone_redacted_various_formats():
    assert "[REDACTED:PHONE]" in redact("call 415-555-1212")
    assert "[REDACTED:PHONE]" in redact("call (415) 555-1212")
    assert "[REDACTED:PHONE]" in redact("call +1 415 555 1212")


def test_credit_card_redacted():
    assert redact("card 4111 1111 1111 1111 expires") == \
        "card [REDACTED:CREDIT_CARD] expires"
    assert redact("4111-1111-1111-1111") == "[REDACTED:CREDIT_CARD]"


def test_ssn_redacted():
    assert redact("ssn 123-45-6789 yes") == "ssn [REDACTED:SSN] yes"


def test_ipv4_redacted():
    assert redact("server is at 10.0.0.5") == "server is at [REDACTED:IP]"


def test_empty_passthrough():
    assert redact("") == ""
    assert redact(None) is None  # type: ignore[arg-type]


def test_no_match_unchanged():
    s = "hello world nothing sensitive"
    assert redact(s) == s


def test_multiple_pii_in_one_string():
    out = redact("email alice@x.com from 1.2.3.4")
    assert "[REDACTED:EMAIL]" in out
    assert "[REDACTED:IP]" in out
    assert "alice@x.com" not in out
    assert "1.2.3.4" not in out
