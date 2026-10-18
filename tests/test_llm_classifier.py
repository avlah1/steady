"""LLMClassifier with a fake Anthropic client: no network, no API key, no cost."""

from types import SimpleNamespace

import pytest

from app.classifier import HAIKU, KeywordClassifier, LLMClassifier, ReplyLabel, classify_reply
from app.config import Settings
from app.deps import build_classifier

pytestmark = pytest.mark.anyio


class FakeMessages:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(label=None, stop_reason="end_turn", error=None):
    parsed = ReplyLabel(label=label) if label else None
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed), error)
    return SimpleNamespace(messages=messages)


@pytest.mark.parametrize("label", ["ok", "needs_help", "unclear"])
async def test_returns_the_models_label(label):
    classifier = LLMClassifier(fake_client(label))

    assert await classifier.classify("Are you okay?", "whatever") == label


async def test_sends_question_and_reply_to_haiku_with_structured_output():
    client = fake_client("needs_help")

    await LLMClassifier(client).classify("Are you okay?", "I don't feel so good")

    call = client.messages.calls[0]
    assert call["model"] == HAIKU
    assert call["output_format"] is ReplyLabel
    assert "<question>Are you okay?</question>" in call["messages"][0]["content"]
    assert "<reply>I don't feel so good</reply>" in call["messages"][0]["content"]


async def test_refusal_is_unclear():
    assert await LLMClassifier(fake_client(stop_reason="refusal")).classify("Are you okay?", "...") == "unclear"


async def test_missing_label_is_unclear():
    assert await LLMClassifier(fake_client(label=None)).classify("Are you okay?", "...") == "unclear"


async def test_api_error_becomes_unclear_via_wrapper():
    classifier = LLMClassifier(fake_client(error=RuntimeError("connection reset")))

    assert await classify_reply(classifier, "Are you okay?", "I'm fine", timeout_s=1) == "unclear"


def test_no_api_key_falls_back_to_keywords():
    assert isinstance(build_classifier(Settings(api_key="k", anthropic_api_key=None)), KeywordClassifier)
    assert isinstance(build_classifier(Settings(api_key="k", anthropic_api_key="")), KeywordClassifier)


def test_api_key_selects_llm_with_no_retries():
    classifier = build_classifier(Settings(api_key="k", anthropic_api_key="sk-ant-test", classify_timeout_s=5))

    assert isinstance(classifier, LLMClassifier)
    assert classifier.client.max_retries == 0
    assert classifier.client.timeout == 4  # inside the 5 s classify timeout
