"""Turns the person's reply into one of three labels. The state machine decides what the label means.

KeywordClassifier is a stand-in until the LLM classifier is written; both share the Classifier interface.
"""

import asyncio
import logging
import re
from typing import Literal, Protocol

log = logging.getLogger("classifier")

Label = Literal["ok", "needs_help", "unclear"]
LABELS: frozenset[str] = frozenset({"ok", "needs_help", "unclear"})


class Classifier(Protocol):
    async def classify(self, question: str, transcript: str) -> Label: ...


# Checked in order. Help words win over ok words, so "I'm okay but my hip hurts" is needs_help.
_NEEDS_HELP = re.compile(
    r"\b(help|hurt|hurts|pain|can'?t (get up|move|breathe)|not (ok|okay|fine|alright)|fell|dizzy|bleeding)\b"
)
_OK = re.compile(r"\b(ok|okay|fine|alright|all right|good|i'?m good|no problem)\b")


class KeywordClassifier:
    async def classify(self, question: str, transcript: str) -> Label:
        text = transcript.lower()
        if _NEEDS_HELP.search(text):
            return "needs_help"
        if _OK.search(text):
            return "ok"
        return "unclear"


async def classify_reply(classifier: Classifier, question: str, transcript: str, timeout_s: float) -> Label:
    """Never raises. Empty text, a timeout, an error or an unexpected label all become "unclear", never "ok"."""
    if not transcript.strip():
        return "unclear"
    try:
        label = await asyncio.wait_for(classifier.classify(question, transcript), timeout_s)
    except Exception:
        log.exception("Classifier failed; treating reply as unclear")
        return "unclear"
    if label not in LABELS:
        log.error("Classifier returned unexpected label %r; treating as unclear", label)
        return "unclear"
    return label
