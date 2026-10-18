"""Frame sources: webcam, video file, or network stream (e.g. an ESP32-CAM).

Every source exposes `read() -> (frame_bgr, timestamp_seconds) | None`, so the
rest of the program doesn't care where frames come from. Swapping the webcam
for the ESP32 later is just a different `--source` string.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple, Union

import cv2
import numpy as np

log = logging.getLogger(__name__)

Item = Tuple[np.ndarray, float]


class FrameSource:
    is_live = False
    closed = False

    def read(self, timeout: float = 1.0) -> Optional[Item]:
        """Return (frame, t). None means "nothing right now" or "finished";
        check `.closed` to tell the two apart."""
        raise NotImplementedError

    def release(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


class FileSource(FrameSource):
    """Video file. Timestamps come from the frame index, so results are
    repeatable and independent of how fast we process."""

    def __init__(self, path: str, pace: bool = False):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise IOError(f"Could not open video file: {path}")
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if fps and fps == fps and fps >= 1 else 30.0
        self.pace = pace
        self._idx = 0
        self._start: Optional[float] = None

    def read(self, timeout: float = 1.0) -> Optional[Item]:
        ok, frame = self.cap.read()
        if not ok:
            self.closed = True
            return None
        t = self._idx / self.fps
        self._idx += 1
        if self.pace:  # play back at the original speed (for eyeballing)
            now = time.monotonic()
            if self._start is None:
                self._start = now
            delay = self._start + t - now
            if delay > 0:
                time.sleep(delay)
        return frame, t

    def release(self) -> None:
        self.closed = True
        self.cap.release()


class LiveSource(FrameSource):
    """Webcam index or stream URL, read on a background thread.

    Only the newest frame is kept. If processing is slower than the camera,
    frames are dropped instead of queueing up, so latency never builds.
    Network streams reconnect automatically. Timestamps are arrival times.
    """

    is_live = True

    def __init__(self, target: Union[int, str]):
        self.target = target
        self._cv = threading.Condition()
        self._frame: Optional[np.ndarray] = None
        self._t = 0.0
        self._seq = 0
        self._last_seq = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def closed(self) -> bool:  # type: ignore[override]
        return self._stop.is_set()

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.target)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def _run(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        while not self._stop.is_set():
            if cap is None:
                cap = self._open()
                if not cap.isOpened():
                    log.warning("Could not open %s; retrying in 2s", self.target)
                    cap.release()
                    cap = None
                    self._stop.wait(2.0)
                    continue
                log.info("Connected to %s", self.target)
            ok, frame = cap.read()
            if not ok:
                log.warning("Read failed on %s; reconnecting", self.target)
                cap.release()
                cap = None
                self._stop.wait(1.0)
                continue
            with self._cv:
                self._frame = frame
                self._t = time.monotonic()
                self._seq += 1
                self._cv.notify_all()
        if cap is not None:
            cap.release()

    def read(self, timeout: float = 1.0) -> Optional[Item]:
        with self._cv:
            self._cv.wait_for(
                lambda: self._seq != self._last_seq or self._stop.is_set(), timeout
            )
            if self._stop.is_set() or self._seq == self._last_seq:
                return None
            self._last_seq = self._seq
            return self._frame, self._t  # type: ignore[return-value]

    def release(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        self._thread.join(timeout=2.0)


def open_source(spec: Union[str, int], pace: bool = False) -> FrameSource:
    """'0' -> webcam 0, 'http://...' / 'rtsp://...' -> stream, else a file path."""
    s = str(spec)
    if s.isdigit():
        return LiveSource(int(s))
    if "://" in s:
        return LiveSource(s)
    return FileSource(s, pace=pace)
