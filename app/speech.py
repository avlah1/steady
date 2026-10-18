"""Speech-to-text for the Box's recorded replies. Runs Whisper locally (faster-whisper) on the laptop.

Same pattern as the classifier: a Transcriber interface, the real Whisper version, and a
wrapper that never raises. A failed transcription becomes empty text, which counts as "unclear".
"""

import asyncio
import io
import logging
import wave
from typing import Protocol

import numpy as np

log = logging.getLogger("speech")

SAMPLE_RATE = 16000  # what Whisper expects; the Box records at this rate so we never resample


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> str: ...


def read_wav(data: bytes) -> np.ndarray:
    """Turn WAV bytes into the float samples Whisper wants. Raises ValueError if not 16 kHz mono 16-bit.

    Read with the standard library rather than faster-whisper's own loader: that one goes through
    PyAV, whose current release is incompatible with it.
    """
    try:
        with wave.open(io.BytesIO(data)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                raise ValueError(
                    f"need 16 kHz mono 16-bit WAV, got {w.getframerate()} Hz, "
                    f"{w.getnchannels()} channel(s), {w.getsampwidth() * 8}-bit"
                )
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise ValueError(f"not a valid WAV file: {e}") from e
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768


class WhisperTranscriber:
    def __init__(self, model_name: str) -> None:
        from faster_whisper import WhisperModel  # imported here so tests never load it

        log.info("Loading Whisper model %s (downloads on first use)", model_name)
        # int8 on CPU: the benchmark on the demo laptop gave ~0.8 s per 5 s clip for base.en
        # with MediaPipe-like load. The GPU isn't worth the CUDA setup at that speed.
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        # beam_size=1 (greedy) is noticeably faster and fine for short answers.
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1)
        return "".join(s.text for s in segments).strip()


async def transcribe_reply(transcriber: Transcriber, audio: np.ndarray, timeout_s: float) -> str:
    """Never raises. A failure or timeout returns "", which the classifier treats as "unclear"."""
    try:
        # Whisper is CPU-heavy and blocking; run it in a thread so the server keeps answering
        # other requests (dashboard polls, detections) meanwhile.
        return await asyncio.wait_for(asyncio.to_thread(transcriber.transcribe, audio), timeout_s)
    except Exception:
        log.exception("Speech-to-text failed; treating reply as unclear")
        return ""
