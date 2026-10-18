"""Read frames from a FrameSource and run MediaPipe BlazePose on each one.

Usage:
    python -m vision.view_pose 0
    python -m vision.view_pose http://<esp32-ip>:81/stream
    python -m vision.view_pose path/to/video.mp4 [--pace] [--variant lite|full|heavy]

Press q or Esc to quit.
"""
from __future__ import annotations

import argparse
import logging

import cv2

from .keypoints import SKELETON, Pose
from .pose import get_estimator
from .sources import open_source

log = logging.getLogger(__name__)

MIN_KP_CONFIDENCE = 0.3


def draw_pose(frame, pose: Pose) -> None:
    kp = pose.kp
    for a, b in SKELETON:
        if kp[a, 2] < MIN_KP_CONFIDENCE or kp[b, 2] < MIN_KP_CONFIDENCE:
            continue
        pa = (int(kp[a, 0]), int(kp[a, 1]))
        pb = (int(kp[b, 0]), int(kp[b, 1]))
        cv2.line(frame, pa, pb, (0, 255, 0), 2)
    for x, y, c in kp:
        if c < MIN_KP_CONFIDENCE:
            continue
        cv2.circle(frame, (int(x), int(y)), 4, (0, 0, 255), -1)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="webcam index, stream URL, or video file path")
    parser.add_argument("--pace", action="store_true", help="play a video file back at its original speed")
    parser.add_argument("--variant", default="full", choices=["lite", "full", "heavy"])
    args = parser.parse_args()

    estimator = get_estimator(variant=args.variant)
    window = "view_pose (q to quit)"
    try:
        with open_source(args.source, pace=args.pace) as src:
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
                pose = estimator.estimate(frame, t)
                if pose is not None:
                    draw_pose(frame, pose)
                else:
                    cv2.putText(frame, "no person detected", (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.imshow(window, frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        estimator.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
