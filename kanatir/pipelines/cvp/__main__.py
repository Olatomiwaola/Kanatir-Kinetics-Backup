"""
Computer Vision Pipeline (CVP)
RTSP/ONVIF source -> YOLOv8-nano detection + ByteTrack -> privacy gate
(face blur + plate hash, fail-closed, audited) -> features.video envelope.

Run (host-native arm64):
    python3 -m kanatir.pipelines.cvp --source rtsp://... --sensor-id cam-01
    python3 -m kanatir.pipelines.cvp --source 0 --sensor-id webcam   # local test

Order of operations per frame is privacy-first:
    1. capture frame
    2. PRIVACY GATE: scrub PII (blur faces / hash plates) + write audit event,
       BEFORE any features leave the box. Fail-closed: on error the frame is
       dropped and nothing is published.
    3. detect + track on the SCRUBBED frame
    4. build + publish FeatureEnvelope to features.video
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import cv2
import structlog

from kanatir.pipelines.common.envelope import (
    Detection,
    FeatureEnvelope,
    GeoRef,
    Modality,
    VideoFeatures,
)
from kanatir.pipelines.common.privacy_gate import (
    PrivacyGateError,
    run_privacy_gate,
)
from kanatir.pipelines.common.producer import EnvelopeProducer
from kanatir.pipelines.cvp.privacy import scrub_frame

log = structlog.get_logger("pipelines.cvp")

TOPIC = "features.video"
ACTOR = "cvp"


def _load_model(weights: str):
    # Imported lazily so the package imports cheaply in CI without torch.
    from ultralytics import YOLO

    return YOLO(weights)


def run(
    source: str,
    sensor_id: str,
    weights: str = "yolov8n.pt",
    conf: float = 0.35,
    geo: GeoRef | None = None,
    max_frames: int | None = None,
    bootstrap: str | None = None,
) -> None:
    model = _load_model(weights)
    producer = EnvelopeProducer(bootstrap=bootstrap)

    src: int | str = int(source) if source.isdigit() else source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video source: {source}")

    log.info("cvp.started", source=source, sensor_id=sensor_id, weights=weights)
    frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                log.info("cvp.source_ended", frames=frames)
                break

            capture_ts = datetime.now(UTC)
            h, w = frame.shape[:2]

            # 1) PRIVACY GATE FIRST — scrub + audit before anything else.
            try:
                privacy = run_privacy_gate(
                    actor=ACTOR,
                    sensor_id=sensor_id,
                    data_modality="video",
                    scrub=lambda: scrub_frame(frame),
                )
            except PrivacyGateError:
                # Fail-closed: drop the frame, publish nothing, keep going.
                log.warning("cvp.frame_dropped", sensor_id=sensor_id)
                continue

            # 2) detect + track on the SCRUBBED frame (faces already blurred).
            results = model.track(
                frame, persist=True, conf=conf, tracker="bytetrack.yaml",
                verbose=False,
            )
            detections: list[Detection] = []
            r = results[0]
            if r.boxes is not None and r.boxes.id is not None:
                ids = r.boxes.id.int().tolist()
                clss = r.boxes.cls.int().tolist()
                confs = r.boxes.conf.tolist()
                xyxy = r.boxes.xyxy.tolist()
                names = r.names
                for tid, c, cf, box in zip(ids, clss, confs, xyxy):
                    detections.append(
                        Detection(
                            track_id=int(tid),
                            cls=names.get(int(c), str(c)),
                            confidence=float(cf),
                            bbox_xyxy=(box[0], box[1], box[2], box[3]),
                        )
                    )

            # 3) build + publish envelope.
            envelope = FeatureEnvelope(
                modality=Modality.VIDEO,
                source_sensor_id=sensor_id,
                geo=geo or GeoRef(),
                capture_ts=capture_ts,
                privacy=privacy,
                features=VideoFeatures(frame_w=w, frame_h=h, detections=detections),
            )
            producer.publish(TOPIC, envelope)

            frames += 1
            if max_frames is not None and frames >= max_frames:
                log.info("cvp.max_frames_reached", frames=frames)
                break
    finally:
        cap.release()
        producer.flush()
        log.info("cvp.stopped", frames=frames)


def main() -> None:
    p = argparse.ArgumentParser(description="Kanatir CVP")
    p.add_argument("--source", required=True, help="RTSP/ONVIF URL or device index")
    p.add_argument("--sensor-id", required=True)
    p.add_argument("--weights", default="yolov8n.pt")
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lon", type=float, default=None)
    p.add_argument("--site-id", default=None)
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--bootstrap", default=None)
    a = p.parse_args()

    geo = GeoRef(lat=a.lat, lon=a.lon, site_id=a.site_id)
    run(
        source=a.source, sensor_id=a.sensor_id, weights=a.weights, conf=a.conf,
        geo=geo, max_frames=a.max_frames, bootstrap=a.bootstrap,
    )


if __name__ == "__main__":
    main()
