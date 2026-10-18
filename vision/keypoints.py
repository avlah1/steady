"""Common keypoint format shared by every pose estimator.

All estimators return a `Pose` holding COCO-17 keypoints in *pixel* coordinates
(x right, y down). Working in pixels (not normalized 0..1) keeps angles and
lengths correct on non-square frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np


class Kp(IntEnum):
    NOSE = 0
    L_EYE = 1
    R_EYE = 2
    L_EAR = 3
    R_EAR = 4
    L_SHOULDER = 5
    R_SHOULDER = 6
    L_ELBOW = 7
    R_ELBOW = 8
    L_WRIST = 9
    R_WRIST = 10
    L_HIP = 11
    R_HIP = 12
    L_KNEE = 13
    R_KNEE = 14
    L_ANKLE = 15
    R_ANKLE = 16


NUM_KEYPOINTS = 17

# Edges used when drawing the skeleton.
SKELETON = [
    (Kp.L_SHOULDER, Kp.R_SHOULDER),
    (Kp.L_SHOULDER, Kp.L_ELBOW), (Kp.L_ELBOW, Kp.L_WRIST),
    (Kp.R_SHOULDER, Kp.R_ELBOW), (Kp.R_ELBOW, Kp.R_WRIST),
    (Kp.L_SHOULDER, Kp.L_HIP), (Kp.R_SHOULDER, Kp.R_HIP),
    (Kp.L_HIP, Kp.R_HIP),
    (Kp.L_HIP, Kp.L_KNEE), (Kp.L_KNEE, Kp.L_ANKLE),
    (Kp.R_HIP, Kp.R_KNEE), (Kp.R_KNEE, Kp.R_ANKLE),
    (Kp.NOSE, Kp.L_SHOULDER), (Kp.NOSE, Kp.R_SHOULDER),
]

# Head, torso and legs. Arms are left out when measuring the body's bounding
# box so that outstretched arms don't distort its aspect ratio.
BODY_CORE = (
    Kp.NOSE,
    Kp.L_SHOULDER, Kp.R_SHOULDER,
    Kp.L_HIP, Kp.R_HIP,
    Kp.L_KNEE, Kp.R_KNEE,
    Kp.L_ANKLE, Kp.R_ANKLE,
)


@dataclass
class Pose:
    """One detected person.

    kp: float array of shape (17, 3) holding (x_px, y_px, confidence).
    """

    kp: np.ndarray
    frame_w: int
    frame_h: int
