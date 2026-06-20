"""
CVP privacy scrub — face blurring + license-plate hashing.

This runs INSIDE the privacy gate (common.privacy_gate.run_privacy_gate). It
mutates the frame in place (Gaussian-blurs face regions) and hashes detected
plate text, then reports a ScrubResult. It must complete fully or raise — the
gate is fail-closed.

Detection of faces/plates uses lightweight OpenCV cascades by default so the
pipeline has zero extra heavy deps for the PII step; a project may swap in a
stronger detector behind the same interface.
"""

from __future__ import annotations

import cv2
import numpy as np

from kanatir.core.pgc.audit import hash_payload
from kanatir.pipelines.common.privacy_gate import ScrubResult

# Haar cascade ships with opencv-python; frontal face is enough for the gate's
# fail-closed blur. Plate detection uses the russian-plate cascade as a stand-in
# locator (we only need bounding regions to hash, not OCR).
_FACE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_PLATE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_russian_plate_number.xml"
)


def _blur_region(frame: np.ndarray, x: int, y: int, w: int, h: int) -> None:
    roi = frame[y : y + h, x : x + w]
    if roi.size == 0:
        return
    k = max(15, (w // 3) | 1)  # odd kernel scaled to region
    frame[y : y + h, x : x + w] = cv2.GaussianBlur(roi, (k, k), 0)


def scrub_frame(frame: np.ndarray) -> ScrubResult:
    """
    Blur faces in place; hash plate regions. Returns ScrubResult.

    Raises on any failure so the gate drops the frame (fail-closed).
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = _FACE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
    for (x, y, w, h) in faces:
        _blur_region(frame, int(x), int(y), int(w), int(h))

    plates = _PLATE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    plate_hashes: list[str] = []
    for (x, y, w, h) in plates:
        region = frame[int(y) : int(y + h), int(x) : int(x + w)]
        # Hash the plate pixels (post nothing — we never store the raw region).
        plate_hashes.append(hash_payload(region.tobytes()))
        _blur_region(frame, int(x), int(y), int(w), int(h))  # blur after hashing

    actions: list[str] = []
    if len(faces):
        actions.append(f"face_blur:{len(faces)}")
    if plate_hashes:
        actions.append(f"plate_hash:{len(plate_hashes)}")

    pii_present = bool(len(faces) or plate_hashes)
    return ScrubResult(
        pii_present=pii_present,
        pii_scrubbed=pii_present,
        actions=actions,
        # Hash the scrubbed frame for the audit trail (post-blur, PII-safe).
        payload_to_hash=frame.tobytes(),
    )
