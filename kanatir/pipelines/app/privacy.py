"""
APP privacy scrub — acoustic PII handling.

Audio has no faces/plates to blur, but it can capture speech, which is PII. The
gate's job here is detection + decision logging, not blurring: we never retain
or forward raw audio. The envelope carries only YAMNet class scores and an MFCC
vector — derived features from which speech content cannot be reconstructed.

If speech-class labels dominate the window, we mark pii_present=True and record
that the raw waveform was discarded after feature extraction (suppression).
"""

from __future__ import annotations

import numpy as np

from kanatir.pipelines.common.privacy_gate import ScrubResult

# YAMNet AudioSet labels that indicate human speech / voice.
_SPEECH_LABELS = {
    "Speech", "Conversation", "Narration, monologue", "Whispering",
    "Shout", "Child speech, kid speaking", "Speech synthesizer",
}
_SPEECH_SCORE_THRESHOLD = 0.30


def scrub_window(
    waveform: np.ndarray,
    yamnet_top: list[tuple[str, float]],
) -> ScrubResult:
    """
    Decide + record acoustic PII handling for one window.

    Raises on failure (fail-closed). The raw `waveform` is NOT placed in the
    envelope by the caller; only derived features are. Here we just hash the
    raw window for the audit trail and report the decision.
    """
    speech_score = max(
        (score for label, score in yamnet_top if label in _SPEECH_LABELS),
        default=0.0,
    )
    speech_present = speech_score >= _SPEECH_SCORE_THRESHOLD

    actions: list[str] = []
    if speech_present:
        # We do not transcribe or store audio; raw window is dropped after MFCC.
        actions.append(f"speech_suppressed:{speech_score:.2f}")

    return ScrubResult(
        pii_present=speech_present,
        pii_scrubbed=speech_present,  # suppression == scrub for audio
        actions=actions,
        payload_to_hash=np.ascontiguousarray(waveform).tobytes(),
    )
