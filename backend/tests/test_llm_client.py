from types import SimpleNamespace

from app.services.llm_client import _AnthropicClient


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


async def test_anthropic_chat_skips_leading_thinking_block():
    # Claude Sonnet 5 runs adaptive thinking by default, so a thinking block
    # can come before the text block.
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="sig"),
            SimpleNamespace(type="text", text="Hello there."),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )
    client = _AnthropicClient(api_key="test-key", model="claude-sonnet-5")
    client._client = SimpleNamespace(messages=_FakeMessages(response))

    result = await client.chat(
        messages=[{"role": "user", "content": "hi"}], system="sys", max_tokens=16000
    )

    assert result.status == "ok"
    assert result.text == "Hello there."
    assert result.total_tokens == 30
    sent = client._client.messages.kwargs
    assert not {"temperature", "top_p", "top_k"} & sent.keys()  # Sonnet 5 rejects them
