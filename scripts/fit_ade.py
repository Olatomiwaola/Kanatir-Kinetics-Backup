"""
fit_ade.py — M7 / TRL-3: offline fit of the ADE detector(s) on a NORMAL corpus
of REAL captured FusedObjects, producing a serialized, schema-pinned model
artifact (+ sidecar .meta.json) that kanatir.core.ade.__main__ loads at startup.

POLICY (load-bearing for the DND submission, do not relax):
  - Fit ONLY on normal/baseline FusedObjects captured through the real live
    pipeline (UDIH -> CVP/APP -> MSFE -> fused.objects). The corpus is a JSONL
    file where each line is one FusedObject as emitted on the bus.
  - NEVER construct FusedObjects synthetically in code for fitting. Each input
    line is re-validated into a real FusedObject; a malformed line is fatal, not
    skipped, so the fit set is exactly what production emitted.
  - NEVER mix evaluation threat/anomaly clips into the fit corpus. That is a
    capture-time responsibility (replay ambient negatives only); this script
    fits on whatever corpus it is given and records its identity in metadata so
    a reviewer can audit what went in.

The artifact pins the feature schema (names, dim, version). ADE startup asserts
exact equality and HARD-FAILS on any drift, so a fitted model can never be
silently scored against a featurizer it was not trained on.

Run (from repo root, with the [ade] extra installed):
    python3 -m pip install -e '.[ade]'
    python3 scripts/fit_ade.py \
        --corpus datasets/ade_fit_corpus/normal_corpus.jsonl \
        --out    models/ade/ade_isoforest_v1.joblib
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from kanatir.core.ade.detectors.isolation_forest import IsolationForestDetector
from kanatir.core.ade.detectors.masked_view import (
    M7_EXCLUDED_FEATURES,
    M7_FEATURE_VIEW,
    M7_KEPT_INDICES,
    M9_EXCLUDED_FEATURES,
    M9_FEATURE_VIEW,
    M9_KEPT_INDICES,
    MaskedFeatureView,
)

# Feature contract — the single source of truth for the artifact's schema pins.
from kanatir.core.ade.features import (
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    extract_features,
)
from kanatir.core.ade.fold_guard import assert_no_heldout
from kanatir.core.msfe.fused import FusedObject

# M7 view constants are imported but unused in the m9 fit path; retained so a
# reviewer can see both views and so a future M7-reproduction fit can re-select
# them. Reference them to keep linters quiet without using them for m9.
_M7_VIEW_REFS = (M7_FEATURE_VIEW, M7_KEPT_INDICES, M7_EXCLUDED_FEATURES)

# Default minimum fraction of fit-corpus objects that must carry acoustic_meta
# before an m9 artifact may be written. The m9 fit corpus is acoustic-led, so a
# near-1.0 fraction is expected; a low fraction means a stale 1.0.0 corpus was
# reused (acoustic features would be all-zero and the detector would learn
# nothing). Overridable via --min-acoustic-fraction for a documented
# mixed-modality corpus.
DEFAULT_MIN_ACOUSTIC_FRACTION = 0.8


def _load_corpus(corpus_path: Path) -> list[FusedObject]:
    """Read a JSONL corpus of FusedObjects. Each line MUST validate into a real
    FusedObject. A malformed line aborts the fit — we never silently drop or
    synthesize, so the fit set is exactly the captured normal corpus."""
    if not corpus_path.exists():
        raise FileNotFoundError(f"corpus not found: {corpus_path}")

    objs: list[FusedObject] = []
    with corpus_path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                objs.append(FusedObject.from_json(line))
            except Exception as exc:  # noqa: BLE001 - want the exact line in the message
                raise ValueError(
                    f"corpus line {lineno} did not validate as a FusedObject "
                    f"({corpus_path}): {exc}. Fit aborted — the fit corpus must be "
                    f"real captured FusedObjects, never partial or synthetic."
                ) from exc

    if not objs:
        raise ValueError(f"corpus {corpus_path} contained no FusedObjects")
    return objs


def _corpus_id(objs: list[FusedObject]) -> str:
    """Stable hash over the corpus identity. Uses each object's fused_id (its
    bus identity) sorted, so the same captured corpus always yields the same id
    regardless of read order."""
    ids = sorted(o.fused_id for o in objs)
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"


def _build_matrix(objs: list[FusedObject]):
    import numpy as np

    rows = [extract_features(o) for o in objs]
    X = np.vstack(rows)
    if X.shape[1] != FEATURE_DIM:
        raise ValueError(
            f"feature matrix width {X.shape[1]} != FEATURE_DIM {FEATURE_DIM}; "
            f"featurizer/contract mismatch"
        )
    return X


def _acoustic_fraction(objs: list[FusedObject]) -> float:
    """Fraction of corpus objects carrying acoustic_meta. Used to refuse fitting
    an m9 artifact from a stale 1.0.0 corpus whose acoustic features would all be
    zero."""
    if not objs:
        return 0.0
    n = sum(1 for o in objs if getattr(o, "acoustic_meta", None) is not None)
    return n / len(objs)


def _assert_acoustic_present(
    objs: list[FusedObject], *, min_fraction: float, can_write_artifact: bool
) -> float:
    """Hard guard (artifact-writing mode): abort if the fit corpus carries
    insufficient acoustic_meta. Two-tier: a corpus with ZERO acoustic_meta can
    NEVER write an m9 artifact (the representation would be unlearnable); below
    min_fraction also aborts unless a documented mixed-modality floor was set.
    Returns the measured fraction for metadata."""
    frac = _acoustic_fraction(objs)
    if not can_write_artifact:
        return frac  # diagnostic mode that cannot persist: skip the hard abort
    msg = (
        "fit corpus carries insufficient acoustic_meta; regenerate through the "
        "1.1.0 MSFE pipeline before fitting M9."
    )
    if frac <= 0.0:
        raise RuntimeError(
            f"{msg} (acoustic_meta present on 0.0% of {len(objs)} objects — "
            f"this looks like a stale 1.0.0 corpus; the M9 acoustic features "
            f"would be all-zero and the detector would learn nothing)."
        )
    if frac < min_fraction:
        raise RuntimeError(
            f"{msg} (acoustic_meta present on {frac:.1%} of {len(objs)} objects, "
            f"below the required {min_fraction:.0%}. If this is a deliberate "
            f"mixed-modality corpus, lower --min-acoustic-fraction with a "
            f"documented rationale; the chosen value is recorded in the artifact "
            f"metadata)."
        )
    return frac


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fit ADE detector(s) on a normal FusedObject corpus.")
    ap.add_argument("--corpus", required=True, type=Path,
                    help="JSONL of captured normal FusedObjects (one per line).")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output model artifact path (.joblib). Sidecar "
                         ".meta.json written beside it.")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="Optional capture manifest (.manifest.json) from "
                         "capture_normal_corpus.py. If given, its "
                         "source_corpus_id and source provenance are carried "
                         "into the artifact so the model links back to the "
                         "source clips, not just the objects.")
    ap.add_argument("--split", type=Path, default=None,
                    help="Optional esc50_split.json. If given (with "
                         "--manifest), the fit ABORTS if any source clip in the "
                         "manifest appears in the held-out "
                         "eval_negatives/eval_positives — a leakage firewall: "
                         "no clip used to fit may appear in the FPR/F1 eval "
                         "set.")
    ap.add_argument("--n-estimators", type=int, default=100)
    ap.add_argument("--contamination", default="auto",
                    help='IsolationForest contamination ("auto" or a float in (0,0.5]).')
    ap.add_argument("--random-state", type=int, default=42)
    ap.add_argument("--min-acoustic-fraction", type=float,
                    default=DEFAULT_MIN_ACOUSTIC_FRACTION,
                    help="Minimum fraction of fit objects that must carry acoustic_meta "
                         "to write an m9 artifact (default 0.8). Lower only for a documented "
                         "mixed-modality corpus; the value is recorded in metadata. A corpus "
                         "with ZERO acoustic_meta can never write an artifact regardless.")
    ap.add_argument("--diagnostic", action="store_true",
                    help="Diagnostic mode: relaxes the fold guard's unparseable-fold check and "
                         "the acoustic-presence hard abort. STRUCTURALLY CANNOT WRITE AN "
                         "ARTIFACT — held-out folds remain fatal even here. Use only for "
                         "inspecting a corpus, never to produce a model.")
    args = ap.parse_args(argv)

    import joblib

    contamination: float | str
    if isinstance(args.contamination, str) and args.contamination != "auto":
        contamination = float(args.contamination)
    else:
        contamination = args.contamination

    objs = _load_corpus(args.corpus)
    X = _build_matrix(objs)

    # Artifact-writing vs diagnostic. Diagnostic mode STRUCTURALLY cannot write a
    # model artifact (wired to fold_guard's can_write_artifact=False contract).
    can_write_artifact = not args.diagnostic

    # No-overwrite guard: never clobber the sealed M7 artifact, even by mistake.
    if can_write_artifact and args.out.name == "ade_isoforest_m7.joblib":
        raise RuntimeError(
            "refusing to write to ade_isoforest_m7.joblib — the sealed M7 artifact "
            "must not be overwritten. Choose a new path, e.g. "
            "models/ade/ade_isoforest_m9.joblib."
        )

    # Acoustic-presence guard (M9): hard-abort in artifact-writing mode if the fit
    # corpus carries insufficient acoustic_meta (i.e. a stale 1.0.0 corpus whose
    # appended acoustic features would be all-zero). See _assert_acoustic_present.
    acoustic_fraction = _assert_acoustic_present(
        objs,
        min_fraction=args.min_acoustic_fraction,
        can_write_artifact=can_write_artifact,
    )

    # Optional source provenance from the capture manifest. corpus_id is the
    # OBJECT identity (hash of fused_ids — proves the fit matrix); source_corpus_id
    # is the SOURCE identity (hash of source clips + capture config — proves where
    # the corpus came from). The report cites both; they are not interchangeable.
    source_provenance: dict = {}
    if args.manifest is not None:
        if not args.manifest.exists():
            raise FileNotFoundError(f"manifest not found: {args.manifest}")
        man = json.loads(args.manifest.read_text(encoding="utf-8"))
        source_provenance = {
            "source_corpus_id": man.get("source_corpus_id"),
            "manifest_path": str(args.manifest),
            "msfe_window_s": man.get("msfe_window_s"),
            "n_modalities_seen": man.get("n_modalities_seen"),
        }
        manifest_clip_paths = [
            c.get("path", "") for c in man.get("source_clips", [])
        ]

        # Fold guard (M9): independent second layer. Parses the ESC-50 fold from
        # each manifest source clip and fails closed on a held-out fold (always)
        # or an unparseable fold (artifact-writing mode). Composes with the
        # filename-set firewall below; this also catches unverifiable provenance.
        assert_no_heldout(
            manifest_clip_paths,
            allow_diagnostic=args.diagnostic,
            can_write_artifact=can_write_artifact,
        )

        # Leakage firewall: no clip used to fit may appear in the held-out eval set.
        if args.split is not None:
            if not args.split.exists():
                raise FileNotFoundError(f"split file not found: {args.split}")
            split = json.loads(args.split.read_text(encoding="utf-8"))
            eval_clips = {e["filename"] for e in split.get("eval_negatives", [])}
            eval_clips |= {e["filename"] for e in split.get("eval_positives", [])}
            # manifest source_clips record the wav paths fed into APP this capture
            fit_clip_names = set()
            for c in man.get("source_clips", []):
                fit_clip_names.add(Path(c.get("path", "")).name)
            leaked = sorted(fit_clip_names & eval_clips)
            if leaked:
                raise RuntimeError(
                    f"LEAKAGE: {len(leaked)} fit-corpus source clip(s) appear in the held-out "
                    f"eval set (e.g. {leaked[:5]}). Fit aborted — refit with a clean fit corpus. "
                    f"No clip used to fit ADE may appear in the FPR/F1 evaluation set."
                )
            source_provenance["split_policy"] = split.get("split_policy")
            source_provenance["fit_folds"] = split.get("fit_folds")
            source_provenance["eval_folds"] = split.get("eval_folds")
            source_provenance["leakage_check"] = "passed"
            source_provenance["fold_guard"] = "passed"
    elif can_write_artifact:
        # m9 artifact write REQUIRES manifest/split provenance sufficient to
        # enforce the fold guard. No blind artifact writes.
        raise RuntimeError(
            "m9 artifact write requires --manifest (and --split) so the fold guard "
            "and leakage firewall can verify no held-out clip entered the fit corpus. "
            "Refusing to write an artifact without source provenance. "
            "(Use --diagnostic to inspect a corpus without writing.)"
        )

    inner = IsolationForestDetector(
        n_estimators=args.n_estimators,
        contamination=contamination,
        random_state=args.random_state,
    )
    # M9 acoustic-event-aware feature view: the detector trains/scores on the
    # masked column view (n_modalities dropped, indices 8..15 acoustic features
    # kept). The wrapper is what we serialize, so the live score path uses the
    # identical view by construction — no train/serve skew.
    detector = MaskedFeatureView(
        inner,
        kept_indices=M9_KEPT_INDICES,
        excluded_features=M9_EXCLUDED_FEATURES,
        feature_view=M9_FEATURE_VIEW,
    )
    detector.fit(X)
    if not detector.is_ready:
        raise RuntimeError("detector did not become ready after fit")

    # Diagnostic mode produces NO artifact, by contract. Report and stop here.
    if not can_write_artifact:
        print(
            f"ade.fit DIAGNOSTIC (no artifact written) corpus={args.corpus} "
            f"n_samples={int(X.shape[0])} acoustic_fraction={acoustic_fraction:.3f} "
            f"feature_dim={FEATURE_DIM} feature_schema={FEATURE_SCHEMA_VERSION}"
        )
        return 0

    detector_config = {
        "isolation_forest": {
            "n_estimators": args.n_estimators,
            "contamination": contamination,
            "random_state": args.random_state,
        }
    }

    feature_view_meta = {
        "feature_view": M9_FEATURE_VIEW,
        "kept_indices": list(M9_KEPT_INDICES),
        "excluded_features": list(M9_EXCLUDED_FEATURES),
    }

    metadata = {
        "feature_names": list(FEATURE_NAMES),
        "feature_dim": FEATURE_DIM,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "detector_config": detector_config,
        "corpus_id": _corpus_id(objs),
        "corpus_path": str(args.corpus),
        "n_samples": int(X.shape[0]),
        "acoustic_fraction": round(acoustic_fraction, 4),
        "min_acoustic_fraction": args.min_acoustic_fraction,
        "trained_at": datetime.now(UTC).isoformat(),
    }
    metadata.update(source_provenance)
    metadata.update(feature_view_meta)

    # The joblib payload mirrors the metadata but additionally carries the live
    # fitted detector object. feature_names is a tuple here (exact-eq on load).
    payload = {
        "fitted_detectors": {"isolation_forest": detector},
        "feature_names": tuple(FEATURE_NAMES),
        "feature_dim": FEATURE_DIM,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "detector_config": detector_config,
        "corpus_id": metadata["corpus_id"],
        "corpus_path": metadata["corpus_path"],
        "n_samples": metadata["n_samples"],
        "acoustic_fraction": metadata["acoustic_fraction"],
        "trained_at": metadata["trained_at"],
        "source_corpus_id": source_provenance.get("source_corpus_id"),
        "feature_view": M9_FEATURE_VIEW,
        "kept_indices": list(M9_KEPT_INDICES),
        "excluded_features": list(M9_EXCLUDED_FEATURES),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, args.out)

    meta_path = args.out.with_suffix(args.out.suffix + ".meta.json")
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print(
        f"ade.fit complete model={args.out} meta={meta_path} "
        f"n_samples={metadata['n_samples']} feature_dim={FEATURE_DIM} "
        f"feature_schema={FEATURE_SCHEMA_VERSION} feature_view={M9_FEATURE_VIEW} "
        f"acoustic_fraction={metadata['acoustic_fraction']} corpus_id={metadata['corpus_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
