"""Run a folder of recorded clips through the pipeline and report what fired.

Convention: a clip whose filename starts with "fall" is expected to raise an
alert; everything else (adl_sit.mp4, adl_bend.mp4, ...) is expected NOT to.

    python -m fall_detector.evaluate clips --target-fps 10

Pose estimation is the slow part, so keypoints are cached in cache/. After the
first run you can edit a config JSON and re-run in seconds to tune thresholds:

    python -m fall_detector.evaluate clips --target-fps 10 --config my_thresholds.json

--target-fps resamples each clip to that rate before pose estimation, to mimic
what the ESP32 will actually deliver over Wi-Fi (usually 5-15 fps).
"""
from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from .detector import DetectorConfig, Event
from .keypoints import NUM_KEYPOINTS, Pose
from .pipeline import FallAnalyzer
from .pose import PROJECT_ROOT, PoseEstimator, get_estimator

log = logging.getLogger("fall_detector")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}


@dataclass
class ClipResult:
    name: str
    expected: bool
    duration_s: float
    pose_rate: float
    events: List[Event] = field(default_factory=list)

    @property
    def alerts(self) -> List[float]:
        return [e.t for e in self.events if e.kind == "alert"]

    @property
    def outcome(self) -> str:
        if self.expected:
            return "TP" if self.alerts else "FN"
        return "FP" if self.alerts else "TN"


# ------------------------------------------------------------- pose extraction
def extract_poses(
    clip: Path, estimator: PoseEstimator, target_fps: Optional[float]
) -> Tuple[np.ndarray, np.ndarray, Tuple[int, int]]:
    """Run the estimator over a clip. Returns (timestamps, keypoints[N,17,3], (w, h)).
    Frames with no detected person are all-NaN rows."""
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise IOError(f"Could not open {clip}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = fps if fps and fps == fps and fps >= 1 else 30.0
    step = 1.0 / target_fps if target_fps else 0.0
    eps = 0.5 / fps
    next_t = 0.0
    ts: List[float] = []
    kps: List[np.ndarray] = []
    size = (0, 0)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        idx += 1
        if t + 1e-9 < next_t:
            continue
        next_t = t + step - eps if step else 0.0
        size = (frame.shape[1], frame.shape[0])
        pose = estimator.estimate(frame, t)
        ts.append(t)
        kps.append(pose.kp if pose is not None else np.full((NUM_KEYPOINTS, 3), np.nan, np.float32))
    cap.release()
    return np.array(ts), np.array(kps, dtype=np.float32), size


def _load_or_extract(clip, estimator_factory, target_fps, cache_path: Optional[Path]):
    st = clip.stat()
    stamp = np.array([st.st_mtime, st.st_size])
    if cache_path is not None and cache_path.exists():
        data = np.load(cache_path)
        if np.allclose(data["stamp"], stamp):
            log.info("  using cached keypoints %s", cache_path.name)
            return data["t"], data["kp"], tuple(int(v) for v in data["size"])
    estimator = estimator_factory(clip)
    try:
        ts, kps, size = extract_poses(clip, estimator, target_fps)
    finally:
        estimator.close()
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache_path, t=ts, kp=kps, size=np.array(size), stamp=stamp)
    return ts, kps, size


# --------------------------------------------------------------------- analysis
def analyze(ts, kps, size, cfg: DetectorConfig, rows: Optional[list] = None) -> Tuple[List[Event], float]:
    analyzer = FallAnalyzer(cfg)
    events: List[Event] = []
    with_pose = 0
    for t, kp in zip(ts, kps):
        pose = None if np.isnan(kp).all() else Pose(kp.copy(), size[0], size[1])
        with_pose += pose is not None
        feats, evs = analyzer.update(float(t), pose)
        events.extend(evs)
        if rows is not None:
            rows.append([
                round(float(t), 3),
                int(pose is not None),
                "" if feats is None else round(feats.hip_y, 1),
                "" if feats is None else round(feats.torso_len, 1),
                "" if feats is None else round(feats.angle, 1),
                "" if feats is None else round(feats.aspect, 2),
                analyzer.state.value,
            ])
    return events, (with_pose / len(ts) if len(ts) else 0.0)


