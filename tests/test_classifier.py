import asyncio

import pytest

from app.classifier import KeywordClassifier, classify_reply

pytestmark = pytest.mark.anyio

Q = "Are you okay?"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("I'm fine", "ok"),
        ("yes I'm okay", "ok"),
        ("all good", "ok"),
        ("help", "needs_help"),
        ("my hip hurts", "needs_help"),
        ("I'm okay but my back hurts", "needs_help"),  # distress wins over "okay"
        ("I'm not okay", "needs_help"),
        ("I can't get up", "needs_help"),
        ("what?", "unclear"),
        ("mmm", "unclear"),
    ],
)
async def test_keyword_classifier(text, label):
    assert await classify_reply(KeywordClassifier(), Q, text, timeout_s=1) == label


@pytest.mark.parametrize("text", ["", "   "])
async def test_empty_reply_is_unclear(text):
    assert await classify_reply(KeywordClassifier(), Q, text, timeout_s=1) == "unclear"


class CrashingClassifier:
    async def classify(self, question, transcript):
        raise RuntimeError("API down")


class SlowClassifier:
    async def classify(self, question, transcript):
        await asyncio.sleep(10)
        return "ok"


class BadLabelClassifier:
    async def classify(self, question, transcript):
        return "probably fine"


@pytest.mark.parametrize("classifier", [CrashingClassifier(), SlowClassifier(), BadLabelClassifier()])
async def test_failures_become_unclear_never_ok(classifier):
    assert await classify_reply(classifier, Q, "I'm fine", timeout_s=0.05) == "unclear"
