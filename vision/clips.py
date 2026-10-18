"""Ring buffer of recent frames, saved as a short clip when an alert fires.

Saved clips are the raw frames (no overlay), so you can drop them straight into
`clips/` (name them fall_*.mp4 or adl_*.mp4) and use them for tuning.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Deque, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)


class ClipRecorder:
    def __init__(self, seconds: float = 12.0, jpeg_quality: int = 85):
        self.seconds = seconds
        self.quality = jpeg_quality
        self._buf: Deque[Tuple[float, np.ndarray]] = deque()

    def add(self, t: float, frame: np.ndarray) -> None:
        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return
        self._buf.append((t, enc))  # JPEG-compressed to keep memory small
        while self._buf and t - self._buf[0][0] > self.seconds:
            self._buf.popleft()

    def save(self, path: Path) -> Optional[Path]:
        frames = list(self._buf)
        if len(frames) < 2:
            return None
        duration = frames[-1][0] - frames[0][0]
        fps = (len(frames) - 1) / duration if duration > 0 else 10.0
        fps = max(1.0, min(60.0, fps))
        first = cv2.imdecode(frames[0][1], cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        if not writer.isOpened():
            log.error("Could not open video writer for %s", path)
            return None
        for _, enc in frames:
            writer.write(cv2.imdecode(enc, cv2.IMREAD_COLOR))
        writer.release()
        log.info("Saved clip %s (%d frames, %.1f fps)", path, len(frames), fps)
        return path
