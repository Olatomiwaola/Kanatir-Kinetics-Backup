"""
CSAT Triage Engine — the stateful dedup/suppression logic behind `alerts.triaged`.

Two distinct mechanisms, deliberately kept separate (they mean different things):

  1. IDEMPOTENCY (drop). A repeated `fused_id` is a *redelivery* — Kafka
     at-least-once, a consumer restart, a replay. Nothing happened twice in the
     world; the bus delivered the same anomaly twice. Dropped silently, counted
     only as a consumer-side `dropped_duplicates` metric. NEVER inflates
     `suppressed_count` — that would fabricate a second observation.

  2. GROUPING (suppress). Two *distinct* anomalies at the same place within a
     sliding time window are one situational event — a drone loitering for 30s
     is one alert with `suppressed_count=29`, not 30 alerts. This is the real
     operator value-add over raw `anomalies.raw`.

Window policy: SLIDING + MAX-AGE CAP.
  - Sliding: each new member resets the group's idle timer, so one continuous
    event stays one alert.
  - Max-age cap: a group that keeps receiving members still force-emits every
    CSAT_MAX_AGE_S, so a long-running threat re-surfaces periodically instead of
    buffering forever behind a never-closing window.

Geo key is tiered, matching GeoRef's own design (lat/lon OR site_id):
  - site_id present       -> exact-match key (the path M5 actually runs on)
  - lat/lon present only   -> proximity bucket (calibration-pending radius)
  - neither                -> single ungeolocated bucket
See `geo_group_key`.

ML-free: pure stdlib + the contract. No sklearn/torch. Imports clean on core.
"""

from __future__ import annotations

import math
import os
from collections.abc import Hashable
from dataclasses import dataclass, field
from uuid import uuid4

from kanatir.core.ade.anomaly import AnomalyRecord
from kanatir.core.csat.alert import Severity, TriagedAlert, assign_severity
from kanatir.pipelines.common.envelope import GeoRef

# --- Tunables (env-overridable; proximity radius is calibration-pending) -------

# Idle gap that closes a sliding group: no new member for this long -> flush.
CSAT_DEDUP_WINDOW_S = float(os.getenv("CSAT_DEDUP_WINDOW_S", "60"))

# Hard cap on group age: force-emit even if members keep arriving, so a
# long-running event re-emits periodically rather than buffering indefinitely.
CSAT_MAX_AGE_S = float(os.getenv("CSAT_MAX_AGE_S", "300"))

# Proximity radius (metres) for lat/lon grouping when no site_id is available.
# CALIBRATION-PENDING: like ADE_Z_THRESHOLD, this constant has no real-media
# justification yet. The site_id path (M5's actual path) does not use it.
CSAT_DEDUP_RADIUS_M = float(os.getenv("CSAT_DEDUP_RADIUS_M", "100"))

# M5.1 (D4): TTL that bounds the idempotency seen-set. A genuine Kafka redelivery
# recurs within seconds; a fused_id older than the longest open incident cannot
# legitimately reappear as a LIVE duplicate, so it is safe to evict. Effective TTL
# = CSAT_MAX_AGE_S + margin, kept comfortably longer than one max-age window. A
# replay arriving AFTER eviction is treated as a NEW observation (documented,
# acceptable). Deterministic: eviction uses the injected `now`, never a clock.
CSAT_SEEN_TTL_MARGIN_S = float(os.getenv("CSAT_SEEN_TTL_MARGIN_S", "300"))
CSAT_SEEN_TTL_S = CSAT_MAX_AGE_S + CSAT_SEEN_TTL_MARGIN_S

_EARTH_R_M = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))


def geo_group_key(geo: GeoRef) -> Hashable:
    """Tiered, hashable grouping key for a GeoRef.

    Tier 1 (site_id present): exact-match on the logical zone label. This is the
        path that runs at the M5 gate (synthetic/replayed media has site_id, not
        a GPS fix). No calibration constant involved.
    Tier 2 (lat/lon present, no site_id): snap to a coarse grid cell whose size
        is ~CSAT_DEDUP_RADIUS_M, so nearby fixes share a key. This is a hashable
        approximation of proximity grouping; exact within-radius membership is
        re-checked in `_same_group` for correctness at cell boundaries.
    Tier 3 (neither): a single ungeolocated bucket. Honest fallback.
    """
    if geo.site_id is not None:
        return ("site", geo.site_id)
    if geo.lat is not None and geo.lon is not None:
        # ~metres-per-degree latitude is ~111_320; longitude scaled by cos(lat).
        cell = CSAT_DEDUP_RADIUS_M / 111_320.0
        return ("cell", round(geo.lat / cell), round(geo.lon / cell))
    return ("ungeo",)


def _same_group(a: GeoRef, b: GeoRef) -> bool:
    """True if two GeoRefs belong in the same spatial group.

    site_id path is exact-match. lat/lon path re-checks true haversine distance
    (the grid key in `geo_group_key` is only a hashable pre-bucket; this is the
    authoritative membership test so boundary cases group correctly).
    """
    if a.site_id is not None or b.site_id is not None:
        return a.site_id == b.site_id
    if None not in (a.lat, a.lon, b.lat, b.lon):
        return _haversine_m(a.lat, a.lon, b.lat, b.lon) <= CSAT_DEDUP_RADIUS_M
    # both ungeolocated -> same bucket
    return (a.lat, a.lon, a.site_id) == (b.lat, b.lon, b.site_id) == (None, None, None)


