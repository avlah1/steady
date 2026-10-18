"""Turn raw keypoints into the few numbers the fall detector needs.

Everything is expressed relative to the person's own torso length, so the same
thresholds work whether they are near the camera or across the room.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .keypoints import BODY_CORE, Kp, Pose


@dataclass
class Features:
    t: float
    hip_xy: np.ndarray        # smoothed hip centre, pixels
    shoulder_xy: np.ndarray   # smoothed shoulder centre, pixels
    torso_len: float          # shoulder-centre to hip-centre distance, pixels
    angle: float              # torso angle from vertical: 0 = upright, 90 = horizontal
    aspect: float             # body bbox width / height (arms excluded)

    @property
    def hip_y(self) -> float:
        return float(self.hip_xy[1])


def _center(kp: np.ndarray, ids, min_conf: float) -> Optional[np.ndarray]:
    pts = [kp[i, :2] for i in ids if kp[i, 2] >= min_conf and np.all(np.isfinite(kp[i, :2]))]
    if not pts:
        return None
    return np.mean(pts, axis=0)


class FeatureExtractor:
    """Stateful: smooths the hip/shoulder centres with a time-based exponential
    moving average (so behaviour doesn't depend on frame rate).

    If the hips leave the frame (e.g. the person steps very close to the
    camera) but the shoulders are still tracked, the last known hip position
    is held rather than reporting "no person" - otherwise the detector can't
    tell that apart from the person actually being gone, and may treat it as
    a fall. Held for up to `hip_hold_s`; beyond that we give up and report
    nothing, same as if the person had genuinely left.
    """

    def __init__(
        self,
        min_conf: float = 0.5,
        min_torso_px: float = 8.0,
        smooth_tau_s: float = 0.15,
        gap_reset_s: float = 1.0,
        hip_hold_s: float = 10.0,
    ):
        self.min_conf = min_conf
        self.min_torso_px = min_torso_px
        self.tau = smooth_tau_s
        self.gap_reset_s = gap_reset_s
        self.hip_hold_s = hip_hold_s
        self.reset()

    def reset(self) -> None:
        self._hip: Optional[np.ndarray] = None
        self._sh: Optional[np.ndarray] = None
        self._last_t: Optional[float] = None
        self._hip_seen_t: Optional[float] = None  # last time the hip was actually measured, not held

    def extract(self, t: float, pose: Optional[Pose]) -> Optional[Features]:
        if pose is None:
            return None
        kp = pose.kp
        sh = _center(kp, (Kp.L_SHOULDER, Kp.R_SHOULDER), self.min_conf)
        hip = _center(kp, (Kp.L_HIP, Kp.R_HIP), self.min_conf)
        if sh is None:
            return None
        if hip is None:
            if self._hip is None or self._hip_seen_t is None or t - self._hip_seen_t > self.hip_hold_s:
                return None
            hip = self._hip  # hold: feeds back into the EMA below as a zero delta
        else:
            self._hip_seen_t = t

        if (
            self._last_t is None
            or self.tau <= 0
            or t - self._last_t > self.gap_reset_s
        ):
            self._hip, self._sh = hip, sh
        else:
            alpha = 1.0 - math.exp(-max(t - self._last_t, 1e-3) / self.tau)
            self._hip = self._hip + alpha * (hip - self._hip)
            self._sh = self._sh + alpha * (sh - self._sh)
        self._last_t = t

        v = self._sh - self._hip  # hip -> shoulder
        torso_len = float(np.hypot(v[0], v[1]))
        if torso_len < self.min_torso_px:
            return None
        angle = math.degrees(math.atan2(abs(v[0]), -v[1]))
        return Features(
            t=t,
            hip_xy=self._hip.copy(),
            shoulder_xy=self._sh.copy(),
            torso_len=torso_len,
            angle=angle,
            aspect=self._aspect(kp),
        )

    def _aspect(self, kp: np.ndarray) -> float:
        pts = kp[[int(k) for k in BODY_CORE]]
        ok = (pts[:, 2] >= self.min_conf) & np.all(np.isfinite(pts[:, :2]), axis=1)
        if ok.sum() < 4:
            return 0.0
        xs, ys = pts[ok, 0], pts[ok, 1]
        return float((xs.max() - xs.min()) / max(ys.max() - ys.min(), 1e-6))
