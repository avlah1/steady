"""Real-time loop: frames -> pose -> fall detector -> alerts.

Examples (run from the project root):
    python -m fall_detector.live --source 0                          # webcam
    python -m fall_detector.live --source clips/fall_01.mp4 --pace   # replay a clip
    python -m fall_detector.live --source http://192.168.1.50:81/stream --save-clips alerts

Keys in the window: q = quit, r = reset the detector.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

import cv2

from .alerts import Alerter
from .clips import ClipRecorder
from .detector import DetectorConfig
from .pipeline import FallAnalyzer
from .pose import get_estimator
from .sources import open_source
from .viz import draw_hud, draw_pose

log = logging.getLogger("fall_detector")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="0", help="webcam index, video file, or stream URL (default 0)")
    p.add_argument("--estimator", default="mediapipe")
    p.add_argument("--model-variant", default="full", choices=["lite", "full", "heavy"])
    p.add_argument("--config", help="JSON file overriding DetectorConfig fields")
    p.add_argument("--max-width", type=int, help="downscale frames wider than this (faster, but small people get smaller)")
    p.add_argument("--pace", action="store_true", help="play video files at their original speed")
    p.add_argument("--no-display", action="store_true", help="run headless")
    p.add_argument("--save-clips", metavar="DIR", help="save the last --clip-seconds of video whenever an alert fires")
    p.add_argument("--clip-seconds", type=float, default=12.0)
    p.add_argument("--webhook", help="URL to POST a JSON alert to")
    p.add_argument("--mqtt-host")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-topic", default="fall_detector/alert")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    display = not args.no_display

    cfg = DetectorConfig.load(args.config) if args.config else DetectorConfig()
    estimator = get_estimator(args.estimator, variant=args.model_variant)
    analyzer = FallAnalyzer(cfg)
    alerter = Alerter(args.webhook, args.mqtt_host, args.mqtt_port, args.mqtt_topic)

    recorder = ClipRecorder(args.clip_seconds) if args.save_clips else None
    clip_dir = Path(args.save_clips) if args.save_clips else None
    if clip_dir:
        clip_dir.mkdir(parents=True, exist_ok=True)

    source = open_source(args.source, pace=args.pace)
    banner, banner_until = "", 0.0
    fps, last = 0.0, time.perf_counter()

    try:
        while True:
            item = source.read()
            if item is None:
                if source.closed:
                    break
                if display and cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue
            frame, t = item
            if args.max_width and frame.shape[1] > args.max_width:
                scale = args.max_width / frame.shape[1]
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if recorder:
                recorder.add(t, frame)

            pose = estimator.estimate(frame, t)
            feats, events = analyzer.update(t, pose)

            for ev in events:
                log.info("%s", ev)
                if ev.kind == "alert":
                    clip = None
                    if recorder:
                        clip = recorder.save(clip_dir / f"alert_{datetime.now():%Y%m%d_%H%M%S}.mp4")
                    alerter.send(ev, clip)
                    banner, banner_until = "FALL ALERT", time.monotonic() + 5.0

            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6) if fps else 1.0 / max(now - last, 1e-6)
            last = now

            if display:
                vis = frame.copy()
                draw_pose(vis, pose)
                if time.monotonic() > banner_until:
                    banner = ""
                draw_hud(vis, analyzer.state, feats, analyzer.detector.last_drop, fps, banner)
                cv2.imshow("fall_detector", vis)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    analyzer.reset()
                    log.info("Detector reset")
    except KeyboardInterrupt:
        pass
    finally:
        source.release()
        estimator.close()
        if display:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
