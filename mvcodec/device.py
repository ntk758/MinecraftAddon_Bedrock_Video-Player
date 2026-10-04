"""GPU バックエンドの検出。

対応バックエンド:
  - cuda     : NVIDIA CUDA 版 PyTorch、および AMD ROCm 版 PyTorch (Linux / Windows)。
               ROCm 版は HIP 経由で torch.cuda として振る舞うため、同じ "cuda" デバイスで動く。
  - directml : torch-directml (Windows の AMD / Intel / NVIDIA など DirectX 12 対応 GPU)。
どれも使えない場合は None を返し、呼び出し側は CPU 経路を使う。
"""
from dataclasses import dataclass

DEVICE_CHOICES = ("auto", "cuda", "rocm", "directml", "cpu")


@dataclass(frozen=True)
class GpuDevice:
    torch_device: object  # torch.device
    backend: str          # "cuda" | "rocm" | "directml"
    name: str

    def describe(self):
        return f"{self.name} ({self.backend})"


def _try_cuda_like():
    """CUDA 版 / ROCm 版 PyTorch の GPU を返す。"""
    try:
        import torch
    except ImportError:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        backend = "rocm" if getattr(torch.version, "hip", None) else "cuda"
        return GpuDevice(torch.device("cuda"), backend, torch.cuda.get_device_name(0))
    except Exception:
        return None


def _try_directml():
    try:
        import torch_directml
    except ImportError:
        return None
    try:
        if torch_directml.device_count() < 1:
            return None
        index = _preferred_directml_index(torch_directml)
        return GpuDevice(torch_directml.device(index), "directml", _clean_name(torch_directml.device_name(index)))
    except Exception:
        return None


def _clean_name(name):
    return str(name).replace(chr(0), "").strip()


def _preferred_directml_index(torch_directml):
    """CPU 内蔵 GPU より単体 GPU (AMD Radeon / NVIDIA GeForce 等) を優先する。"""
    names = [_clean_name(torch_directml.device_name(i)) for i in range(torch_directml.device_count())]
    integrated_markers = ("uhd graphics", "iris", "intel(r) hd", "microsoft basic")
    for i, name in enumerate(names):
        if not any(marker in name.lower() for marker in integrated_markers):
            return i
    return torch_directml.default_device()


def detect_gpu(preference="auto"):
    """preference に従って GPU を選ぶ。見つからなければ None。

    auto は CUDA/ROCm → DirectML の順に探す (CUDA/ROCm の方が高速・互換性が高いため)。
    """
    if preference == "cpu":
        return None
    if preference in ("cuda", "rocm"):
        return _try_cuda_like()
    if preference == "directml":
        return _try_directml()
    return _try_cuda_like() or _try_directml()


def empty_cache(device):
    """キャッシュ解放 API があるバックエンドだけで呼ぶ (DirectML には無い)。"""
    if device is not None and device.backend in ("cuda", "rocm"):
        import torch
        torch.cuda.empty_cache()


def squared_distances(a, b):
    """(N, C) と (M, C) の二乗ユークリッド距離 (N, M)。

    torch.cdist は DirectML で未対応の場合があるため、行列積だけで計算する。
    argmin を取る用途なので平方根は省略している。
    """
    a_sq = (a * a).sum(dim=1, keepdim=True)
    b_sq = (b * b).sum(dim=1).unsqueeze(0)
    return a_sq - 2.0 * (a @ b.transpose(0, 1)) + b_sq
