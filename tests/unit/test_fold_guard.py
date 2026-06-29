"""Unit tests — M9 fold leakage guard."""

from __future__ import annotations

import pytest

from kanatir.core.ade.fold_guard import (
    FIT_FOLDS,
    HELDOUT_FOLDS,
    HeldoutLeakageError,
    assert_no_heldout,
    fold_of,
)


def test_fold_of_parses_esc50_basename():
    assert fold_of("datasets/ESC-50/audio/1-100038-A-14.wav") == 1
    assert fold_of("4-102871-A-42.wav") == 4
    assert fold_of("/abs/path/5-1-A-0.wav") == 5


def test_fold_of_unparseable_returns_none():
    assert fold_of("weirdname.wav") is None
    assert fold_of("audio.wav") is None
    assert fold_of("") is None


def test_fit_and_heldout_fold_sets_disjoint():
    assert FIT_FOLDS.isdisjoint(HELDOUT_FOLDS)
    assert FIT_FOLDS == {1, 2, 3}
    assert HELDOUT_FOLDS == {4, 5}


def test_fit_folds_pass():
    # All fit-fold clips: no raise.
    assert_no_heldout(["1-a-A-0.wav", "2-b-A-1.wav", "3-c-A-2.wav"]) is None


def test_heldout_fold_raises():
    with pytest.raises(HeldoutLeakageError) as ei:
        assert_no_heldout(["1-a-A-0.wav", "4-b-A-1.wav"])
    assert ei.value.fold == 4


def test_heldout_fold_5_raises():
    with pytest.raises(HeldoutLeakageError):
        assert_no_heldout(["5-only-A-0.wav"])


def test_unparseable_fold_fatal_in_write_mode():
    with pytest.raises(HeldoutLeakageError) as ei:
        assert_no_heldout(["weird.wav"], can_write_artifact=True)
    assert ei.value.fold is None


def test_unparseable_fold_allowed_in_diagnostic_no_write():
    # Diagnostic that cannot write: unparseable fold is tolerated, but a held-out
    # fold would still raise (tested below).
    assert_no_heldout(
        ["weird.wav", "1-a-A-0.wav"],
        allow_diagnostic=True,
        can_write_artifact=False,
    )


def test_heldout_fatal_even_in_diagnostic():
    with pytest.raises(HeldoutLeakageError):
        assert_no_heldout(
            ["4-b-A-1.wav"], allow_diagnostic=True, can_write_artifact=False
        )


def test_diagnostic_with_write_is_structural_error():
    # allow_diagnostic=True together with can_write_artifact=True is forbidden.
    with pytest.raises(ValueError):
        assert_no_heldout(
            ["1-a-A-0.wav"], allow_diagnostic=True, can_write_artifact=True
        )
