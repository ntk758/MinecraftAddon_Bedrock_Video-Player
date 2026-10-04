"""GPU バックエンド選択 (CUDA / ROCm / DirectML / CPU) のテスト。"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert  # noqa: E402
from mvcodec.color import PALETTES  # noqa: E402
from mvcodec.device import GpuDevice, detect_gpu  # noqa: E402

HAS_TORCH = importlib.util.find_spec("torch") is not None


def test_cpu_preference_never_returns_gpu():
    assert detect_gpu("cpu") is None
    assert convert.select_gpu("cpu", "none", True) is None


def test_select_gpu_falls_back_to_cpu_when_self_test_fails(monkeypatch, capsys):
    fake = GpuDevice(torch_device="fake", backend="directml", name="Fake GPU")
    monkeypatch.setattr(convert, "detect_gpu", lambda preference: fake)

    def broken(*_args, **_kwargs):
        raise RuntimeError("unsupported op")

    monkeypatch.setattr(convert, "gpu_self_test", broken)
    assert convert.select_gpu("auto", "ordered", True) is None
    assert "unsupported op" in capsys.readouterr().err


def test_select_gpu_uses_device_when_self_test_passes(monkeypatch):
    fake = GpuDevice(torch_device="fake", backend="rocm", name="Fake Radeon")
    monkeypatch.setattr(convert, "detect_gpu", lambda preference: fake)
    monkeypatch.setattr(convert, "gpu_self_test", lambda *a, **k: None)
    assert convert.select_gpu("auto", "none", True) is fake


def test_missing_requested_backend_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(convert, "detect_gpu", lambda preference: None)
    assert convert.select_gpu("directml", "none", True) is None
    assert "directml" in capsys.readouterr().err


@pytest.mark.skipif(not HAS_TORCH, reason="torch が必要")
def test_squared_distances_matches_cdist():
    import torch
    from mvcodec.device import squared_distances

    gen = torch.Generator().manual_seed(0)
    a = torch.rand(200, 3, generator=gen)
    b = torch.rand(39, 3, generator=gen)
    assert torch.allclose(squared_distances(a, b), torch.cdist(a, b) ** 2, atol=1e-5)
    assert torch.equal(squared_distances(a, b).argmin(dim=1), torch.cdist(a, b).argmin(dim=1))


@pytest.mark.skipif(not HAS_TORCH, reason="torch が必要")
@pytest.mark.parametrize("dither", ["none", "ordered", "blue_noise"])
def test_gpu_quantizer_runs_on_torch_cpu(dither):
    """GPU 用の減色処理がデバイス非依存に書けていること (torch の CPU 実行で検証)。"""
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, size=(16, 24, 3), dtype=np.uint8) for _ in range(6)]
    pal_a = np.array([item["rgb"] for item in PALETTES["full"]], dtype=np.float64)
    pal_b = pal_a[::-1].copy()
    results = list(convert.quantize_frames_gpu(frames, [pal_a, pal_b], [0, 3, 6], 24, 16, dither, True, gpu=None))
    assert len(results) == 6
    for r in results:
        assert r.shape == (16, 24) and r.min() >= 0 and r.max() < len(pal_a)
