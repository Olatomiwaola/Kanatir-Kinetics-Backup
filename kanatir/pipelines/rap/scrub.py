"""
RAP — RF Acquisition Pipeline: privacy scrub.

RF identifiers (MAC, BLE address, etc.) are civilian-derived sensitive data.
This module performs identifier ANONYMIZATION before any feature is derived:

  - HMAC-SHA256 each raw identifier under a rotating secret salt. The salt
    rotates on a configurable interval (default 15 min, env KAN_RF_SALT_ROTATE_S)
    so a given device's hash cannot be tracked across rotation epochs.
  - Retain ONLY the per-window hashed-identifier set needed to compute aggregate
    features (counts, rates). Raw identifiers are never returned, logged, or
    persisted.
  - No payload is ever captured or processed here.

NOTE on the two hashes in this system, kept deliberately distinct:
  - HMAC-SHA256-with-rotating-salt (here) = identifier ANONYMIZATION. Keyed and
    rotated so hashes are non-linkable across epochs and non-reversible without
    the salt.
  - SHA-256 hash_payload (in PGC) = audit-trail INTEGRITY hash over a post-scrub
    payload. Unkeyed; its job is tamper-evidence, not anonymization.

The scrub is designed to be handed to common.privacy_gate.run_privacy_gate as a
zero-arg closure (see rap/features.py), so the audit event is written about the
exact operation that happened, fail-closed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass


def _salt_rotate_seconds() -> int:
    raw = os.environ.get("KAN_RF_SALT_ROTATE_S")
    if raw is None:
        return 15 * 60  # 15 minutes default
    try:
        return max(1, int(raw))
    except ValueError:
        return 15 * 60


@dataclass
class _SaltEpoch:
    epoch: int
    salt: bytes


class RotatingSalt:
    """
    Holds the current HMAC salt and rotates it every `interval_s`. The salt is a
    process-held secret; only the epoch number is ever exposed (for audit
    metadata). Rotation is time-based and lazy (checked on access).
    """

    def __init__(self, interval_s: int | None = None,
                 clock: callable = time.time) -> None:
        self._interval = interval_s if interval_s is not None else _salt_rotate_seconds()
        self._clock = clock
        self._current = self._new_epoch(self._epoch_for(self._clock()))

    def _epoch_for(self, t: float) -> int:
        return int(t // self._interval)

    def _new_epoch(self, epoch: int) -> _SaltEpoch:
        return _SaltEpoch(epoch=epoch, salt=secrets.token_bytes(32))

    def current(self) -> _SaltEpoch:
        epoch = self._epoch_for(self._clock())
        if epoch != self._current.epoch:
            self._current = self._new_epoch(epoch)
        return self._current

    @property
    def interval_s(self) -> int:
        return self._interval


def hash_identifier(raw_id: str, salt: bytes) -> str:
    """HMAC-SHA256 of a raw identifier under the rotating salt. Non-reversible."""
    return hmac.new(salt, raw_id.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class RawRFObservation:
    """
    Locally captured RF metadata for one emitter sighting. NO PAYLOAD.

    `raw_id` is the only sensitive field; it never leaves this module un-hashed.
    """

    raw_id: str          # MAC / BLE addr — sensitive, hashed immediately
    rssi: float          # dBm
    is_probe: bool = False
    is_burst: bool = False
    first_seen: bool = False   # first time this id seen in the rolling known-set
    known: bool = True         # in the rolling known-set


@dataclass
class ScrubbedRFWindow:
    """Post-scrub, minimized RF window. Contains NO raw identifiers."""

    hashed_ids: list[str]
    rssis: list[float]
    n_probe: int
    n_burst: int
    n_new: int
    n_unknown: int
    salt_epoch: int
    n_ids_hashed: int


def scrub_rf_window(
    observations: list[RawRFObservation],
    salt: RotatingSalt,
) -> ScrubbedRFWindow:
    """
    Anonymize and minimize a window of raw RF observations.

    Raw identifiers are HMAC-hashed and discarded; only hashes + aggregate-ready
    quantities are returned. Raises on no salt available (caller fail-closes).
    """
    epoch = salt.current()
    hashed: list[str] = []
    rssis: list[float] = []
    n_probe = n_burst = n_new = n_unknown = 0

    for obs in observations:
        h = hash_identifier(obs.raw_id, epoch.salt)
        hashed.append(h)
        rssis.append(obs.rssi)
        if obs.is_probe:
            n_probe += 1
        if obs.is_burst:
            n_burst += 1
        if obs.first_seen:
            n_new += 1
        if not obs.known:
            n_unknown += 1

    return ScrubbedRFWindow(
        hashed_ids=hashed,
        rssis=rssis,
        n_probe=n_probe,
        n_burst=n_burst,
        n_new=n_new,
        n_unknown=n_unknown,
        salt_epoch=epoch.epoch,
        n_ids_hashed=len(hashed),
    )
