"""
Block D — ресэмплинг и валидация audio_meta.
"""

import numpy as np
import pytest

from audio_utils import (
    resample_audio, SAMPLE_RATE,
    pick_client_sample_rate, validate_audio_meta,
)


class vox_main:  # совместимость с именами в тестах ниже
    _pick_client_sample_rate = staticmethod(pick_client_sample_rate)
    _validate_audio_meta = staticmethod(validate_audio_meta)


@pytest.mark.parametrize("orig_sr", [8000, 16000, 44100, 48000])
def test_resample_to_16k_length(orig_sr):
    # 0.5с синуса 440 Гц
    dur = 0.5
    t = np.linspace(0, dur, int(orig_sr * dur), endpoint=False, dtype=np.float32)
    audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    out = resample_audio(audio, orig_sr, SAMPLE_RATE)
    assert out.dtype == np.float32
    if orig_sr == SAMPLE_RATE:
        assert len(out) == len(audio)
    else:
        expected = int(dur * SAMPLE_RATE)
        assert abs(len(out) - expected) <= 2
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out))) <= 1.0 + 1e-6


def test_resample_noop_same_rate_returns_same():
    audio = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    out = resample_audio(audio, 16000, 16000)
    assert np.array_equal(out, audio)


# ── _pick_client_sample_rate ──────────────────────────────────────────────────

def test_pick_sample_rate_prefers_context():
    meta = {"context_sample_rate": 48000, "track_sample_rate": 44100}
    assert vox_main._pick_client_sample_rate(meta) == 48000


def test_pick_sample_rate_falls_back_to_default_on_garbage():
    assert vox_main._pick_client_sample_rate({"context_sample_rate": "oops"}) == 16000
    assert vox_main._pick_client_sample_rate(None) == 16000
    # вне диапазона → дефолт
    assert vox_main._pick_client_sample_rate({"sample_rate": 999999}) == 16000


# ── _validate_audio_meta ──────────────────────────────────────────────────────

def test_validate_audio_meta_ok():
    ok, _ = vox_main._validate_audio_meta(
        {"context_sample_rate": 48000, "sample_format": "float32", "channels": 1}
    )
    assert ok is True


@pytest.mark.parametrize("sr", [8000, 16000, 44100, 48000])
def test_validate_audio_meta_accepts_common_rates(sr):
    ok, _ = vox_main._validate_audio_meta({"context_sample_rate": sr})
    assert ok is True


def test_validate_audio_meta_rejects_bad_format():
    ok, reason = vox_main._validate_audio_meta({"sample_format": "int16"})
    assert ok is False and "sample_format" in reason


def test_validate_audio_meta_rejects_out_of_range_rate():
    ok, reason = vox_main._validate_audio_meta({"context_sample_rate": 1000})
    assert ok is False and "range" in reason.lower() or "диапазон" in reason


def test_validate_audio_meta_rejects_bad_channels():
    ok, reason = vox_main._validate_audio_meta({"channels": 5})
    assert ok is False
