"""
fold_guard.py — M9 / TRL 3->4: a hard, artifact-blocking leakage guard against
ESC-50 held-out folds entering an ADE fit corpus.

Policy (load-bearing; do not relax):
  - ESC-50 is split disjointly by fold: FIT = {1, 2, 3}, HELD-OUT = {4, 5}.
  - No clip from a held-out fold may ever enter a fit corpus that produces a
    model artifact. The M7 recall claim and the M9 separability claim both
    depend on the eval set being unseen at fit time.
  - This guard is INDEPENDENT of, and composes with, the existing manifest/
    split filename-set firewall in fit_ade.py. That firewall checks membership
    against the split's explicit eval lists; this guard parses the *fold number*
    out of each source filename. Two angles, defense in depth: this one also
    catches files whose fold cannot be parsed at all.

Modes:
  - Artifact-writing (default): the guard is MANDATORY and fails closed. A
    held-out fold, OR an unparseable fold, raises and aborts the fit before any
    artifact is written.
  - Diagnostic: may relax (allow_diagnostic=True) ONLY when the caller also
    proves it cannot write an artifact (can_write_artifact=False). Passing
    allow_diagnostic=True together with can_write_artifact=True is itself a hard
    error: a diagnostic that could persist an artifact is the exact footgun this
    guard exists to forbid.

ESC-50 filename convention: ``{FOLD}-{clipid}-{take}-{target}.wav`` — the fold
is the leading integer before the first '-'. Only the basename is parsed, so a
full path or a bare filename both work.
"""

from __future__ import annotations

import re
from pathlib import Path

FIT_FOLDS: frozenset[int] = frozenset({1, 2, 3})
HELDOUT_FOLDS: frozenset[int] = frozenset({4, 5})

# Leading integer before the first '-' in an ESC-50 basename.
_FOLD_RE = re.compile(r"^(\d+)-")


class HeldoutLeakageError(Exception):
    """Raised when a fit corpus source path resolves to a held-out fold, or to
    an unparseable fold in artifact-writing mode. Names the offending file and
    the fold so a reviewer can audit exactly what tripped the guard."""

    def __init__(self, filename: str, fold: int | None, reason: str) -> None:
        self.filename = filename
        self.fold = fold
        self.reason = reason
        super().__init__(
            f"LEAKAGE GUARD: {reason} (file={filename!r}, fold={fold!r}). "
            f"Fit aborted — no held-out (folds {sorted(HELDOUT_FOLDS)}) or "
            f"unverifiable clip may enter a fit corpus that writes an artifact."
        )


def fold_of(path: str) -> int | None:
    """Parse the ESC-50 fold from a path or filename. Returns the fold integer,
    or None if the basename does not match the ESC-50 convention."""
    name = Path(path).name
    m = _FOLD_RE.match(name)
    if m is None:
        return None
    return int(m.group(1))


def assert_no_heldout(
    paths: list[str],
    *,
    allow_diagnostic: bool = False,
    can_write_artifact: bool = True,
) -> None:
    """Fail closed if any path resolves to a held-out fold (always), or to an
    unparseable fold (artifact-writing mode only).

    Raises:
      ValueError: if allow_diagnostic and can_write_artifact are both True — a
                  diagnostic run that could persist an artifact is forbidden.
      HeldoutLeakageError: on the first held-out or (in writing mode)
                  unparseable fold encountered.
    """
    if allow_diagnostic and can_write_artifact:
        raise ValueError(
            "fold_guard: allow_diagnostic=True requires can_write_artifact=False. "
            "A diagnostic run that can write a model artifact is forbidden."
        )

    for p in paths:
        fold = fold_of(p)
        if fold in HELDOUT_FOLDS:
            # A held-out fold is ALWAYS fatal, diagnostic or not.
            raise HeldoutLeakageError(p, fold, "source clip is in a held-out fold")
        if fold is None and not allow_diagnostic:
            # Unparseable provenance is fatal in artifact-writing mode: a fit set
            # must be over files whose fold can be verified.
            raise HeldoutLeakageError(
                p, None, "source clip fold could not be parsed (unverifiable provenance)"
            )