def run_evaluation(
    clips_dir: Path,
    estimator_factory: Callable[[Path], PoseEstimator],
    *,
    cfg: Optional[DetectorConfig] = None,
    target_fps: Optional[float] = None,
    cache_dir: Optional[Path] = None,
    estimator_tag: str = "est",
    features_dir: Optional[Path] = None,
    expect_prefix: str = "fall",
) -> List[ClipResult]:
    cfg = cfg or DetectorConfig()
    clips = sorted(p for p in Path(clips_dir).iterdir() if p.suffix.lower() in VIDEO_EXTS)
    results: List[ClipResult] = []
    for clip in clips:
        log.info("%s", clip.name)
        cache_path = None
        if cache_dir is not None:
            fps_tag = f"fps{target_fps:g}" if target_fps else "native"
            cache_path = Path(cache_dir) / f"{clip.stem}.{estimator_tag}.{fps_tag}.npz"
        ts, kps, size = _load_or_extract(clip, estimator_factory, target_fps, cache_path)
        rows: Optional[list] = [] if features_dir else None
        events, pose_rate = analyze(ts, kps, size, cfg, rows)
        if features_dir:
            Path(features_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(features_dir) / f"{clip.stem}.csv", "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["t", "pose", "hip_y", "torso_len", "angle", "aspect", "state"])
                w.writerows(rows)
        results.append(ClipResult(
            name=clip.name,
            expected=clip.stem.lower().startswith(expect_prefix.lower()),
            duration_s=float(ts[-1]) if len(ts) else 0.0,
            pose_rate=pose_rate,
            events=events,
        ))
    return results


def format_report(results: List[ClipResult], verbose: bool = False) -> str:
    lines = [f"{'clip':32} {'expect':6} {'result':6} {'pose%':>5}  alerts / events"]
    for r in results:
        alerts = ", ".join(f"{t:.1f}s" for t in r.alerts) or "-"
        lines.append(
            f"{r.name:32} {'fall' if r.expected else 'no':6} {r.outcome:6} {r.pose_rate * 100:5.0f}  {alerts}"
        )
        if verbose:
            lines.append("    " + ("; ".join(str(e) for e in r.events) or "(no events)"))
    tp = sum(r.outcome == "TP" for r in results)
    fn = sum(r.outcome == "FN" for r in results)
    fp = sum(r.outcome == "FP" for r in results)
    tn = sum(r.outcome == "TN" for r in results)
    lines.append("")
    lines.append(f"falls caught: {tp}/{tp + fn}    false alarms: {fp}/{fp + tn} non-fall clips")
    low = [r.name for r in results if r.pose_rate < 0.5]
    if low:
        lines.append(f"warning: pose found in <50% of frames for: {', '.join(low)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("clips_dir")
    p.add_argument("--estimator", default="mediapipe")
    p.add_argument("--model-variant", default="full", choices=["lite", "full", "heavy"])
    p.add_argument("--target-fps", type=float, help="resample clips to this rate (e.g. 10)")
    p.add_argument("--config", help="JSON file overriding DetectorConfig fields")
    p.add_argument("--cache-dir", default=str(PROJECT_ROOT / "cache"))
    p.add_argument("--no-cache", action="store_true", help="ignore and don't write the keypoint cache")
    p.add_argument("--features-dir", help="write per-frame feature CSVs here (for plotting thresholds)")
    p.add_argument("--expect-prefix", default="fall", help="filename prefix of clips that contain a fall")
    p.add_argument("-v", "--verbose", action="store_true", help="list every event per clip")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = DetectorConfig.load(args.config) if args.config else DetectorConfig()
    results = run_evaluation(
        Path(args.clips_dir),
        lambda clip: get_estimator(args.estimator, variant=args.model_variant),
        cfg=cfg,
        target_fps=args.target_fps,
        cache_dir=None if args.no_cache else Path(args.cache_dir),
        estimator_tag=f"{args.estimator}-{args.model_variant}",
        features_dir=Path(args.features_dir) if args.features_dir else None,
        expect_prefix=args.expect_prefix,
    )
    if not results:
        print(f"No video files found in {args.clips_dir}")
        return 1
    print()
    print(format_report(results, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
