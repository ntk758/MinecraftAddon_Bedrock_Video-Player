"""PMX・VMD・音声から、MMD を動画 (MP4) に描く (Rendered Mode。docs/research/mmd_dual_path_design_2026-11.md の 3.2)。

1. 姿勢を全部のコマぶん計算する (image_mode.rig_anim.PosedDance。物理は時間の順に 1 回だけ)
2. コマを PNG に描く (mvcodec.mmd_render。プロセスを分けて並べて描く)。作業フォルダーの frame_000000.png …
   書き終えてから名前を付けるので、途中で止めても書きかけのコマは残らない。もう一度動かすと、
   設定が同じなら、できているコマ (開いて大きさを確かめる) を飛ばして続きから描く
3. FFmpeg で MP4 にする (音声があれば入れて、長さを音声に合わせる)。一時ファイルに書き、長さ・コマ数・音声を
   読み直して確かめてから名前を付ける。FFmpeg が失敗したら止める (成功にしない)
4. 確かめ終えたら、作業フォルダーのコマを消す (keep_frames なら残す)
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

# Rendered Mode の段: 名前 → (幅, 高さ, fps)。描いた動画の目標 (Minecraft の中でこの大きさのまま流れるとは限らない)
RENDER_PRESETS = {
    "mobile": (854, 480, 24),
    "standard": (1280, 720, 30),
    "high": (1920, 1080, 30),
    "ultra": (2560, 1440, 30),
    "experimental": (3840, 2160, 30),
}
DEFAULT_PRESET = "standard"
FRAME_PATTERN = "frame_%06d.png"
SETTINGS_FILE = "render.json"
# 縦横 N 倍で描くときの 1 画素あたりのメモリの目安 (バイト。深さ・持ち主・重心座標・色の作業用)。並べるプロセスの数を決める
BYTES_PER_PIXEL = 64
MODEL_BYTES = 400 * 1024 * 1024
ENCODERS = ("auto", "h264_amf", "libx264")


class RenderError(RuntimeError):
    pass


@dataclass
class VideoJob:
    pmx: str
    motion: str | None = None
    output: str = "mmd.mp4"
    audio: str | None = None
    face_motions: list = field(default_factory=list)
    camera_motion: str | None = None
    stage: str | None = None
    preset: str = DEFAULT_PRESET
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    seconds: float | None = None
    supersample: int | None = None
    physics: str | None = "standard"
    expressions: bool = True
    edges: bool = True
    background: tuple = (32, 36, 48)
    background_image: str | None = None
    jobs: int | None = None
    encoder: str = "auto"
    crf: int = 18
    work_dir: str | None = None
    keep_frames: bool = False
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    def size(self) -> tuple[int, int, float]:
        width, height, fps = RENDER_PRESETS[self.preset]
        width, height = self.width or width, self.height or height
        if width % 2 or height % 2:
            raise RenderError(f"幅と高さは偶数にしてください (H.264 の yuv420p): {width}x{height}")
        return width, height, float(self.fps or fps)

    def resolved_supersample(self) -> int:
        if self.supersample:
            return int(self.supersample)
        width, height, _ = self.size()
        return 1 if width * height > 2560 * 1440 else 2


def _file_identity(path) -> dict | None:
    if not path:
        return None
    p = Path(path)
    stat = p.stat()
    return {"name": p.name, "size": stat.st_size, "mtime": int(stat.st_mtime)}


def job_fingerprint(job: VideoJob) -> dict:
    """作業フォルダーのコマを使い回してよいかを決める、描き方の設定 (出力先・並べる数・エンコードは入れない)。"""
    width, height, fps = job.size()
    return {
        "version": 1, "pmx": _file_identity(job.pmx), "motion": _file_identity(job.motion),
        "faces": [_file_identity(f) for f in job.face_motions], "camera": _file_identity(job.camera_motion),
        "stage": _file_identity(job.stage), "audio": _file_identity(job.audio), "width": width, "height": height,
        "fps": fps, "seconds": job.seconds, "supersample": job.resolved_supersample(), "physics": job.physics,
        "expressions": job.expressions, "edges": job.edges, "background": list(job.background),
        "background_image": _file_identity(job.background_image),
    }


def probe_media(path, ffprobe: str = "ffprobe") -> dict:
    """ffprobe で、動画の長さ・コマ数・大きさと、音声の長さを読む。"""
    cmd = [ffprobe, "-v", "error", "-count_frames", "-show_entries",
           "stream=codec_type,codec_name,width,height,nb_read_frames,duration,r_frame_rate,sample_rate:format=duration",
           "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RenderError(f"できた動画を読めませんでした ({path}): {result.stderr.strip()[-500:]}")
    data = json.loads(result.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    out = {"duration": float(data.get("format", {}).get("duration", 0) or 0)}
    if video:
        out.update({"video_codec": video.get("codec_name"), "width": int(video.get("width", 0)),
                    "height": int(video.get("height", 0)), "frames": int(video.get("nb_read_frames", 0) or 0),
                    "video_duration": float(video.get("duration", 0) or 0)})
    if audio:
        out.update({"audio_codec": audio.get("codec_name"), "audio_duration": float(audio.get("duration", 0) or 0),
                    "sample_rate": int(audio.get("sample_rate", 0) or 0)})
    return out


def frame_is_valid(path: Path, width: int, height: int) -> bool:
    """できているコマか (PNG として壊れていず、大きさが合う)。"""
    try:
        with Image.open(path) as image:
            if image.size != (width, height) or image.format != "PNG":
                return False
            image.verify()
        return True
    except (OSError, ValueError, SyntaxError):
        return False


# --- 子プロセス (コマを描く) ---

_WORKER = {}


def _worker_init(pmx: str, stage: str | None, settings: dict, background_image: str | None) -> None:
    from mvcodec import mmd, mmd_render

    model = mmd.read_pmx(pmx)
    scenes = {"model": mmd_render.Scene(model, Path(pmx).parent)}
    static = None
    if stage:
        stage_model = mmd.read_pmx(stage)
        stage_scene = mmd_render.Scene(stage_model, Path(stage).parent)
        rest = mmd.Poser(stage_model).pose({}, 0)
        static = (stage_scene, *stage_scene.posed(rest))
    image = None
    if background_image:
        with Image.open(background_image) as source:
            image = np.asarray(source.convert("RGB"), np.float32) / 255.0
    _WORKER.update(scenes=scenes, static=static,
                   settings=mmd_render.RenderSettings(**settings, background_image=image))


def _render_one(task) -> tuple[int, float, int]:
    from mvcodec import mmd_render

    index, path, skin, weights, camera = task
    start = time.perf_counter()
    scene = _WORKER["scenes"]["model"]
    vertices, normals = scene.posed(skin, weights)
    meshes = [(scene, vertices, normals)]
    if _WORKER["static"]:
        meshes.append(_WORKER["static"])
    eye, target, up, fov = camera
    image = mmd_render.render(meshes, mmd_render.Camera(np.array(eye), np.array(target), np.array(up), fov),
                              _WORKER["settings"])
    temp = Path(str(path) + ".tmp")
    Image.fromarray(image[..., :3], "RGB").save(temp, "PNG", compress_level=1)
    os.replace(temp, path)
    peak = 0
    try:
        import psutil
        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None) or info.rss
    except ImportError:
        pass
    return index, time.perf_counter() - start, peak


# --- 親 ---

def default_jobs(width: int, height: int, supersample: int) -> int:
    """並べて描くプロセスの数: CPU の数と、空いているメモリの 6 割に入る数の小さいほう。"""
    cpus = os.cpu_count() or 1
    try:
        import psutil
        available = psutil.virtual_memory().available
    except ImportError:
        available = 8 * 1024 ** 3
    per = width * height * supersample * supersample * BYTES_PER_PIXEL + MODEL_BYTES
    return max(1, min(cpus - 1 if cpus > 2 else cpus, int(available * 0.6 // per)))


def _cameras(dance, count: int, fps: float, aspect: float, scene_for_framing, log) -> list[tuple]:
    from mvcodec import mmd_render

    if dance.camera_keys:
        cams = [mmd_render.camera_from_vmd(dance.camera_keys, i / fps * 30) for i in range(count)]
    else:
        # カメラの VMD が無ければ、踊りの全部の姿勢が入る正面の固定のカメラ
        step = max(1, len(dance.skins) // 24)
        points = np.concatenate([scene_for_framing.posed(dance.skins[k])[0] for k in range(0, len(dance.skins), step)])
        camera = mmd_render.framing_camera(points, aspect)
        log("カメラ: VMD のカメラが無いので、踊り全体が入る正面の固定のカメラにします")
        cams = [camera] * count
    return [(c.eye.tolist(), c.target.tolist(), c.up.tolist(), float(c.fov)) for c in cams]


def _choose_encoder(job: VideoJob) -> list[str]:
    if job.encoder != "auto":
        return [job.encoder]
    return ["h264_amf", "libx264"]


def _encode_args(encoder: str, crf: int) -> list[str]:
    if encoder == "h264_amf":
        # AMD の GPU。品質を固定 (qp) にする
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]


def encode(frames_dir: Path, fps: float, output: Path, job: VideoJob, seconds: float, log) -> dict:
    """コマを MP4 にする。音声があれば AAC で入れ、長さを seconds (音声の長さ) にそろえる。確かめてから名前を付ける。"""
    temp = output.with_name(output.stem + ".part" + output.suffix)
    errors = []
    for encoder in _choose_encoder(job):
        cmd = [job.ffmpeg, "-y", "-v", "error", "-framerate", f"{fps:g}", "-i", str(frames_dir / FRAME_PATTERN)]
        if job.audio:
            cmd += ["-i", str(job.audio), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
        cmd += [*_encode_args(encoder, job.crf), "-pix_fmt", "yuv420p", "-r", f"{fps:g}", "-t", f"{seconds:.6f}",
                "-movflags", "+faststart", str(temp)]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            break
        errors.append(f"{encoder}: {result.stderr.strip()[-400:]}")
        log(f"エンコーダー {encoder} が使えませんでした。次を試します")
        temp.unlink(missing_ok=True)
    else:
        raise RenderError("FFmpeg で動画にできませんでした: " + " / ".join(errors))
    info = probe_media(temp, job.ffprobe)
    expected = int(round(seconds * fps))
    problems = []
    width, height, _ = job.size()
    if (info.get("width"), info.get("height")) != (width, height):
        problems.append(f"大きさが {info.get('width')}x{info.get('height')} (期待 {width}x{height})")
    if abs(info.get("frames", 0) - expected) > 1:
        problems.append(f"コマが {info.get('frames')} (期待 {expected})")
    if job.audio and "audio_duration" not in info:
        problems.append("音声がありません")
    if problems:
        temp.unlink(missing_ok=True)
        raise RenderError("できた動画がおかしいので捨てました: " + "、".join(problems))
    os.replace(temp, output)
    info["encoder"] = encoder
    return info


def render_video(job: VideoJob, log=print) -> dict:
    """VideoJob どおりに MP4 を作り、計測と確かめの結果 (dict) を返す。"""
    from image_mode import rig_anim
    from mvcodec import mmd, mmd_render

    for label, path in (("モデル", job.pmx), ("モーション", job.motion), ("曲", job.audio),
                        ("カメラ", job.camera_motion), ("ステージ", job.stage), ("背景", job.background_image),
                        *[("表情", f) for f in job.face_motions]):
        if path and not Path(path).is_file():
            raise RenderError(f"{label}のファイルがありません: {path}")
    if job.preset not in RENDER_PRESETS:
        raise RenderError(f"段は {', '.join(RENDER_PRESETS)} のどれか: {job.preset}")
    width, height, fps = job.size()
    supersample = job.resolved_supersample()
    output = Path(job.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(job.work_dir) if job.work_dir else output.with_name(output.stem + ".frames")
    work.mkdir(parents=True, exist_ok=True)
    report = {"preset": job.preset, "width": width, "height": height, "fps": fps, "supersample": supersample}
    timings = {}

    seconds = job.seconds
    if job.audio:
        from image_mode import atlas
        audio_seconds = atlas.probe_duration(job.audio, job.ffmpeg)
        if audio_seconds is None:
            raise RenderError(f"曲の長さを読めませんでした: {job.audio}")
        seconds = min(seconds, audio_seconds) if seconds else audio_seconds
    start = time.perf_counter()
    motion = job.motion
    temp_motion = None
    if motion and Path(motion).suffix.lower() == ".bvh":
        from mvcodec import bvh
        temp_motion = work / "motion_from_bvh.vmd"
        motion = str(bvh.ensure_vmd(motion, job.pmx, work, log=log))
    dance = rig_anim.PosedDance(job.pmx, motion, seconds=seconds, rate=fps, log=log, face_vmds=job.face_motions,
                                expressions=job.expressions, physics=None if job.physics == "off" else job.physics,
                                camera_vmd=job.camera_motion)
    timings["pose_s"] = round(time.perf_counter() - start, 2)
    if seconds is None:
        seconds = (len(dance.frames) - 1) / fps
    count = max(1, int(math.ceil(seconds * fps - 1e-9)))
    report.update(seconds=round(seconds, 4), frames=count)

    fingerprint = job_fingerprint(job)
    settings_path = work / SETTINGS_FILE
    old = None
    if settings_path.exists():
        try:
            old = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
    if old != fingerprint:
        stale = sorted(work.glob("frame_*.png"))
        if stale:
            log(f"作業フォルダーのコマ ({len(stale)} 枚) は設定が違うので描き直します")
            for f in stale:
                f.unlink()
        settings_path.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=1), encoding="utf-8")
    for leftover in work.glob("frame_*.png.tmp"):
        leftover.unlink()

    frames = [work / (FRAME_PATTERN % i) for i in range(count)]
    todo = [i for i, path in enumerate(frames) if not (path.exists() and frame_is_valid(path, width, height))]
    reused = count - len(todo)
    if reused:
        log(f"できているコマ {reused} 枚を使い、残り {len(todo)} 枚を描きます")
    report["frames_reused"] = reused
    model_scene = mmd_render.Scene(dance.model, Path(job.pmx).parent, log=log)
    report["warnings"] = list(model_scene.warnings)
    cameras = _cameras(dance, count, fps, width / height, model_scene, log)
    morph_names = list(dance.morph_tracks)

    def weights_at(i):
        frame = float(dance.frames[min(i, len(dance.frames) - 1)])
        return {name: mmd.sample_morph(dance.morph_tracks[name], frame) for name in morph_names}

    def task(i):
        return (i, str(frames[i]), dance.skins[min(i, len(dance.skins) - 1)], weights_at(i), cameras[i])

    settings = {"width": width, "height": height, "supersample": supersample,
                "background": (*job.background[:3], 255), "edges": job.edges}
    jobs = job.jobs or default_jobs(width, height, supersample)
    report["jobs"] = jobs
    frame_times = []
    peaks = []
    cpu = None
    start = time.perf_counter()
    if todo:
        try:
            import psutil
            psutil.cpu_percent(None)
        except ImportError:
            psutil = None
        log(f"コマを描きます: {len(todo)} 枚、{width}x{height} (縦横 {supersample} 倍で描いて縮める)、{jobs} プロセス")
        step = max(1, len(todo) // 20)
        done = 0
        if jobs == 1:
            _worker_init(job.pmx, job.stage, settings, job.background_image)
            results = (_render_one(task(i)) for i in todo)
            executor = None
        else:
            # 子プロセスの numpy が CPU の数だけスレッドを作ると、プロセスを並べたときに取り合って遅くなる
            for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                os.environ.setdefault(name, "1")
            executor = ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init,
                                           initargs=(job.pmx, job.stage, settings, job.background_image))
            results = executor.map(_render_one, (task(i) for i in todo), chunksize=1)
        try:
            for _, took, peak in results:
                frame_times.append(took)
                peaks.append(peak)
                done += 1
                if done % step == 0 or done == len(todo):
                    log(f"  {done}/{len(todo)} 枚")
        finally:
            if executor is not None:
                executor.shutdown(cancel_futures=True)
        if psutil:
            cpu = psutil.cpu_percent(None)
    timings["render_s"] = round(time.perf_counter() - start, 2)
    if frame_times:
        timings["frame_s_mean"] = round(float(np.mean(frame_times)), 3)
        timings["frame_s_max"] = round(float(np.max(frame_times)), 3)
    report["worker_peak_ram_mib"] = round(max(peaks) / 1024 ** 2, 1) if peaks and max(peaks) else None
    report["cpu_percent_system"] = cpu
    missing = [i for i, path in enumerate(frames) if not frame_is_valid(path, width, height)]
    if missing:
        raise RenderError(f"描けなかったコマがあります: {missing[:10]}")
    start = time.perf_counter()
    info = encode(work, fps, output, job, seconds, log)
    timings["encode_s"] = round(time.perf_counter() - start, 2)
    report["output"] = str(output)
    report["output_bytes"] = output.stat().st_size
    report["frames_bytes"] = sum(p.stat().st_size for p in frames)
    report["probe"] = info
    if job.audio:
        report["av_offset_s"] = round(abs(info.get("video_duration", 0) - info.get("audio_duration", 0)), 4)
    try:
        import psutil
        memory = psutil.Process().memory_info()
        report["main_peak_ram_mib"] = round((getattr(memory, "peak_wset", None) or memory.rss) / 1024 ** 2, 1)
    except ImportError:
        pass
    report["timings"] = timings
    if not job.keep_frames:
        for path in frames:
            path.unlink(missing_ok=True)
        settings_path.unlink(missing_ok=True)
        if temp_motion is not None:
            temp_motion.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass  # ほかのファイルがあれば残す
    log(f"動画: {output} ({report['output_bytes'] / 1024 ** 2:.1f} MiB、{count} コマ、{seconds:.2f} 秒、"
        f"エンコーダー {info['encoder']})")
    return report


def job_to_dict(job: VideoJob) -> dict:
    return asdict(job)
