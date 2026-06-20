"""
Acoustic Pipeline (APP)
Audio source -> windowing -> YAMNet classification + MFCC -> privacy gate
(speech-presence decision, fail-closed, audited) -> features.acoustic envelope.

Run (host-native arm64):
    python3 -m kanatir.pipelines.app --source mic --sensor-id mic-01
    python3 -m kanatir.pipelines.app --source path/to/audio.wav --sensor-id file-01

YAMNet expects 16 kHz mono float32 in [-1, 1]. We window the stream, run YAMNet
for class scores and librosa for MFCCs, gate, then publish.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import numpy as np
import structlog

from kanatir.pipelines.app.privacy import scrub_window
from kanatir.pipelines.common.envelope import (
    AcousticFeatures,
    FeatureEnvelope,
    GeoRef,
    Modality,
)
from kanatir.pipelines.common.privacy_gate import (
    PrivacyGateError,
    run_privacy_gate,
)
from kanatir.pipelines.common.producer import EnvelopeProducer

log = structlog.get_logger("pipelines.app")

TOPIC = "features.acoustic"
ACTOR = "app"
TARGET_SR = 16_000  # YAMNet requirement
N_MFCC = 40


def _load_yamnet():
    # Lazy import so CI doesn't need TensorFlow.
    import tensorflow_hub as hub

    return hub.load("https://tfhub.dev/google/yamnet/1")


def _load_class_map(model) -> list[str]:
    import csv

    path = model.class_map_path().numpy().decode("utf-8")
    names: list[str] = []
    with open(path) as f:
        for row in csv.DictReader(f):
            names.append(row["display_name"])
    return names


def _windows(source: str, window_s: float):
    """Yield (waveform_float32_16k_mono) windows from mic or file."""
    import librosa

    if source == "mic":
        import sounddevice as sd

        block = int(TARGET_SR * window_s)
        with sd.InputStream(samplerate=TARGET_SR, channels=1, dtype="float32") as st:
            while True:
                data, _ = st.read(block)
                yield data.reshape(-1).astype(np.float32)
    else:
        y, _ = librosa.load(source, sr=TARGET_SR, mono=True)
        block = int(TARGET_SR * window_s)
        for start in range(0, len(y) - block + 1, block):
            yield y[start : start + block].astype(np.float32)


def run(
    source: str,
    sensor_id: str,
    window_s: float = 0.96,  # YAMNet's native patch length
    geo: GeoRef | None = None,
    max_windows: int | None = None,
    bootstrap: str | None = None,
) -> None:
    import librosa

    model = _load_yamnet()
    class_names = _load_class_map(model)
    producer = EnvelopeProducer(bootstrap=bootstrap)

    log.info("app.started", source=source, sensor_id=sensor_id)
    count = 0
    try:
        for wave in _windows(source, window_s):
            capture_ts = datetime.now(UTC)

            # YAMNet scores.
            scores, _embeddings, _spec = model(wave)
            mean_scores = np.mean(scores.numpy(), axis=0)
            top_idx = np.argsort(mean_scores)[::-1][:5]
            yamnet_top = [(class_names[i], float(mean_scores[i])) for i in top_idx]

            # MFCC mean vector.
            mfcc = librosa.feature.mfcc(y=wave, sr=TARGET_SR, n_mfcc=N_MFCC)
            mfcc_mean = mfcc.mean(axis=1).astype(float).tolist()

            # PRIVACY GATE — decide + audit before publishing.
            try:
                privacy = run_privacy_gate(
                    actor=ACTOR,
                    sensor_id=sensor_id,
                    data_modality="acoustic",
                    scrub=lambda: scrub_window(wave, yamnet_top),
                )
            except PrivacyGateError:
                log.warning("app.window_dropped", sensor_id=sensor_id)
                continue

            envelope = FeatureEnvelope(
                modality=Modality.ACOUSTIC,
                source_sensor_id=sensor_id,
                geo=geo or GeoRef(),
                capture_ts=capture_ts,
                privacy=privacy,
                features=AcousticFeatures(
                    sample_rate=TARGET_SR,
                    window_s=window_s,
                    yamnet_top=yamnet_top,
                    mfcc_mean=mfcc_mean,
                ),
            )
            producer.publish(TOPIC, envelope)

            count += 1
            if max_windows is not None and count >= max_windows:
                break
    finally:
        producer.flush()
        log.info("app.stopped", windows=count)


def main() -> None:
    p = argparse.ArgumentParser(description="Kanatir APP")
    p.add_argument("--source", required=True, help="'mic' or path to audio file")
    p.add_argument("--sensor-id", required=True)
    p.add_argument("--window-s", type=float, default=0.96)
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lon", type=float, default=None)
    p.add_argument("--site-id", default=None)
    p.add_argument("--max-windows", type=int, default=None)
    p.add_argument("--bootstrap", default=None)
    a = p.parse_args()

    geo = GeoRef(lat=a.lat, lon=a.lon, site_id=a.site_id)
    run(
        source=a.source, sensor_id=a.sensor_id, window_s=a.window_s, geo=geo,
        max_windows=a.max_windows, bootstrap=a.bootstrap,
    )


if __name__ == "__main__":
    main()
