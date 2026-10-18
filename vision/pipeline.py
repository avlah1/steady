"""Glue: pose -> features -> detector. Shared by live.py and evaluate.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .detector import DetectorConfig, Event, FallDetector, State
from .features import FeatureExtractor, Features
from .keypoints import Pose
from .pose import PoseEstimator


class FallAnalyzer:
    """Everything after pose estimation. Feed it (timestamp, Pose | None)."""

    def __init__(
        self,
        cfg: Optional[DetectorConfig] = None,
        min_conf: float = 0.5,
        smooth_tau_s: float = 0.15,
    ):
        self.extractor = FeatureExtractor(min_conf=min_conf, smooth_tau_s=smooth_tau_s)
        self.detector = FallDetector(cfg)

    @property
    def state(self) -> State:
        return self.detector.state

    def update(self, t: float, pose: Optional[Pose]) -> Tuple[Optional[Features], List[Event]]:
        feats = self.extractor.extract(t, pose)
        return feats, self.detector.update(t, feats)

    def reset(self) -> None:
        self.extractor.reset()
        self.detector.reset()


@dataclass
class FrameResult:
    pose: Optional[Pose]
    features: Optional[Features]
    events: List[Event] = field(default_factory=list)
    state: State = State.MONITORING


class Pipeline:
    def __init__(self, estimator: PoseEstimator, analyzer: FallAnalyzer):
        self.estimator = estimator
        self.analyzer = analyzer

    def process(self, frame_bgr: np.ndarray, t: float) -> FrameResult:
        pose = self.estimator.estimate(frame_bgr, t)
        feats, events = self.analyzer.update(t, pose)
        return FrameResult(pose, feats, events, self.analyzer.state)
