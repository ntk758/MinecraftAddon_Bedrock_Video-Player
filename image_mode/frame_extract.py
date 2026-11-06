"""動画からフレームの PNG を書き出す (ffmpeg を使う)。

画像（テクスチャ）再生方式では、フレームの PNG を Resource Pack に同梱する。
解像度とフレームレートを引数で指定する。フレームレートは 20 が上限 (Minecraft は 1 秒に 20 tick)。

`extract_frames()` は PNG の bytes のリストと、実際に使った大きさ / フレーム数を返す。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

TICKS_PER_SECOND = 20
MAX_FPS = 20.0


def ffmpeg_command() -> str:
    """ffmpeg の実行ファイル。見つからればそのパス、無ければ "ffmpeg"。"""
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


def ticks_per_frame(fps: float) -> int:
    """1 フレームを何 tick で進めるか。fps が 20 を超えたら 1 tick に 1 フレーム (20fps) にする。"""
    if fps <= 0:
        raise ValueError(f"fps は 0 より大きい値: {fps}")
    return max(1, round(TICKS_PER_SECOND / min(float(fps), MAX_FPS)))


def extract_frames(source: Path | None, width: int, height: int, fps: float = 20.0,
                   max_frames: int | None = None) -> dict:
    """フレームの PNG を書き出す。

    source を省略すると ffmpeg の testsrc (動くテスト映像) を使う。
    width / height は `-vf fps=..,scale=..:force_original_aspect_ratio=decrease` で等比例的に縮小するので、
    元動画の aspect ratio が違うときは実際の大きさが要求より小さくなる (返り値の width / height が実際の値)。

    返り値: {"frames": [PNG bytes], "width": 実際の幅, "height": 実際の高さ, "fps": clamp 後の fps, "ticks_per_frame": ..}
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"解像度は 0 より大きい値: {width}x{height}")
    use_fps = min(float(fps), MAX_FPS)
    count = max_frames if max_frames is not None else 12
    if count <= 0:
        raise ValueError(f"フレームの数 は 0 より大きい値: {count}")
    with tempfile.TemporaryDirectory(prefix="bvp-frames-") as directory:
        target = Path(directory)
        inputs = ([f"-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={use_fps}"]
                  if source is None else ["-i", str(source)])
        command = [ffmpeg_command(), "-hide_banner", "-loglevel", "error", *inputs,
                   "-vf", f"fps={use_fps},scale={width}:{height}:force_original_aspect_ratio=decrease",
                   "-frames:v", str(count), str(target / "frame_%04d.png")]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg が失敗しました ({result.returncode}): {result.stderr.strip()[:400]}")
        paths = sorted(target.glob("frame_*.png"))
        if not paths:
            raise RuntimeError(f"フレームの PNG が 0 枚: {result.stderr.strip()[:200]}")
        frames = [path.read_bytes() for path in paths]
    size = png_size(frames[0])
    return {"frames": frames, "width": size[0], "height": size[1], "fps": use_fps,
            "ticks_per_frame": ticks_per_frame(use_fps)}


def png_size(data: bytes) -> tuple[int, int]:
    """PNG の IHDR から幅と高さを読む (画像のライブラリを使わない)。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("PNG のヘッダーがありません")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
