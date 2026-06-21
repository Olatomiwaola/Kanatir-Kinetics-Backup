"""
Redis-backed sliding correlation buffer for the live MSFE consumer.

Each consumed envelope is parked in a per-spatial-key Redis sorted set, scored
by ingest_ts (epoch seconds; see fusion.correlation_ts). On each tick we read
back the members within the
current window, hand them to fusion.correlate/fuse_window, and trim anything
older than the window so the buffer stays bounded. Redis is already in the
stack; this keeps MSFE state out of process memory so a restart doesn't lose the
in-flight window, and so a later sprint could run multiple MSFE workers.

Pure-logic note: correlation/fusion live in fusion.py and are tested without
Redis. This module is the thin stateful adapter.
"""

from __future__ import annotations

import time
from datetime import timedelta

import redis

from kanatir.core.msfe.fused import FusedObject
from kanatir.core.msfe.fusion import (
    _spatial_key,
    correlate,
    correlation_ts,
    fuse_window,
)
from kanatir.pipelines.common.envelope import FeatureEnvelope

_KEY_PREFIX = "msfe:win:"


class WindowBuffer:
    def __init__(
        self,
        client: redis.Redis,
        window: timedelta,
        ttl_pad_s: float = 5.0,
    ) -> None:
        self._r = client
        self._window = window
        self._ttl_s = int(window.total_seconds() + ttl_pad_s)

    def _zkey(self, env: FeatureEnvelope) -> str:
        return f"{_KEY_PREFIX}{_spatial_key(env)}"

    def add(self, env: FeatureEnvelope) -> None:
        zkey = self._zkey(env)
        score = correlation_ts(env).timestamp()
        # Store the serialized envelope as the member; score = capture epoch.
        self._r.zadd(zkey, {env.to_json(): score})
        self._r.expire(zkey, self._ttl_s)

    def _keys(self) -> list[str]:
        return [k.decode() if isinstance(k, bytes) else k for k in self._r.keys(f"{_KEY_PREFIX}*")]

    def harvest(self, now_ts: float | None = None) -> list[FusedObject]:
        """
        Pull mature windows (those whose anchor has fully aged past `window`),
        fuse them, and trim consumed members. A window is "mature" when no newer
        envelope can still arrive for it, approximated as: oldest member is more
        than one window behind `now`.
        """
        now = now_ts if now_ts is not None else time.time()
        cutoff = now - self._window.total_seconds()
        out: list[FusedObject] = []

        for zkey in self._keys():
            raw_members = self._r.zrangebyscore(zkey, min="-inf", max=cutoff)
            if not raw_members:
                continue
            envelopes = [
                FeatureEnvelope.from_json(m.decode() if isinstance(m, bytes) else m)
                for m in raw_members
            ]
            for group in correlate(envelopes, self._window):
                fused = fuse_window(group)
                if fused is not None:
                    out.append(fused)
            # Trim the matured members we just consumed.
            self._r.zremrangebyscore(zkey, min="-inf", max=cutoff)
        return out
