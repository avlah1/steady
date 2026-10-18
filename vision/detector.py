"""Fall detection state machine.

Pure logic: it takes per-frame `Features` (or None when no person was found) with
timestamps and emits events. No camera or model code in here, so it can be
unit-tested with synthetic keypoint sequences.

    MONITORING --rapid hip drop--> DESCENT --torso horizontal--> DOWN
        ^                             |                           |
        |<------ stayed upright ------+                           +--still for N s--> ALERTED
        |<--------------------- recovered (stood back up) --------+                      |
        +<-------------------------------------------------------------------------------+

Distances are in torso lengths, which makes thresholds independent of how far
the person is from the camera.

All thresholds are first guesses. Tune them on your own clips (evaluate.py).
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from .features import Features


@dataclass
class DetectorConfig:
    # 1) Descent trigger: hips drop at least `min_drop_torso` torso lengths
    #    within `descent_window_s`. (Standing -> floor is ~1.5-2 torso lengths in
    #    well under a second; sitting down is ~0.8 over a second or more.)
    descent_window_s: float = 0.8
    min_drop_torso: float = 1.0
    min_samples: int = 3          # need this many frames in the window to trust it
    history_s: float = 3.0        # how much history is kept (also used for scale)

    # 2) Posture check: after the drop, the body must end up horizontal within
    #    `confirm_window_s`, otherwise it was a squat or sit.
    confirm_window_s: float = 2.0
    horizontal_angle_deg: float = 60.0   # torso angle from vertical
    horizontal_aspect: float = 1.2       # or body bbox wider than tall

    # 3) Stillness: stay down and barely move for `still_duration_s`.
    still_duration_s: float = 4.0
    still_max_move_torso: float = 0.5    # hip movement above this resets the timer
    max_down_s: float = 15.0             # alert anyway if down this long, moving or not

    # 4) Recovery: torso back near vertical for `recover_hold_s` cancels / ends it.
    recover_angle_deg: float = 40.0
    recover_hold_s: float = 1.0

    # 5) No pose found. Models often lose a person lying on the floor, so by
    #    default losing them after a drop counts as "still down".
    lost_counts_as_down: bool = True
    lost_as_down_s: float = 1.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectorConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown detector config keys: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def load(cls, path: str) -> "DetectorConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))


class State(str, Enum):
    MONITORING = "monitoring"
    DESCENT = "descent"
    DOWN = "down"
    ALERTED = "alerted"


@dataclass
class Event:
    kind: str   # descent | cancelled | down | alert | recovered
    t: float
    info: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.kind}@{self.t:.1f}s {self.info}" if self.info else f"{self.kind}@{self.t:.1f}s"


@dataclass
class _Sample:
    t: float
    hip_y: float
    torso_len: float


class FallDetector:
    def __init__(self, cfg: Optional[DetectorConfig] = None):
        self.cfg = cfg or DetectorConfig()
        self.reset()

    def reset(self) -> None:
        self.state = State.MONITORING
        self.last_drop = 0.0          # latest hip drop in torso lengths (for the HUD)
        self._hist: Deque[_Sample] = deque()
        self._scale = 1.0
        self._lost_since: Optional[float] = None
        self._last_hip: Optional[np.ndarray] = None
        self._t_descent = 0.0
        self._t_down = 0.0
        self._still_since = 0.0
        self._anchor: Optional[np.ndarray] = None
        self._upright_since: Optional[float] = None

    # ------------------------------------------------------------------ API
    def update(self, t: float, f: Optional[Features]) -> List[Event]:
        cfg = self.cfg
        events: List[Event] = []

        if f is not None:
            self._lost_since = None
            self._last_hip = f.hip_xy
            self._hist.append(_Sample(t, f.hip_y, f.torso_len))
        elif self._lost_since is None:
            self._lost_since = t
        while self._hist and t - self._hist[0].t > cfg.history_s:
            self._hist.popleft()

        # Sequential ifs (not elif) so a transition can be acted on in the same frame.
        if self.state is State.MONITORING:
            self._monitor(t, f, events)
        if self.state is State.DESCENT:
            self._descent(t, f, events)
        if self.state is State.DOWN:
            self._down(t, f, events)
        elif self.state is State.ALERTED:
            self._alerted(t, f, events)
        return events

    # --------------------------------------------------------------- states
    def _monitor(self, t: float, f: Optional[Features], events: List[Event]) -> None:
        if f is None:
            return
        cfg = self.cfg
        # Median torso length over recent history = robust distance-independent scale.
        self._scale = max(float(np.median([s.torso_len for s in self._hist])), 1e-6)
        window = [s for s in self._hist if t - s.t <= cfg.descent_window_s]
        if len(window) < cfg.min_samples:
            return
        ref = min(s.hip_y for s in window)          # highest hip position in the window
        drop = (f.hip_y - ref) / self._scale        # image y grows downward
        self.last_drop = drop
        if drop >= cfg.min_drop_torso:
            self.state = State.DESCENT
            self._t_descent = t
            events.append(Event("descent", t, {"drop_torso": round(drop, 2)}))

    def _descent(self, t: float, f: Optional[Features], events: List[Event]) -> None:
        cfg = self.cfg
        horizontal = f is not None and self._is_horizontal(f)
        lost = (
            f is None
            and cfg.lost_counts_as_down
            and self._lost_since is not None
            and t - self._lost_since >= cfg.lost_as_down_s
        )
        if horizontal or lost:
            self.state = State.DOWN
            self._t_down = t
            self._still_since = t
            self._anchor = None if self._last_hip is None else self._last_hip.copy()
            self._upright_since = None
            events.append(Event("down", t, {"pose_lost": bool(lost)}))
        elif t - self._t_descent > cfg.confirm_window_s:
            self.state = State.MONITORING
            events.append(Event("cancelled", t, {"reason": "stayed upright"}))

    def _down(self, t: float, f: Optional[Features], events: List[Event]) -> None:
        cfg = self.cfg
        if f is not None:
            if self._recovered(t, f):
                self.state = State.MONITORING
                events.append(Event("recovered", t, {"alerted": False}))
                return
            if self._anchor is None:
                self._anchor = f.hip_xy.copy()
            elif np.linalg.norm(f.hip_xy - self._anchor) > cfg.still_max_move_torso * self._scale:
                self._anchor = f.hip_xy.copy()
                self._still_since = t
        elif (
            not cfg.lost_counts_as_down
            and self._lost_since is not None
            and t - self._lost_since >= cfg.lost_as_down_s
        ):
            self.state = State.MONITORING
            events.append(Event("cancelled", t, {"reason": "pose lost"}))
            return

        if t - self._still_since >= cfg.still_duration_s:
            self._raise_alert(t, events, "still")
        elif t - self._t_down >= cfg.max_down_s:
            self._raise_alert(t, events, "down_too_long")

    def _alerted(self, t: float, f: Optional[Features], events: List[Event]) -> None:
        if f is not None and self._recovered(t, f):
            self.state = State.MONITORING
            events.append(Event("recovered", t, {"alerted": True}))

    # -------------------------------------------------------------- helpers
    def _raise_alert(self, t: float, events: List[Event], reason: str) -> None:
        self.state = State.ALERTED
        self._upright_since = None
        events.append(Event("alert", t, {"reason": reason, "down_for_s": round(t - self._t_down, 1)}))

    def _is_horizontal(self, f: Features) -> bool:
        return f.angle >= self.cfg.horizontal_angle_deg or f.aspect >= self.cfg.horizontal_aspect

    def _recovered(self, t: float, f: Features) -> bool:
        """True once the torso has been near-vertical for `recover_hold_s`."""
        if f.angle < self.cfg.recover_angle_deg:
            if self._upright_since is None:
                self._upright_since = t
            return t - self._upright_since >= self.cfg.recover_hold_s
        self._upright_since = None
        return False
