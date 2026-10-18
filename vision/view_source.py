"""Quick manual check that a FrameSource connects and produces frames.

Usage:
    python -m vision.view_source 0                       # webcam 0
    python -m vision.view_source http://<esp32-ip>/stream # ESP32-CAM MJPEG stream
    python -m vision.view_source path/to/video.mp4

Press q or Esc to quit.
"""
from __future__ import annotations

import argparse
import logging

import cv2

from .sources import open_source

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="webcam index, stream URL, or video file path")
    parser.add_argument("--pace", action="store_true", help="play a video file back at its original speed")
    args = parser.parse_args()

    with open_source(args.source, pace=args.pace) as src:
        window = "view_source (q to quit)"
        n_frames = 0
        while True:
            item = src.read(timeout=2.0)
            if item is None:
                if src.closed:
                    log.info("Source closed after %d frames", n_frames)
                    break
                log.warning("No frame within timeout; still waiting")
                continue
            frame, t = item
            n_frames += 1
            cv2.putText(frame, f"t={t:.2f}s frame={n_frames}", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imshow(window, frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
