import pytest
from pydantic import ValidationError

from app.api.schemas import ChatRequest, ChatResponse


def test_chat_request_requires_message():
    with pytest.raises(ValidationError):
        ChatRequest()


def test_chat_request_rejects_empty_message():
    with pytest.raises(ValidationError):
        ChatRequest(message="")


def test_chat_request_accepts_message_only():
    r = ChatRequest(message="hi")
    assert r.session_id is None
    assert r.message == "hi"


def test_chat_response_round_trip():
    r = ChatResponse(session_id="abc", response="hello")
    assert r.model_dump() == {"session_id": "abc", "response": "hello"}
