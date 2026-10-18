"""Pose estimators.

Each estimator implements `estimate(frame_bgr, t) -> Pose | None` and returns the
shared COCO-17 format from `keypoints.py`. To try another model (YOLO-pose,
RTMPose, ...) subclass `PoseEstimator`, convert its output to that format and
register it in `get_estimator`. Nothing downstream needs to change.
"""
from __future__ import annotations

import logging
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .keypoints import NUM_KEYPOINTS, Pose

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# COCO-17 index -> MediaPipe BlazePose (33 landmarks) index.
_COCO_FROM_MP = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]

_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_{v}/float16/latest/pose_landmarker_{v}.task"
)


class PoseEstimator(ABC):
    name = "base"

    @abstractmethod
    def estimate(self, frame_bgr: np.ndarray, t: float) -> Optional[Pose]:
        """Return the (single) detected person, or None if nobody was found.
        `t` is the frame timestamp in seconds, non-decreasing."""

    def close(self) -> None:
        pass


def _to_pose(landmarks, w: int, h: int) -> Pose:
    kp = np.zeros((NUM_KEYPOINTS, 3), dtype=np.float32)
    for i, m in enumerate(_COCO_FROM_MP):
        lm = landmarks[m]
        vis = getattr(lm, "visibility", None)
        kp[i] = (lm.x * w, lm.y * h, float(vis) if vis is not None else 1.0)
    return Pose(kp, w, h)


def ensure_model(variant: str, models_dir: Optional[Path] = None) -> str:
    """Return the path to a MediaPipe pose_landmarker model, downloading it once."""
    models_dir = Path(models_dir) if models_dir else PROJECT_ROOT / "models"
    path = models_dir / f"pose_landmarker_{variant}.task"
    if path.exists():
        return str(path)
    url = _MODEL_URL.format(v=variant)
    models_dir.mkdir(parents=True, exist_ok=True)
    log.info("Downloading MediaPipe model %s -> %s", url, path)
    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    except Exception as exc:
        raise RuntimeError(
            f"Could not download the pose model. Download it manually from\n  {url}\n"
            f"and save it as\n  {path}"
        ) from exc
    return str(path)


class MediaPipePose(PoseEstimator):
    """MediaPipe BlazePose. Uses the current Tasks API (needs a .task model file,
    downloaded automatically) and falls back to the legacy `mp.solutions.pose`
    if the installed MediaPipe has no Tasks API.

    variant: 'lite' | 'full' | 'heavy'. 'full' is a sensible default for a wide
    room view, where the person is small in the frame.
    """

    name = "mediapipe"

    def __init__(
        self,
        variant: str = "full",
        model_path: Optional[str] = None,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ):
        import mediapipe as mp

        self._mp = mp
        self._landmarker = None
        self._legacy = None
        self._last_ts_ms = -1

        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            has_tasks = hasattr(vision, "PoseLandmarker")
        except ImportError:
            has_tasks = False

        if has_tasks:
            options = vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(
                    model_asset_path=model_path or ensure_model(variant)
                ),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=min_detection_confidence,
                min_pose_presence_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self._landmarker = vision.PoseLandmarker.create_from_options(options)
        else:
            log.warning("MediaPipe Tasks API not found; using legacy solutions API")
            complexity = {"lite": 0, "full": 1, "heavy": 2}[variant]
            self._legacy = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=complexity,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )

    def estimate(self, frame_bgr: np.ndarray, t: float) -> Optional[Pose]:
        h, w = frame_bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        if self._landmarker is not None:
            ts_ms = int(t * 1000)
            if ts_ms <= self._last_ts_ms:  # Tasks API needs strictly increasing ms
                ts_ms = self._last_ts_ms + 1
            self._last_ts_ms = ts_ms
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
            result = self._landmarker.detect_for_video(image, ts_ms)
            if not result.pose_landmarks:
                return None
            return _to_pose(result.pose_landmarks[0], w, h)
        result = self._legacy.process(rgb)
        if result.pose_landmarks is None:
            return None
        return _to_pose(result.pose_landmarks.landmark, w, h)

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
        if self._legacy is not None:
            self._legacy.close()


def get_estimator(name: str = "mediapipe", variant: str = "full", **kwargs) -> PoseEstimator:
    if name == "mediapipe":
        return MediaPipePose(variant=variant, **kwargs)
    raise ValueError(f"Unknown estimator '{name}'. Available: mediapipe")