def _severity_rank(s: Severity) -> int:
    return {Severity.INFO: 0, Severity.WATCH: 1, Severity.ALERT: 2}[s]


@dataclass
class _OpenGroup:
    """A group of distinct anomalies accumulating toward one TriagedAlert."""

    geo: GeoRef
    members: list[AnomalyRecord] = field(default_factory=list)
    opened_at: float = 0.0          # wall-clock (monotonic-ish) when group opened
    last_member_at: float = 0.0     # wall-clock of most recent add (sliding timer)
    # M5.1 (D5): geo-temporal incident identity. Assigned when the group opens
    # (first member = incident begins); PRESERVED across each max-age flush-and-
    # reset so a long-running event re-emits under a stable id, with
    # incident_sequence incrementing on each reset. A group that goes idle and
    # closes ends the incident; a later member in the same geo bucket opens a NEW
    # group -> new incident_id. Geo-temporal continuity, NOT physical-object.
    incident_id: str = field(default_factory=lambda: str(uuid4()))
    incident_sequence: int = 0

    def add(self, rec: AnomalyRecord, now: float) -> None:
        self.members.append(rec)
        self.last_member_at = now

    def is_idle(self, now: float) -> bool:
        return (now - self.last_member_at) >= CSAT_DEDUP_WINDOW_S

    def is_aged(self, now: float) -> bool:
        return (now - self.opened_at) >= CSAT_MAX_AGE_S

    def trigger(self) -> AnomalyRecord:
        """The member whose snapshot drives the alert: highest severity, then
        most recent window_end as a tiebreak."""
        return max(
            self.members,
            key=lambda r: (
                _severity_rank(
                    assign_severity(
                        is_anomaly=r.is_anomaly,
                        anomaly_score=r.anomaly_score,
                        baseline_state=r.baseline_state,
                    )
                ),
                r.window_end,
            ),
        )

    def to_alert(self) -> TriagedAlert:
        return TriagedAlert.from_anomalies(
            self.members,
            trigger=self.trigger(),
            incident_id=self.incident_id,
            incident_sequence=self.incident_sequence,
        )


class TriageBuffer:
    """Stateful triage over a stream of AnomalyRecords.

    Usage (single-threaded consumer loop):
        buf = TriageBuffer()
        for rec in anomalies:
            buf.offer(rec, now=time.monotonic())
            for alert in buf.flush_ready(now=time.monotonic()):
                produce(alert)
        for alert in buf.drain(now=time.monotonic()):   # at shutdown
            produce(alert)

    `now` is injected (not read internally) so tests are deterministic.
    """

    def __init__(self) -> None:
        # D4: map fused_id -> last-seen `now` so stale ids can be TTL-evicted and
        # the idempotency set stays bounded (was an unbounded set[str] pre-M5.1).
        self._seen_fused_ids: dict[str, float] = {}
        self._groups: dict[Hashable, list[_OpenGroup]] = {}
        self.dropped_duplicates: int = 0  # consumer metric; NOT a suppressed_count

    # -- ingest -----------------------------------------------------------------

    def _evict_stale_seen(self, now: float) -> None:
        """Drop seen fused_ids older than the TTL so the idempotency set stays
        bounded (D4). Deterministic in the injected `now`."""
        stale = [fid for fid, ts in self._seen_fused_ids.items()
                 if (now - ts) >= CSAT_SEEN_TTL_S]
        for fid in stale:
            del self._seen_fused_ids[fid]

    def offer(self, rec: AnomalyRecord, *, now: float) -> None:
        """Admit one anomaly: drop if a redelivery, else place in a group."""
        self._evict_stale_seen(now)
        if rec.fused_id in self._seen_fused_ids:
            self.dropped_duplicates += 1
            return
        self._seen_fused_ids[rec.fused_id] = now

        key = geo_group_key(rec.geo)
        bucket = self._groups.setdefault(key, [])
        for grp in bucket:
            if not grp.is_idle(now) and _same_group(grp.geo, rec.geo):
                grp.add(rec, now)
                return
        grp = _OpenGroup(geo=rec.geo, opened_at=now, last_member_at=now)
        grp.add(rec, now)
        bucket.append(grp)

    # -- emit -------------------------------------------------------------------

    def flush_ready(self, *, now: float) -> list[TriagedAlert]:
        """Emit alerts for groups that have gone idle OR hit the max-age cap.

        Idle groups are removed. Aged-but-still-active groups emit and RESET
        (re-open with opened_at=now, members cleared) so a long-running event
        re-emits periodically without losing its grouping identity.
        """
        out: list[TriagedAlert] = []
        for key, bucket in list(self._groups.items()):
            keep: list[_OpenGroup] = []
            for grp in bucket:
                if grp.is_idle(now):
                    out.append(grp.to_alert())
                elif grp.is_aged(now):
                    out.append(grp.to_alert())
                    grp.members.clear()
                    grp.opened_at = now
                    grp.last_member_at = now
                    grp.incident_sequence += 1  # D5: next re-emit is the next sequence
                    keep.append(grp)
                else:
                    keep.append(grp)
            if keep:
                self._groups[key] = keep
            else:
                del self._groups[key]
        return out

    def drain(self, *, now: float) -> list[TriagedAlert]:
        """Emit every open group regardless of timers (shutdown / end-of-replay)."""
        out: list[TriagedAlert] = []
        for bucket in self._groups.values():
            out.extend(grp.to_alert() for grp in bucket if grp.members)
        self._groups.clear()
        return out
