"""
Adaptive baseline — the scoring / thresholding layer for ADE.

Ported (re-implemented clean, NOT imported) from the AdaptiveBaseline logic in
the parallel research-spike prototype, which is excluded from gate evidence per
protocol. The idea is topology-independent and applies to any scalar detector
score: maintain a rolling estimate of "normal", express the current score as a
z-score against it, and only fold CONFIRMED-NORMAL readings back into the
baseline so a sustained anomaly cannot drift the baseline into hiding itself.

Two design points that matter for TRL 6:
  - WARMUP FALLBACK. Until the window holds enough samples, the baseline is not
    confident and reports state=WARMUP. The caller records that state on the
    AnomalyRecord so a "no anomaly" during warmup is distinguishable from a
    confident "no anomaly". We do not fabricate confidence we don't have.
  - CONFIRMED-NORMAL-ONLY FOLDBACK. A reading that the baseline itself flags as
    anomalous is NOT folded back into the rolling statistics. This is what stops
    a slow, sustained anomaly from gradually being absorbed as the new normal.

ML-free. numpy imported lazily so this module imports without the [ade] extra.
"""

from __future__ import annotations

from collections import deque

from kanatir.core.ade.anomaly import BaselineState


class AdaptiveBaseline:
    """Rolling z-score baseline over a scalar score, with warmup + safe foldback.

    Parameters
    ----------
    window : int
        Max number of confirmed-normal samples retained for the rolling stats.
    warmup : int
        Minimum samples before the baseline reports ACTIVE. Below this it is in
        WARMUP and never flags (it has no basis to).
    z_threshold : float
        |z| at or above which a reading is considered anomalous once ACTIVE.
        NOTE (calibration-pending): at TRL 6 this should be expressed as a target
        false-alarm rate against the learned baseline once real-media score
        distributions exist. The raw sigma here is a placeholder default, not a
        calibrated operating point — do not present it as one.
    """

    def __init__(self, window: int = 200, warmup: int = 30, z_threshold: float = 3.0) -> None:
        if warmup < 2:
            raise ValueError("warmup must be >= 2 (need >=2 samples for a stddev)")
        if window < warmup:
            raise ValueError("window must be >= warmup")
        self.window = window
        self.warmup = warmup
        self.z_threshold = z_threshold
        self._samples: deque[float] = deque(maxlen=window)

    @property
    def state(self) -> BaselineState:
        return BaselineState.ACTIVE if len(self._samples) >= self.warmup else BaselineState.WARMUP

    def _mean_std(self) -> tuple[float, float]:
        import numpy as np

        arr = np.fromiter(self._samples, dtype=np.float64, count=len(self._samples))
        return float(arr.mean()), float(arr.std(ddof=0))

    def score(self, value: float) -> tuple[float, bool, BaselineState]:
        """Score one scalar reading.

        Returns (anomaly_score in [0,1], is_anomaly, baseline_state).

        Side effect: folds `value` into the rolling baseline IFF it is not
        flagged anomalous (and always during warmup, to build the baseline).
        anomaly_score is a bounded squashing of |z| so downstream consumers get
        a [0,1] value; is_anomaly is the thresholded decision.
        """
        state = self.state

        if state is BaselineState.WARMUP:
            # No basis to flag. Record the sample to build the baseline, report
            # a neutral score, never anomalous.
            self._samples.append(float(value))
            return 0.0, False, state

        mean, std = self._mean_std()
        if std == 0.0:
            # Degenerate baseline (all identical). Any exact match is normal;
            # any deviation is maximally surprising.
            z = 0.0 if value == mean else float("inf")
        else:
            z = abs(value - mean) / std

        is_anomaly = z >= self.z_threshold
        # Squash |z| into [0,1): z_threshold maps to ~0.5, grows toward 1.
        anomaly_score = 1.0 - 1.0 / (1.0 + (z / max(self.z_threshold, 1e-9)))
        anomaly_score = max(0.0, min(1.0, anomaly_score))

        # Confirmed-normal-only foldback: do NOT absorb a flagged reading.
        if not is_anomaly:
            self._samples.append(float(value))

        return anomaly_score, is_anomaly, state
