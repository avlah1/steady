"""Drawing helpers for the live view."""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from .detector import State
from .features import Features
from .keypoints import NUM_KEYPOINTS, SKELETON, Pose

_STATE_COLORS = {  # BGR
    State.MONITORING: (80, 200, 80),
    State.DESCENT: (0, 200, 255),
    State.DOWN: (0, 120, 255),
    State.ALERTED: (0, 0, 255),
}


def draw_pose(frame: np.ndarray, pose: Optional[Pose], min_conf: float = 0.5) -> None:
    if pose is None:
        return
    kp = pose.kp
    for a, b in SKELETON:
        if kp[a, 2] >= min_conf and kp[b, 2] >= min_conf:
            cv2.line(frame, (int(kp[a, 0]), int(kp[a, 1])), (int(kp[b, 0]), int(kp[b, 1])), (0, 255, 0), 2)
    for i in range(NUM_KEYPOINTS):
        if kp[i, 2] >= min_conf:
            cv2.circle(frame, (int(kp[i, 0]), int(kp[i, 1])), 3, (0, 0, 255), -1)


def draw_hud(
    frame: np.ndarray,
    state: State,
    feats: Optional[Features],
    drop: float,
    fps: float,
    banner: str = "",
) -> None:
    color = _STATE_COLORS[state]
    lines = [f"state: {state.value}   {fps:4.1f} fps"]
    if feats is not None:
        lines.append(f"angle {feats.angle:5.1f} deg   aspect {feats.aspect:4.2f}   drop {drop:4.2f} torso")
    else:
        lines.append("no pose")
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
        cv2.putText(frame, text, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)
    if banner:
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, h - 50), (w, h), (0, 0, 255), -1)
        cv2.putText(frame, banner, (12, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
