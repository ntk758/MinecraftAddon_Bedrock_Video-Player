"""動画から Minecraft Bedrock 用の .mcaddon を作成する GUI。複数動画搭載・音声同期・多言語対応。"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui_i18n import (
    LOCALES, Translator, available_languages, initial_language, load_settings, save_settings,
)
from pack_builder import (  # noqa: F401 (stable_pack_uuid などは以前からこのモジュールにあった名前)
    AUDIO_CHUNK_SECONDS,
    BUILD_STAMP_EPOCH,
    MANIFEST_VERSION_MAX,
    build_addon,
    build_manifest_version,
    frame_data_dims,
    stable_pack_uuid,
    vox_thumbnail_png,
)
from mvcodec.stage import stage_geometry
from pack_metadata import PACK_VERSION, RELEASE_NOTES, version_text  # noqa: F401 (PACK_VERSION はテストが使う)


APP_DIR = Path(__file__).resolve().parent
MANIFEST = APP_DIR / "manifest.json"
MAIN_SCRIPT = APP_DIR / "main.js"
CODEC_SCRIPT = APP_DIR / "codec.js"
CONVERTER = APP_DIR / "convert.py"
# EXE 版で自分自身を変換器として起動するための引数
RUN_CONVERTER_FLAG = "--run-converter"
# EXE で人物の切り抜きだけを行う (mvcodec/person.py のコマンドライン。scripts/make_person_videos.py と同じ)
RUN_PERSON_FLAG = "--run-person-split"


def subprocess_kwargs() -> dict:
    """EXE (windowed) から ffmpeg 等を起動する時にコンソール窓を出さない。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def person_command() -> list[str]:
    """人物の切り抜き (mvcodec/person.py のコマンドライン) を起動するコマンドの先頭部分。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_PERSON_FLAG]
    return [sys.executable, str(APP_DIR / "scripts" / "make_person_videos.py")]


# 人物の切り抜きのログ (子プロセスの出力すべてと統計の JSON) を残す数。新しい順にこれだけ残し、古いものは消す
PERSON_LOG_KEEP = 10
# 人物の切り抜きのまとめに注意を足す条件: 人物が空のフレームがこの割合を超える・予備のモデルがこの割合を超える
PERSON_WARN_EMPTY = 0.10
PERSON_WARN_FALLBACK = 0.50


def logs_dir() -> Path:
    """ログを置くフォルダ。Windows は %LOCALAPPDATA%/BlockVideoPlayer/logs、ほかは ~/.local/state/BlockVideoPlayer/logs。
    環境変数 BVP_LOG_DIR で変えられる。"""
    if os.environ.get("BVP_LOG_DIR"):
        return Path(os.environ["BVP_LOG_DIR"])
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "BlockVideoPlayer" / "logs"


def new_person_log(keep: int = PERSON_LOG_KEEP) -> Path | None:
    """人物の切り抜きの新しいログのファイル (person-YYYYMMDD-HHMMSS.log) を作り、古いログを消して今回を含め新しい keep 個だけ
    残す (子プロセスが書く統計の person-stats-*.json も、今回の 1 つを足して keep 個になるよう消す)。フォルダやファイルを
    作れなければ None (ログなしで続ける)。"""
    folder = logs_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = folder / f"person-{stamp}.log"
        number = 1
        while path.exists():   # 同じ秒に 2 つ作ったとき
            number += 1
            path = folder / f"person-{stamp}-{number}.log"
        path.touch()
    except OSError:
        return None

    def age(file: Path) -> tuple[float, str]:
        try:
            return file.stat().st_mtime, file.name
        except OSError:
            return 0.0, file.name

    # 今作ったログ (と、これから子プロセスが書く統計) を入れて keep 個になるよう、前のものを keep - 1 個残す
    for pattern in ("person-*.log", "person-stats-*.json"):
        others = [file for file in folder.glob(pattern) if file != path]
        for stale in sorted(others, key=age, reverse=True)[keep - 1:]:
            try:
                stale.unlink()
            except OSError:
                pass
    return path


@contextlib.contextmanager
def open_log(path: Path | None):
    """ログのファイルに 1 行ずつ追記する関数 write(text) を渡す。1 行ごとに書き出す (途中で落ちても残る)。path が None や、
    書けないときは何もしない。"""
    handle = None
    if path is not None:
        try:
            handle = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            handle = None

    def write(text: str) -> None:
        if handle is not None:
            try:
                handle.write(text + "\n")
            except OSError:
                pass

    try:
        yield write
    finally:
        if handle is not None:
            handle.close()


def person_summary_lines(tr, stats: dict, log: Path | None) -> list[str]:
    """人物の切り抜きの統計 (mvcodec/person.py が出す「stats <JSON>」) から、処理ログに出すまとめを作る: まとめ 1 行、ログの場所
    1 行 (ログは動画の場所を含み、ユーザー名が入ることがあるので、共有するときの注意を添える)、そして次のどれかに
    当てはまるときだけ注意 1 行: 人物が空のフレームが 10% を超える・予備のモデルが 50% を超える・目立つ物の切り抜き (BiRefNet) を外した。
    予備の結果を検査で退けて空にしたフレームがあれば、まとめの 1 行の終わりにその数を足す。"""
    frames = int(stats.get("frames") or 0)
    sources = stats.get("sources") or {}
    fallback = int(sources.get("matched", 0)) + int(sources.get("fallback", 0))
    empty = int(stats.get("empty_frames") or 0)
    models = stats.get("models") or {}
    providers = (models.get("anime") or models.get("photo") or {}).get("providers") or []
    lines = [tr("build.person_summary", frames=frames, seconds=f"{float(stats.get('wall_s') or 0):.0f}",
                profile=tr(f"profile.{stats.get('profile', 'photo')}"), backend=providers[0] if providers else "?",
                primary=int(sources.get("anime", sources.get("photo", 0))), filled=int(sources.get("filled", 0)),
                fallback=fallback, empty=empty)]
    rejected = int(sources.get("rejected", 0))
    if rejected:
        lines[0] += tr("build.person_rejected", rejected=rejected)
    if log is not None:
        lines.append(tr("build.person_log", path=log))
    warnings = []
    if frames and empty / frames > PERSON_WARN_EMPTY:
        warnings.append(tr("build.person_warn_empty", percent=round(100 * empty / frames)))
    if frames and fallback / frames > PERSON_WARN_FALLBACK:
        warnings.append(tr("build.person_warn_fallback", percent=round(100 * fallback / frames)))
    dropped = stats.get("salient_dropped")
    if dropped:
        reason = dropped.get("reason")
        warnings.append(tr(f"build.person_warn_dropped_{reason if reason in ('oom', 'error') else 'slow'}",
                           frame=dropped.get("frame", "?")))
    if warnings:
        lines.append(tr("build.person_warn", reasons=" / ".join(warnings)))
    return lines


def converter_command() -> list[str]:
    """convert.py を起動するコマンドの先頭部分。EXE 版では自分自身を変換モードで起動する。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_CONVERTER_FLAG]
    return [sys.executable, str(CONVERTER)]


def default_output_path() -> Path:
    """作るアドオンの既定の保存先。EXE 版は EXE と同じフォルダ (以前は中身の _internal フォルダになっていて見つけにくかった)。"""
    folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else APP_DIR
    return folder / "VideoPlayer.mcaddon"


def get_ffmpeg_path() -> str | None:
    # 1. PyInstaller 内部展開ディレクトリ
    if hasattr(sys, "_MEIPASS"):
        meipass_ffmpeg = Path(getattr(sys, "_MEIPASS")) / "ffmpeg.exe"
        if meipass_ffmpeg.is_file():
            return str(meipass_ffmpeg)
    
    # 2. アプリ実行ファイルと同階層
    app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else APP_DIR
    for name in ("ffmpeg.exe", "ffmpeg"):
        local_ffmpeg = app_dir / name
        if local_ffmpeg.is_file():
            return str(local_ffmpeg)

    # 3. カレントディレクトリ
    for name in ("ffmpeg.exe", "ffmpeg"):
        cwd_ffmpeg = Path.cwd() / name
        if cwd_ffmpeg.is_file():
            return str(cwd_ffmpeg)

    # 4. システム PATH
    return shutil.which("ffmpeg")


_VIDEO_SIZE_PATTERN = re.compile(r"Stream #.*?Video:.*?[ ,](\d{2,5})x(\d{2,5})[ ,\[]")
# スマートフォンの縦動画などは、保存された大きさと別に回転の情報を持つ (ffmpeg は変換時に回転して読む)
_ROTATION_PATTERN = re.compile(r"rotation of (-?\d+(?:\.\d+)?) degrees|rotate\s*:\s*(-?\d+)")


def probe_video_size(path: str | Path, ffmpeg: str | None = None) -> tuple[int, int] | None:
    """動画の幅と高さ (画素) を ffmpeg の情報表示から読む。読めなければ None。

    ffprobe は EXE 版に同梱していないので、ffmpeg -i の標準エラー出力の映像ストリームの行を使う。
    90° / 270° の回転の情報があれば、変換時の向き (ffmpeg が自動で回転した後) の幅と高さを返す。
    """
    ffmpeg = ffmpeg or get_ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30, **subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VIDEO_SIZE_PATTERN.search(result.stderr or "")
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    # 回転の情報は映像ストリームの行の後 (次のストリームの前) にある
    rest = result.stderr[match.end():]
    next_stream = rest.find("Stream #")
    rotation = _ROTATION_PATTERN.search(rest if next_stream < 0 else rest[:next_stream])
    if rotation:
        degrees = round(float(rotation.group(1) or rotation.group(2))) % 180
        if degrees == 90:
            width, height = height, width
    return width, height


def suggest_size(source_width: int, source_height: int, long_side: int, limit: int | None = None) -> tuple[int, int]:
    """元の縦横比のまま、長い辺を long_side ブロックにした幅・高さ (黒帯が出ない大きさ)。

    例: 1920×1080 で long_side 128 → 128×72。どちらの辺も 1〜limit (既定は MAX_SCREEN_SIZE) に収める。
    """
    limit = MAX_SCREEN_SIZE if limit is None else limit
    long_side = max(1, min(limit, int(long_side)))
    if source_width >= source_height:
        width, height = long_side, round(long_side * source_height / source_width)
    else:
        width, height = round(long_side * source_width / source_height), long_side
    return max(1, min(limit, width)), max(1, min(limit, height))


# 1 ブロック置く費用の目安 (µs)。Windows 版の実測 (fillBlocks の箱 0.55〜1.2、1 個ずつ 2.3) の中間
LOAD_US_PER_BLOCK = 1.5
# 全マスが変わったときに 1 tick (50 ms) あたり置くのにかかる時間 (ms) の目安の境目: 軽い / 普通 / それ以上は重い
LOAD_LEVELS = ((8.0, "light"), (25.0, "medium"))
# 立体を置ける高さ (オーバーワールドの建築範囲 Y=-64〜319)
MAX_VOLUME_HEIGHT = 384


def stage_figure_size(width: int, height: int) -> tuple[int, int]:
    """だまし絵にする、動画から作る立体の大きさ (長い辺を STAGE_MAX_SIDE までに縮める。縦横比は保つ)。"""
    scale = min(1.0, STAGE_MAX_SIDE / max(width, height, 1))
    return max(1, round(width * scale)), max(1, round(height * scale))


def estimate_load(width: int, height: int, fps: int, mode: str = "screen", depth: int = 16,
                  anamorphic: bool = False, background: bool = False) -> dict:
    """再生負荷の目安。全マスが毎フレーム変わる最悪の場合に、1 tick あたり何 ms ブロックを置くことになるか。

    実際に変わるのは多くの映像で 1〜3 割なので、普段の負荷はこれより小さい。立体は表面と側面が中心なので、
    盤面全体ではなく奥行き 3 段ぶんを目安にする。だまし絵は立体を縮め (stage_figure_size)、背景の映像があれば
    後ろの壁 (舞台の幅 × 壁の高さ) のマスも足す (人物の切り抜きは、人物を消した映像が背景になる)。
    """
    three_d = mode in VOLUME_MODES
    if anamorphic and three_d:
        width, height = stage_figure_size(width, height)
    cells = width * height * (min(depth, 3) if three_d else 1)
    if anamorphic and three_d and (background or mode in PERSON_MODES):
        from mvcodec.stage import stage_geometry
        geom = stage_geometry(width, height, depth)
        cells += geom["stageWidth"] * geom["wallHeight"]
    blocks_per_second = cells * fps
    ms_per_tick = blocks_per_second * LOAD_US_PER_BLOCK / 1000 / 20
    level = next((name for limit, name in LOAD_LEVELS if ms_per_tick < limit), "heavy")
    return {"cells": cells, "blocks_per_second": blocks_per_second, "ms_per_tick": ms_per_tick, "level": level}


def validate_settings(width, height, fps, duration=None, mode="screen", depth=16) -> list[str]:
    """変換設定の誤りを、エラーメッセージの翻訳キー (error.*) の一覧で返す (空なら問題なし)。"""
    errors = []
    if not (1 <= width <= MAX_SCREEN_SIZE and 1 <= height <= MAX_SCREEN_SIZE):
        errors.append("error.size_range")
    if not MIN_FPS <= fps <= MAX_FPS:
        errors.append("error.fps_range")
    if duration is not None and duration <= 0:
        errors.append("error.duration")
    if mode in VOLUME_MODES:
        if not 2 <= depth <= MAX_DEPTH:
            errors.append("error.depth_range")
        if height > MAX_VOLUME_HEIGHT:
            errors.append("error.volume_height")
    return errors


# 選択肢の値 (convert.py に渡す値)。表示名は locales/*.json の "<種類>.<値>" キー
PALETTE_VALUES = ("auto", "all", "full", "expanded", "concrete", "glass")
DITHER_VALUES = ("none", "blue_noise", "ordered", "floyd", "atkinson", "burkes", "sierra")
DEVICE_VALUES = ("auto", "cuda", "directml", "cpu")
# person: 動画の人物を AI で切り抜いて、丸くふくらませた立体にする (変換は inflate + 人物の範囲の動画)。
# person_anime は同じことをアニメ・イラスト向けのモデルで行う
MODE_VALUES = ("screen", "relief", "extrude", "inflate", "person", "person_anime")
VOLUME_MODES = ("relief", "extrude", "inflate", "person", "person_anime")
# 人物を切り抜くモード → 切り抜きのモデル (mvcodec/person.py の SEGMENTERS)
PERSON_MODES = {"person": "photo", "person_anime": "anime"}
MAX_SCREEN_SIZE = 512
MAX_DEPTH = 64
DEFAULT_MODE = "screen"
DEFAULT_DEPTH = 16
VOX_SUFFIX = ".vox"
# MMD のモデル。追加するときに踊り (.vmd) も選び、作るときに立体のアニメ (.vox) にする
PMX_SUFFIX = ".pmx"
# MMD の人物の高さ (ブロック) の上限。高すぎると重い (実機で高さ 56 は 10 fps、96 は重すぎた)
MMD_MAX_HEIGHT = 64
# だまし絵のときの上限。MMD のモデルは表面だけの殻で、髪やスカートの薄い板が多く、見える面が同じ高さの手作りの
# 立体の約 2.6 倍ある (実在のモデルで、高さ 56 は実機で 1 フレーム約 90 ms の見込み。40 で約 42 ms)
MMD_STAGE_MAX_HEIGHT = 40
# 「MMD を高解像度で (エクストリーム)」のときの上限。重いので、作るときに 1 フレームの見込みと勧める fps を出す
MMD_EXTREME_MAX_HEIGHT = 128
MMD_EXTREME_STAGE_MAX_HEIGHT = 80
# だまし絵の MMD の 1 フレームのスクリプト時間の見込み: 実機で高さ 40 は 44 ms (2026-10-24)、80 は 217 ms (2026-10-25、
# 4.9 倍) だったので、高さの 2.3 乗に比例するとみなす (モックでは 64 が 3.1 倍、80 が 5.6 倍)
MMD_STAGE_MS_AT_40 = 44.0
MMD_STAGE_COST_EXPONENT = 2.3


def mmd_height_limit(anamorphic: bool, extreme: bool = False) -> int:
    """MMD の人物の高さ (ブロック) の上限。"""
    if anamorphic:
        return MMD_EXTREME_STAGE_MAX_HEIGHT if extreme else MMD_STAGE_MAX_HEIGHT
    return MMD_EXTREME_MAX_HEIGHT if extreme else MMD_MAX_HEIGHT


def mmd_stage_frame_ms(height: int) -> float:
    """だまし絵の MMD の、1 フレームのスクリプト時間の見込み (ms)。"""
    return MMD_STAGE_MS_AT_40 * (height / 40) ** MMD_STAGE_COST_EXPONENT


def mmd_stage_max_fps(height: int) -> int:
    """だまし絵の MMD が遅れずに再生できる fps の目安 (1 フレームの見込みの時間に 1 割の余裕を見る)。"""
    return max(1, int(1000 / (mmd_stage_frame_ms(height) * 1.1)))
# だまし絵にする、動画から作る立体 (レリーフ・押し出し・ふくらませ・人物) の長い辺の上限。舞台 (と背景の映像) は
# 立体の大きさに比例して広がる (実機で人物 128 × 128 + 背景の映像 320 × 192 は 1 フレーム平均 238 ms、5 fps の
# 目標に 3 fps)。80 × 45 なら背景は 160 × 68
STAGE_MAX_SIDE = 80
# 画質プリセット: キー -> (幅, 高さ, fps)
QUALITY_PRESETS: dict[str, tuple[int, int, int]] = {
    "light": (64, 64, 20),
    "standard": (96, 96, 10),
    "high": (128, 128, 10),
    "detail": (128, 128, 5),
    "ultra": (256, 256, 5),
    "extreme": (512, 512, 2),
}
# fps スライダーの範囲 (Minecraft は 1 秒 20 tick なので 20 fps が上限)
MIN_FPS, MAX_FPS = 1, 20
# ゲーム内の表示言語: all = 各プレイヤーのゲーム言語 (10 言語) / fixed = この画面の言語に固定 (言語ファイル 1 個)
ADDON_LANG_VALUES = ("all", "fixed")
DEFAULT_PRESET = "high"
# 自動: 全ブロックの候補からシーンごとに最大 64 色を選ぶ (全ブロックとほぼ同じ画質でデータが少ない。docs/research/palette_space.md)
DEFAULT_PALETTE = "auto"
DEFAULT_DITHER = "blue_noise"
# 立体の表示モードで使うディザ。立体は形 (奥行き) が主役で、ディザの粒は表面の白い点々のノイズになるので使わない
VOLUME_DITHER = "none"


def dither_after_mode_change(old_mode: str, new_mode: str, dither: str, auto_set: bool) -> tuple[str, bool]:
    """表示モードを切り替えたときのディザと「自動で変えたか」の印を返す。

    平面 → 立体で、ディザが既定 (Blue Noise) のままなら「なし」にする。立体 → 平面で、自動で「なし」にしたままなら
    既定に戻す。利用者が自分で選んだディザは変えない。
    """
    if old_mode == "screen" and new_mode != "screen" and dither == DEFAULT_DITHER:
        return VOLUME_DITHER, True
    if old_mode != "screen" and new_mode == "screen" and auto_set and dither == VOLUME_DITHER:
        return DEFAULT_DITHER, False
    return dither, auto_set and new_mode != "screen"
DEFAULT_DEVICE = "auto"


def option_labels(tr: Translator, kind: str, values) -> dict[str, str]:
    """{表示名: 値} (コンボボックス用、表示順を保つ)"""
    return {tr(f"{kind}.{value}"): value for value in values}


def _all_language_labels(kind: str, values) -> dict[str, str]:
    labels = {}
    for language in LOCALES:
        labels.update(option_labels(Translator(language), kind, values))
    return labels


# 日本語の表示名 -> 値 (互換用)
PALETTE_OPTIONS: dict[str, str] = option_labels(Translator("ja"), "palette", PALETTE_VALUES)
DITHER_OPTIONS: dict[str, str] = option_labels(Translator("ja"), "dither", DITHER_VALUES)
DEVICE_OPTIONS: dict[str, str] = option_labels(Translator("ja"), "device", DEVICE_VALUES)


def resolve_device(device_text: str) -> str:
    """デバイス表示文字列 (どの言語でも可) から convert.py 用の値を判定する。"""
    return _all_language_labels("device", DEVICE_VALUES).get(device_text, "auto")


def build_converter_args(
    video: Path, output: Path, ffmpeg: str, width: int, height: int, fps: float,
    palette: str, dither_method: str, keyframe_interval: int, device: str,
    perceptual: bool, duration: float | None, language: str = "en",
    mode: str = DEFAULT_MODE, depth: int = DEFAULT_DEPTH, anamorphic: bool = False,
) -> list[str]:
    """convert.py に渡す引数 (起動コマンド部分を除く)。.vox は立体アニメとして変換する。
    anamorphic なら、立体 (立体のモードと .vox) を視点に合わせただまし絵で表示する (平面スクリーンには使わない)。"""
    if Path(video).suffix.lower() == VOX_SUFFIX:
        source = ["--input-vox", str(video), "--mode", "vox"]
        three_d = True
    else:
        source = ["--input-video", str(video), "--mode", "inflate" if mode in PERSON_MODES else mode]
        three_d = mode != "screen"
        if three_d:
            source += ["--depth", str(depth)]
    if anamorphic and three_d:
        source += ["--view", "anamorphic"]
    args = [
        *source,
        "--ffmpeg", ffmpeg,
        "--fps", str(float(fps)),
        "--output", str(output), "--width", str(width), "--height", str(height),
        "--palette", palette,
        "--dither-method", dither_method,
        "--keyframe-interval", str(keyframe_interval),
        "--device", device,
        "--lang", language,
    ]
    if not perceptual:
        args.append("--no-perceptual")
    if duration is not None:
        args.extend(["--duration", str(duration)])
    return args


def write_vox_thumbnail(vox_path: Path, png_path: Path, size: int = 256, palette: str = "auto") -> None:
    """MagicaVoxel の最初のフレームを斜め上から見た立体の画像をサムネイルにする (ブラウザ版と共通)。"""
    Path(png_path).write_bytes(vox_thumbnail_png(vox_path, size, palette))


def resolve_palette(pal_text: str) -> str:
    """パレット表示文字列 (どの言語でも可) から convert.py 用の引数名を判定する。"""
    labels = _all_language_labels("palette", PALETTE_VALUES)
    if pal_text in labels:
        return labels[pal_text]
    if pal_text.startswith("自動"):
        return "auto"
    elif pal_text.startswith("全ブロック"):
        return "all"
    elif pal_text.startswith("全39"):
        return "full"
    elif pal_text.startswith("拡張"):
        return "expanded"
    else:
        return "concrete"


def resolve_dither(dither_text: str) -> str:
    """ディザ表示文字列 (どの言語でも可) から convert.py 用の引数名を判定する。"""
    labels = _all_language_labels("dither", DITHER_VALUES)
    if dither_text in labels:
        return labels[dither_text]
    if dither_text.startswith("Floyd"):
        return "floyd"
    elif dither_text.startswith("Atkinson"):
        return "atkinson"
    elif dither_text.startswith("Burkes"):
        return "burkes"
    elif dither_text.startswith("Sierra"):
        return "sierra"
    elif dither_text.startswith("Ordered"):
        return "ordered"
    elif dither_text.startswith("Blue Noise"):
        return "blue_noise"
    else:
        return "none"


class PackBuilderApp(tk.Tk):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        self.settings = load_settings()
        self.tr = Translator(language or initial_language(self.settings))
        self.title(self.tr("app.title"))
        self.minsize(780, 700)
        self.columnconfigure(0, weight=1)
        self.messages = queue.Queue()
        self.building = False

        self.output_var = tk.StringVar(value=str(default_output_path()))
        self.pack_name_var = tk.StringVar(value="Block Video Player")
        self.namespace_var = tk.StringVar(value="badapple")
        self.width_var = tk.IntVar(value=64)
        self.height_var = tk.IntVar(value=64)
        self.fps_var = tk.IntVar(value=10)
        self.addon_lang_var = tk.StringVar()
        self.duration_var = tk.StringVar(value="")
        self.keyframe_interval_var = tk.IntVar(value=30)
        self.perceptual_var = tk.BooleanVar(value=True)
        # コンボボックスは表示名を持つので、言語切替時に値から表示名を作り直す
        self.language_var = tk.StringVar()
        self.quality_var = tk.StringVar()
        self.palette_var = tk.StringVar()
        self.dither_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.mode_var = tk.StringVar()
        self.depth_var = tk.IntVar(value=DEFAULT_DEPTH)
        # 立体を、見ている人の目の位置に合わせて床と壁に描くだまし絵にする
        self.anamorphic_var = tk.BooleanVar(value=False)
        # MMD を高解像度で (エクストリーム: 人物の高さの上限を上げる。重い)
        self.mmd_extreme_var = tk.BooleanVar(value=False)
        # MMD のモデル (.pmx のパス) → 踊り (.vmd のパス)
        self.mmd_motions: dict[str, str] = {}
        # だまし絵の後ろの壁に流す背景の映像 (空なら壁は単色)
        self.stage_background_var = tk.StringVar(value="")
        self.preset_key = DEFAULT_PRESET
        # 最後に作ったアドオン (保存先を開く・Minecraft に読み込むボタン用) と、はじめてガイドの画面
        self.building_output: Path | None = None
        self.last_output: Path | None = None
        self.wizard = None
        self._set_option_labels(DEFAULT_PALETTE, DEFAULT_DITHER, DEFAULT_DEVICE, DEFAULT_MODE)
        self.namespace_var.trace_add("write", lambda *_: self._update_command_hint())
        for variable in (self.width_var, self.height_var, self.depth_var, self.anamorphic_var,
                         self.stage_background_var):
            variable.trace_add("write", lambda *_: self._update_load_hint())

        self._build_ui()
        self._apply_quality_preset()
        self.after(100, self._drain_messages)
        # 初めて起動したときは、はじめてガイドを開く
        if not self.settings.get("wizard_shown"):
            self.settings["wizard_shown"] = True
            save_settings(self.settings)
            self.after(300, self._open_wizard)

    # --- 言語 ---
    def _preset_labels(self) -> dict[str, str]:
        return {self.tr(f"preset.{key}"): key for key in QUALITY_PRESETS}

    def _selected_values(self) -> tuple[str, str, str, str]:
        """現在の言語の表示名から (パレット, ディザ, デバイス, 表示モード) の値を得る。"""
        palette = option_labels(self.tr, "palette", PALETTE_VALUES).get(self.palette_var.get(), DEFAULT_PALETTE)
        dither = option_labels(self.tr, "dither", DITHER_VALUES).get(self.dither_var.get(), DEFAULT_DITHER)
        device = option_labels(self.tr, "device", DEVICE_VALUES).get(self.device_var.get(), DEFAULT_DEVICE)
        mode = option_labels(self.tr, "mode", MODE_VALUES).get(self.mode_var.get(), DEFAULT_MODE)
        return palette, dither, device, mode

    def _on_mode_selected(self) -> None:
        """表示モードを選び直したとき、立体ならディザを「なし」にする (dither_after_mode_change)。"""
        palette, dither, device, mode = self._selected_values()
        old_mode = getattr(self, "_last_mode", DEFAULT_MODE)
        new_dither, self._dither_auto_set = dither_after_mode_change(
            old_mode, mode, dither, getattr(self, "_dither_auto_set", False))
        self._last_mode = mode
        if new_dither != dither:
            self.dither_var.set(self.tr(f"dither.{new_dither}"))
        self._update_load_hint()

    def _addon_language(self) -> str | None:
        """ゲーム内の表示言語を固定するなら GUI の言語コード、各プレイヤーの言語なら None。"""
        choice = option_labels(self.tr, "addon_lang", ADDON_LANG_VALUES).get(self.addon_lang_var.get(), "all")
        self._addon_lang_choice = choice
        return self.tr.language if choice == "fixed" else None

    def _set_option_labels(self, palette: str, dither: str, device: str, mode: str = DEFAULT_MODE) -> None:
        self.palette_var.set(self.tr(f"palette.{palette}"))
        self.dither_var.set(self.tr(f"dither.{dither}"))
        self.device_var.set(self.tr(f"device.{device}"))
        self.mode_var.set(self.tr(f"mode.{mode}"))
        self._last_mode = mode
        self.addon_lang_var.set(self.tr(f"addon_lang.{getattr(self, '_addon_lang_choice', 'all')}"))
        self.quality_var.set(self.tr(f"preset.{self.preset_key}"))
        self.language_var.set(dict(available_languages())[self.tr.language])

    def change_language(self, language: str) -> None:
        """表示言語を切り替える。入力内容・動画リスト・ログは引き継ぐ。"""
        if language == self.tr.language or self.building:
            return
        values = self._selected_values()
        videos = [self.video_tree.item(iid)["values"] for iid in self.video_tree.get_children()]
        log_text = self.log.get("1.0", "end-1c")

        self.tr = Translator(language)
        self._set_option_labels(*values)
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
        for row in videos:
            self.video_tree.insert("", "end", values=row)
        if log_text:
            self._append_log(log_text)
        self.title(self.tr("app.title"))

        self.settings["language"] = language
        save_settings(self.settings)

    def _on_language_selected(self) -> None:
        names = {name: code for code, name in available_languages()}
        self.change_language(names.get(self.language_var.get(), self.tr.language))

    def _build_ui(self) -> None:
        tr = self.tr
        pad = {"padx": 10, "pady": 4}

        # --- 言語選択 ---
        top_bar = ttk.Frame(self)
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        top_bar.columnconfigure(0, weight=1)
        ttk.Button(top_bar, text=tr("wizard.open"), command=self._open_wizard).grid(row=0, column=0, sticky="w")
        ttk.Label(top_bar, text=tr("language.label")).grid(row=0, column=1, padx=(0, 6))
        language_box = ttk.Combobox(
            top_bar, textvariable=self.language_var, state="readonly", width=20,
            values=tuple(name for _code, name in available_languages()),
        )
        language_box.grid(row=0, column=2)
        language_box.bind("<<ComboboxSelected>>", lambda _event: self._on_language_selected())
        self.language_box = language_box

        # --- 動画リストセクション ---
        video_frame = ttk.LabelFrame(self, text=tr("videos.frame"))
        video_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(6, 4))
        video_frame.columnconfigure(0, weight=1)
        video_frame.rowconfigure(0, weight=1)

        cols = ("video_id", "file_path", "thumb_sec")
        self.video_tree = ttk.Treeview(video_frame, columns=cols, show="headings", height=5)
        self.video_tree.heading("video_id", text=tr("videos.col_id"))
        self.video_tree.heading("file_path", text=tr("videos.col_path"))
        self.video_tree.heading("thumb_sec", text=tr("videos.col_thumb"))
        self.video_tree.column("video_id", width=120, minwidth=80)
        self.video_tree.column("file_path", width=400, minwidth=200)
        self.video_tree.column("thumb_sec", width=100, minwidth=60)
        self.video_tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=6)

        tree_scroll = ttk.Scrollbar(video_frame, orient="vertical", command=self.video_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 10))
        self.video_tree.configure(yscrollcommand=tree_scroll.set)

        btn_frame = ttk.Frame(video_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
        ttk.Button(btn_frame, text=tr("videos.add"), command=self._add_videos).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text=tr("videos.remove"), command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(btn_frame, text=tr("videos.edit_id"), command=self._edit_video_id).pack(side="left", padx=4)
        ttk.Button(btn_frame, text=tr("videos.edit_thumb"), command=self._edit_thumb_sec).pack(side="left", padx=4)

        # --- 出力先 ---
        out_frame = ttk.Frame(self)
        out_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        out_frame.columnconfigure(1, weight=1)
        ttk.Label(out_frame, text=tr("output.label")).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(out_frame, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(out_frame, text=tr("output.browse"), command=self._select_output).grid(row=0, column=2)

        # --- パック基本設定 ---
        pack_settings = ttk.LabelFrame(self, text=tr("pack.frame"))
        pack_settings.grid(row=3, column=0, sticky="ew", padx=10, pady=4)
        pack_settings.columnconfigure(1, weight=1)
        ttk.Label(pack_settings, text=tr("pack.name")).grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        ttk.Entry(pack_settings, textvariable=self.pack_name_var).grid(row=0, column=1, padx=(0, 10), pady=6, sticky="ew")
        ttk.Label(pack_settings, text=tr("pack.namespace")).grid(row=1, column=0, padx=(10, 4), pady=(0, 6), sticky="w")
        ttk.Entry(pack_settings, textvariable=self.namespace_var).grid(row=1, column=1, padx=(0, 10), pady=(0, 6), sticky="ew")
        self.command_hint = ttk.Label(pack_settings, text="", foreground="#555555")
        self.command_hint.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 6), sticky="w")
        self._update_command_hint()
        # ゲーム内のメッセージ・リモコン画面の言語
        ttk.Label(pack_settings, text=tr("pack.addon_language")).grid(row=3, column=0, padx=(10, 4), pady=(0, 6), sticky="w")
        addon_lang_box = ttk.Combobox(
            pack_settings, textvariable=self.addon_lang_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "addon_lang", ADDON_LANG_VALUES)),
        )
        addon_lang_box.grid(row=3, column=1, padx=(0, 10), pady=(0, 6), sticky="w")
        # 選んだ値を覚えておく (表示言語を切り替えて画面を作り直しても選択を保つ)
        addon_lang_box.bind("<<ComboboxSelected>>", lambda _event: self._addon_language())

        # --- 変換・再生設定 ---
        settings = ttk.LabelFrame(self, text=tr("settings.frame"))
        settings.grid(row=4, column=0, sticky="ew", padx=10, pady=4)
        for index, (key, variable, maximum) in enumerate((
            ("settings.width", self.width_var, 512),
            ("settings.height", self.height_var, 512),
        )):
            ttk.Label(settings, text=tr(key)).grid(row=0, column=index * 2, padx=(10, 4), pady=6)
            ttk.Spinbox(settings, from_=1, to=maximum, textvariable=variable, width=7).grid(
                row=0, column=index * 2 + 1, padx=(0, 10), pady=6
            )
        # fps: 1〜20 のスライダー (整数に丸める) と、選んだ値の表示
        ttk.Label(settings, text=tr("settings.fps")).grid(row=0, column=4, padx=(10, 4), pady=6)
        fps_box = ttk.Frame(settings)
        fps_box.grid(row=0, column=5, padx=(0, 10), pady=6, sticky="w")
        self.fps_label = ttk.Label(fps_box, width=7)
        ttk.Scale(
            fps_box, from_=MIN_FPS, to=MAX_FPS, orient="horizontal", length=110, variable=self.fps_var,
            command=lambda _value: self._on_fps_changed(),
        ).grid(row=0, column=0)
        self.fps_label.grid(row=0, column=1, padx=(4, 0))
        self._on_fps_changed()
        ttk.Label(settings, text=tr("settings.keyframe")).grid(row=0, column=6, padx=(10, 4), pady=6)
        ttk.Spinbox(settings, from_=0, to=300, textvariable=self.keyframe_interval_var, width=7).grid(row=0, column=7, padx=(0, 10), pady=6)

        ttk.Label(settings, text=tr("settings.preset")).grid(row=1, column=0, padx=(10, 4), pady=(0, 6))
        preset = ttk.Combobox(
            settings, textvariable=self.quality_var, state="readonly", width=28,
            values=tuple(self._preset_labels()),
        )
        preset.grid(row=1, column=1, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w")
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_quality_preset())

        # パレット選択
        ttk.Label(settings, text=tr("settings.palette")).grid(row=1, column=3, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.palette_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "palette", PALETTE_VALUES)),
        ).grid(row=1, column=4, columnspan=4, padx=(0, 10), pady=(0, 6), sticky="w")

        # ディザリング選択 (全7種対応)
        ttk.Label(settings, text=tr("settings.dither")).grid(row=2, column=0, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.dither_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "dither", DITHER_VALUES)),
        ).grid(row=2, column=1, columnspan=3, padx=(0, 10), pady=(0, 6), sticky="w")
        # 動画の縦横比に合わせた幅・高さにする (黒帯のぶんのブロックを無駄にしない)
        ttk.Button(settings, text=tr("settings.fit_aspect"), command=self._fit_to_aspect).grid(
            row=2, column=4, columnspan=4, padx=(10, 10), pady=(0, 6), sticky="w"
        )

        # 知覚最適化(エッジ減衰)オプション
        ttk.Checkbutton(
            settings, text=tr("settings.perceptual"), variable=self.perceptual_var
        ).grid(row=3, column=0, columnspan=3, padx=(10, 4), pady=(0, 6), sticky="w")

        # 変換デバイス (GPU: CUDA / ROCm / DirectML)
        ttk.Label(settings, text=tr("settings.device")).grid(row=3, column=3, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.device_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "device", DEVICE_VALUES)),
        ).grid(row=3, column=4, columnspan=4, padx=(0, 10), pady=(0, 6), sticky="w")

        # 表示モード (平面 / 立体) と立体の奥行き
        ttk.Label(settings, text=tr("settings.mode")).grid(row=4, column=0, padx=(10, 4), pady=(0, 6))
        mode_box = ttk.Combobox(
            settings, textvariable=self.mode_var, state="readonly", width=34,
            values=tuple(option_labels(tr, "mode", MODE_VALUES)),
        )
        mode_box.grid(row=4, column=1, columnspan=3, padx=(0, 10), pady=(0, 6), sticky="w")
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self._on_mode_selected())
        ttk.Label(settings, text=tr("settings.depth")).grid(row=4, column=4, padx=(10, 4), pady=(0, 6))
        ttk.Spinbox(settings, from_=2, to=MAX_DEPTH, textvariable=self.depth_var, width=7).grid(
            row=4, column=5, padx=(0, 10), pady=(0, 6), sticky="w"
        )
        ttk.Checkbutton(settings, text=tr("settings.anamorphic"), variable=self.anamorphic_var).grid(
            row=4, column=6, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w"
        )
        # だまし絵の背景の映像
        ttk.Label(settings, text=tr("settings.stage_background")).grid(row=5, column=0, padx=(10, 4), pady=(0, 6))
        ttk.Entry(settings, textvariable=self.stage_background_var).grid(
            row=5, column=1, columnspan=5, padx=(0, 6), pady=(0, 6), sticky="ew"
        )
        ttk.Button(settings, text=tr("settings.stage_background_browse"), command=self._select_stage_background).grid(
            row=5, column=6, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w"
        )
        ttk.Checkbutton(
            settings, text=tr("settings.mmd_extreme", height=MMD_EXTREME_MAX_HEIGHT, stage=MMD_EXTREME_STAGE_MAX_HEIGHT),
            variable=self.mmd_extreme_var,
        ).grid(row=6, column=0, columnspan=8, padx=10, pady=(0, 6), sticky="w")
        # 再生負荷の目安 (幅・高さ・fps・表示モード・奥行きを変えるたびに更新)
        self.load_label = ttk.Label(settings, wraplength=720)
        self.load_label.grid(row=7, column=0, columnspan=8, padx=10, pady=(0, 6), sticky="w")
        self._update_load_hint()

        # --- 説明文 ---
        ttk.Label(self, text=tr("info.description"), wraplength=740).grid(row=5, column=0, sticky="w", **pad)

        version_info = tr("info.version", version=version_text())
        if tr.language == "ja":
            # リリースノートは日本語のみ
            version_info += "  |  " + " / ".join(RELEASE_NOTES[:2])
        ttk.Label(self, text=version_info, wraplength=740, foreground="#555555").grid(row=6, column=0, sticky="w", **pad)

        build_bar = ttk.Frame(self)
        build_bar.grid(row=7, column=0, pady=6)
        self.build_button = ttk.Button(build_bar, text=tr("build.button"), command=self._start_build)
        self.build_button.grid(row=0, column=0, padx=(0, 12))
        # できたアドオン: 保存先を開く・Minecraft に読み込む・再生のしかた (作り終わるまで押せない)
        after_state = "normal" if self.last_output else "disabled"
        self.after_buttons = [
            ttk.Button(build_bar, text=tr("after.open_folder"), command=self.open_output_folder, state=after_state),
            ttk.Button(build_bar, text=tr("after.import"), command=self.import_output, state=after_state),
            ttk.Button(build_bar, text=tr("after.guide_button"), command=self._show_play_guide, state=after_state),
        ]
        for column, button in enumerate(self.after_buttons, start=1):
            button.grid(row=0, column=column, padx=3)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=8, column=0, sticky="ew", padx=10, pady=4)

        ttk.Label(self, text=tr("log.label")).grid(row=9, column=0, sticky="w", padx=10, pady=(4, 0))
        self.log = tk.Text(self, height=12, state="disabled", wrap="word")
        self.log.grid(row=10, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.rowconfigure(10, weight=1)

        if self.building:
            self.build_button.configure(state="disabled")
            language_box.configure(state="disabled")

    # --- 動画リスト操作 ---
    def _add_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title=self.tr("dialog.select_videos_title"),
            filetypes=[
                (self.tr("dialog.video_files"), "*.mp4 *.mkv *.avi *.mov *.webm"),
                (self.tr("dialog.vox_files"), "*.vox"),
                (self.tr("dialog.mmd_files"), "*.pmx"),
                (self.tr("dialog.all_files"), "*.*"),
            ],
        )
        for path in paths:
            p = Path(path)
            if p.suffix.lower() == PMX_SUFFIX:
                # MMD のモデルには踊り (.vmd) が要る
                motion = filedialog.askopenfilename(
                    title=self.tr("dialog.select_motion", model=p.name),
                    filetypes=[(self.tr("dialog.vmd_files"), "*.vmd"), (self.tr("dialog.all_files"), "*.*")],
                )
                if not motion:
                    continue
                self.mmd_motions[str(p)] = motion
            self.add_video_path(p)

    def add_video_path(self, path: str | Path) -> str:
        """動画リストに 1 本足し、付けた動画 ID を返す (はじめてガイドからも使う)。"""
        p = Path(path)
        video_id = re.sub(r"[^a-z0-9_]", "_", p.stem.lower())
        existing_ids = [self.video_tree.item(iid)["values"][0] for iid in self.video_tree.get_children()]
        if video_id in existing_ids:
            suffix = 2
            while f"{video_id}_{suffix}" in existing_ids:
                suffix += 1
            video_id = f"{video_id}_{suffix}"
        self.video_tree.insert("", "end", values=(video_id, str(p), "0"))
        # 最初の 1 本を選んでおく (「動画の縦横比に合わせる」がそのまま使える)
        if len(self.video_tree.get_children()) == 1:
            self.video_tree.selection_set(self.video_tree.get_children()[0])
        return video_id

    def _remove_selected(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.select_remove"))
            return
        for iid in selected:
            self.video_tree.delete(iid)

    def _edit_video_id(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.select_edit_id"))
            return
        iid = selected[0]
        values = self.video_tree.item(iid)["values"]
        from tkinter import simpledialog
        new_id = simpledialog.askstring(
            self.tr("edit_id.title"), self.tr("edit_id.prompt", current=values[0]), initialvalue=values[0], parent=self
        )
        if new_id and re.fullmatch(r"[a-z0-9_]+", new_id):
            self.video_tree.item(iid, values=(new_id, values[1], values[2]))
        elif new_id:
            messagebox.showerror(self.tr("edit_id.invalid_title"), self.tr("edit_id.invalid"))

    def _edit_thumb_sec(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.select_edit_thumb"))
            return
        iid = selected[0]
        values = self.video_tree.item(iid)["values"]
        from tkinter import simpledialog
        new_sec = simpledialog.askstring(
            self.tr("edit_thumb.title"), self.tr("edit_thumb.prompt", current=values[2]), initialvalue=str(values[2]), parent=self
        )
        if new_sec is not None:
            try:
                float(new_sec)
                self.video_tree.item(iid, values=(values[0], values[1], new_sec))
            except ValueError:
                messagebox.showerror(self.tr("edit_thumb.invalid_title"), self.tr("edit_thumb.invalid"))

    def _update_command_hint(self) -> None:
        if not hasattr(self, "command_hint") or not self.command_hint.winfo_exists():
            return
        namespace = self.namespace_var.get().strip() or self.tr("pack.namespace_placeholder")
        self.command_hint.configure(text=self.tr("pack.hint", ns=namespace))

    def _apply_quality_preset(self) -> None:
        self.preset_key = self._preset_labels().get(self.quality_var.get(), self.preset_key)
        width, height, fps = QUALITY_PRESETS[self.preset_key]
        self.width_var.set(width)
        self.height_var.set(height)
        self.fps_var.set(fps)
        self._on_fps_changed()

    def _selected_video_path(self) -> str | None:
        """選択中の動画 (無ければ一覧の先頭から) のパス。.vox は縦横比の対象外。"""
        items = list(self.video_tree.selection()) or list(self.video_tree.get_children())
        for iid in items:
            path = str(self.video_tree.item(iid)["values"][1])
            if not path.lower().endswith((VOX_SUFFIX, PMX_SUFFIX)):
                return path
        return None

    def _fit_to_aspect(self) -> None:
        """動画の縦横比を読み、今の幅・高さの長い辺はそのままで黒帯の出ない大きさにする。"""
        path = self._selected_video_path()
        if path is None:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.fit_aspect_no_video"))
            return
        size = probe_video_size(path)
        if size is None:
            messagebox.showerror(self.tr("error.probe_title"), self.tr("error.probe", path=path))
            return
        try:
            long_side = max(int(self.width_var.get()), int(self.height_var.get()))
        except (tk.TclError, ValueError):
            long_side = QUALITY_PRESETS[DEFAULT_PRESET][0]
        width, height = suggest_size(*size, long_side)
        self.width_var.set(width)
        self.height_var.set(height)
        self.messages.put(self.tr("settings.fit_aspect_done", src_w=size[0], src_h=size[1], width=width, height=height))

    def _update_load_hint(self) -> None:
        if not hasattr(self, "load_label") or not self.load_label.winfo_exists():
            return
        try:
            width, height = int(self.width_var.get()), int(self.height_var.get())
            fps = int(round(float(self.fps_var.get())))
            depth = int(self.depth_var.get())
        except (tk.TclError, ValueError):
            self.load_label.configure(text="")
            return
        mode = self._selected_values()[3]
        load = estimate_load(width, height, fps, mode, depth, self.anamorphic_var.get(),
                             bool(self.stage_background_var.get().strip()))
        self.load_label.configure(text=self.tr(
            "settings.load", blocks=f"{load['blocks_per_second']:,}", ms=f"{load['ms_per_tick']:.0f}",
            level=self.tr(f"load.{load['level']}"),
        ))

    def _on_fps_changed(self) -> None:
        """スライダーの値を 1〜20 の整数に丸めて表示する。"""
        try:
            fps = int(round(float(self.fps_var.get())))
        except (tk.TclError, ValueError):
            fps = 10
        fps = max(MIN_FPS, min(MAX_FPS, fps))
        if self.fps_var.get() != fps:
            self.fps_var.set(fps)
        if hasattr(self, "fps_label"):
            self.fps_label.configure(text=self.tr("settings.fps_value", fps=fps))
        self._update_load_hint()

    def _select_stage_background(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("settings.stage_background"),
            filetypes=[(self.tr("dialog.video_files"), "*.mp4 *.mkv *.avi *.mov *.webm"), (self.tr("dialog.all_files"), "*.*")],
        )
        if path:
            self.stage_background_var.set(path)

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title=self.tr("dialog.save_title"),
            defaultextension=".mcaddon",
            filetypes=[("Minecraft Addon", "*.mcaddon")],
        )
        if path:
            self.output_var.set(path)

    def _start_build(self) -> None:
        video_entries = []
        for iid in self.video_tree.get_children():
            vals = self.video_tree.item(iid)["values"]
            video_entries.append({
                "video_id": str(vals[0]),
                "file_path": str(vals[1]),
                "thumb_sec": str(vals[2]),
            })
            if str(vals[1]) in self.mmd_motions:
                video_entries[-1]["motion"] = self.mmd_motions[str(vals[1])]

        if not video_entries:
            messagebox.showerror(self.tr("error.no_videos_title"), self.tr("error.no_videos"))
            return

        output = Path(self.output_var.get().strip())
        if not output.name:
            messagebox.showerror(self.tr("error.no_output_title"), self.tr("error.no_output"))
            return
        if output.suffix.lower() != ".mcaddon":
            output = output.with_suffix(".mcaddon")
            self.output_var.set(str(output))

        for entry in video_entries:
            if not Path(entry["file_path"]).is_file():
                messagebox.showerror(self.tr("error.file_missing_title"), self.tr("error.file_missing", path=entry["file_path"]))
                return

        palette, dither_method, device, mode = self._selected_values()
        try:
            width = int(self.width_var.get())
            height = int(self.height_var.get())
            fps = int(round(float(self.fps_var.get())))
            # 奥行きは立体のときだけ使う (平面では欄が空でも作れる)
            depth = int(self.depth_var.get()) if mode in VOLUME_MODES else DEFAULT_DEPTH
            duration = float(self.duration_var.get()) if self.duration_var.get().strip() else None
        except (tk.TclError, ValueError):
            messagebox.showerror(self.tr("error.settings_title"), self.tr("error.settings"))
            return
        errors = validate_settings(width, height, fps, duration, mode, depth)
        if errors:
            text = "\n".join(self.tr(key, max_size=MAX_SCREEN_SIZE, min_fps=MIN_FPS, max_fps=MAX_FPS,
                                      max_depth=MAX_DEPTH, max_height=MAX_VOLUME_HEIGHT) for key in errors)
            messagebox.showerror(self.tr("error.settings_title"), text)
            return
        # 重い設定は、そのまま作るか確かめる (ゲームが重くなる・映像が遅れる)
        load = estimate_load(width, height, fps, mode, depth, self.anamorphic_var.get(),
                             bool(self.stage_background_var.get().strip()))
        if load["level"] == "heavy" and not messagebox.askyesno(
            self.tr("warn.heavy_title"), self.tr("warn.heavy", ms=f"{load['ms_per_tick']:.0f}")
        ):
            return

        pack_name = self.pack_name_var.get().strip()
        namespace = self.namespace_var.get().strip()

        if not pack_name:
            messagebox.showerror(self.tr("error.pack_name_title"), self.tr("error.pack_name"))
            return
        if not re.fullmatch(r"[a-z0-9_.\-]+", namespace) or namespace == "minecraft":
            messagebox.showerror(self.tr("error.namespace_title"), self.tr("error.namespace"))
            return

        self.building = True
        self.building_output = output
        self.build_button.configure(state="disabled")
        self.language_box.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(
            target=self._build_pack,
            args=(video_entries, output, pack_name, namespace, width, height, fps, duration, palette, dither_method,
                  device, mode, depth, self._addon_language(), self.anamorphic_var.get(),
                  self.stage_background_var.get().strip() or None, self.mmd_extreme_var.get()),
            daemon=True,
        ).start()

    def _mmd_to_vox(self, model: Path, motion: str | None, output: Path, height: int, fps: float,
                    duration: float | None) -> Path:
        """MMD のモデル (.pmx) と踊り (.vmd) から、立体のアニメ (.vox) を作る (mvcodec/mmd.py)。"""
        from mvcodec.mmd import mmd_to_vox
        if not motion or not Path(motion).is_file():
            raise RuntimeError(self.tr("build.mmd_no_motion", path=model))
        self.messages.put(self.tr("build.mmd", height=height))

        def progress(done, total):
            if done % 50 == 0 or done == total:
                self.messages.put(self.tr("build.mmd_progress", done=done, total=total))

        mmd_to_vox(model, motion, output, height=height, fps=fps, duration=duration, progress=progress)
        return output

    def _split_person(self, video: Path, work: Path, width: int, height: int, fps: float, duration: float | None,
                      ffmpeg: str, model: str = "photo") -> tuple[Path, Path]:
        """動画から人物を AI で切り抜き、(人物の範囲の動画, 人物を消した背景の動画) を作る (mvcodec/person.py)。
        model は "photo" (実写向き) か "anime" (アニメ・イラスト向き)。

        切り抜きは別のプロセスで行う (AI のモデルは GPU のメモリと RAM を大きく使い、同じプロセスだと作り終わっても
        解放されない。メモリ不足で落ちても GUI は止まらない)。空きメモリから一番重い段階を選び、メモリ不足で止まったら
        軽い段階でやり直す (mvcodec/person.py の profile_ladder)。入力の動画を読めない (終了コード 4) ときは、軽い段階にしても
        直らないので、やり直さずにその理由を知らせる。モデルをダウンロードできない (終了コード 5) ときは、アニメ向けなら
        gpu・cpu を飛ばして photo の段階 (別のモデル。写真向けの DeepLab) で続け、photo の段階でもだめなら理由を知らせる。

        子プロセスの出力すべてと統計は、ログのファイル (new_person_log。やり直した分も続けて 1 つ) に書く。全部の段階でだめなら、
        そのログの場所を失敗の知らせに書く。"""
        from mvcodec.person import EXIT_INPUT_ERROR, EXIT_MODEL_ERROR, EXIT_OUT_OF_MEMORY, profile_ladder
        from mvcodec.resources import GIB, gpu_memory, system_memory

        self.messages.put(self.tr("build.person_anime" if model == "anime" else "build.person"))
        ram, vram = system_memory(), gpu_memory()
        ladder = profile_ladder(model, ram and ram[1], vram and vram[1])
        self.messages.put(self.tr(
            "build.memory_check", ram=f"{ram[1] / GIB:.1f}" if ram else "?", vram=f"{vram[1] / GIB:.1f}" if vram else "?",
            profile=self.tr(f"profile.{ladder[0]}")))
        work.mkdir(parents=True, exist_ok=True)
        mask, background = work / "mask.mp4", work / "background.mp4"
        # 子プロセスの出力すべてと統計を、やり直した分も続けて 1 つのログに書く (失敗の知らせにその場所を出す)
        log = self.person_log = new_person_log()
        with open_log(log) as write_log:
            write_log(f"{datetime.now():%Y-%m-%d %H:%M:%S} person cutout ({model}): {video}")
        why = "auto"   # 段階を選んだ理由 (最初は空きメモリから。やり直しはその前の段階がどうなったか)
        # 測った空きメモリは子プロセスに渡す (子で測り直すと、VRAM は約 2.6 秒かかる)。測れなかった (-1) ときも測り直さない
        free = ["--free-ram", str(ram[1] if ram else -1), "--free-vram", str(vram[1] if vram else -1)]
        step = 0
        while step < len(ladder):
            profile = ladder[step]
            argv = [str(video), str(mask), str(background), "--fps", str(fps), "--ffmpeg", ffmpeg,
                    "--model", model, "--profile", profile, "--profile-reason", why, *free]
            if duration is not None:
                argv += ["--duration", str(duration)]
            if log is not None:   # 統計はログのフォルダにも書く (標準出力を読めなくても残る)
                argv += ["--stats", str(log.parent)]
            code, output = self._person_process(argv)
            if code == 0:
                return mask, background
            if "No module named" in output or "ImportError" in output:
                key = "build.person_anime_missing" if model == "anime" else "build.person_missing"
                raise RuntimeError(self.tr(key, error=output.strip().splitlines()[-1] if output.strip() else code))
            if code in (EXIT_INPUT_ERROR, EXIT_MODEL_ERROR):
                key = "build.person_input_error" if code == EXIT_INPUT_ERROR else "build.person_model_error"
                prefix = "input error: " if code == EXIT_INPUT_ERROR else "model error: "
                lines = [line for line in output.splitlines() if line.startswith(prefix)]
                detail = lines[-1][len(prefix):] if lines else output.strip()[-400:]
                if code == EXIT_MODEL_ERROR and model == "anime" and profile != "photo" and "photo" in ladder:
                    # アニメ向けのモデル (Hugging Face) を取得できなくても、photo の段階は別のモデル (写真向け) なので動く
                    step, why = ladder.index("photo"), "retry_model"
                    self.messages.put(self.tr("build.person_retry_model", detail=detail))
                    continue
                raise RuntimeError(self.tr(key, detail=detail))
            if step + 1 < len(ladder):
                reason = "memory" if code == EXIT_OUT_OF_MEMORY else "crash"
                why = f"retry_{reason}"
                self.messages.put(self.tr(f"build.person_retry_{reason}", profile=self.tr(f"profile.{ladder[step + 1]}")))
            step += 1
        raise RuntimeError(self.tr("build.person_failed", detail=output.strip()[-400:], log=log if log is not None else "-"))

    def _person_process(self, argv: list[str]) -> tuple[int, str]:
        """人物の切り抜きを別のプロセスで行い、(終了コード, 出力の最後の部分) を返す。進み具合を処理ログへ流す。

        出力はすべて person_log (_split_person が作る) に書く。子プロセスが最後に出す「stats <JSON>」の行は、そのまま
        流さずに、終了コード 0 のとき 1〜2 行のまとめと (条件に当てはまれば) 注意にして流す (person_summary_lines)。"""
        command = [*person_command(), *argv]
        self.messages.put("$ " + subprocess.list2cmdline(command))
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", **subprocess_kwargs(),
        )
        tail: list[str] = []
        last_download = -1
        stats = None
        log = getattr(self, "person_log", None)
        with open_log(log) as write_log:
            write_log("$ " + subprocess.list2cmdline(command))
            for raw in process.stdout:
                write_log(raw.rstrip("\r\n"))
                line = raw.strip()
                if not line:
                    continue
                words = line.split()
                if line.startswith("stats {"):   # 統計の JSON (1 行。文字列の中に空白がある)
                    try:
                        stats = json.loads(line[len("stats "):])
                        continue
                    except ValueError:
                        pass
                tail = (tail + [line])[-30:]
                if len(words) == 2 and words[1] == "frames" and words[0].isdigit():
                    if int(words[0]) % 50 == 0:
                        self.messages.put(self.tr("build.person_progress", count=int(words[0])))
                elif len(words) == 3 and words[0] == "download" and words[1].isdigit() and words[2].isdigit():
                    done, total = int(words[1]) >> 20, int(words[2]) >> 20
                    if done // 16 != last_download or done == total:
                        last_download = done // 16
                        self.messages.put(self.tr("build.model_download", done=done, total=total))
                elif words[0] == "profile":
                    self.messages.put(self.tr("build.person_profile", profile=self.tr(f"profile.{words[1]}")))
                elif words[0] == "sources":
                    pass   # まとめに入る (ログには書いた)
                elif "W:onnxruntime" not in line:
                    self.messages.put("  " + line)
            code = process.wait()
            write_log(f"exit code {code}")
        if code == 0 and isinstance(stats, dict):
            for text in person_summary_lines(self.tr, stats, log):
                self.messages.put(text)
        return code, "\n".join(tail)

    def _extract_audio(self, ffmpeg: str, video: Path, sounds_dir: Path) -> list[tuple[int, str, bytes]]:
        """音声を AUDIO_CHUNK_SECONDS 秒ごとの .ogg (44.1kHz ステレオ) に切り出す。無音の動画やエラーなら []。"""
        sounds_dir.mkdir(parents=True, exist_ok=True)
        self.messages.put(self.tr("build.audio", sec=AUDIO_CHUNK_SECONDS))
        try:
            self._run([
                ffmpeg, "-y", "-i", str(video),
                "-f", "segment", "-segment_time", str(AUDIO_CHUNK_SECONDS),
                "-vn", "-acodec", "libvorbis",
                "-ar", "44100", "-ac", "2", "-b:a", "192k",
                str(sounds_dir / "chunk_%d.ogg")
            ])
        except Exception as e:
            self.messages.put(self.tr("build.audio_skipped", error=e))
        return [(int(f.stem.split("_")[1]), "ogg", f.read_bytes()) for f in sounds_dir.glob("chunk_*.ogg")]

    def _convert_stage_background(
        self, source: Path, background_id: str, figure_js: str, temp: Path, ffmpeg: str, fps: float, palette: str,
        dither_method: str, device: str, duration: float | None,
    ) -> dict:
        """だまし絵の背景の映像を、後ろの壁と同じ大きさの平面の動画に変換する (画面を埋めるように切り取る)。"""
        if not source.is_file():
            raise RuntimeError(self.tr("build.missing_file", path=source))
        dims = frame_data_dims(figure_js)
        geom = stage_geometry(dims["width"], dims["height"], dims["depth"])
        self.messages.put(self.tr("build.stage_background", width=geom["stageWidth"], height=geom["wallHeight"]))
        sounds = self._extract_audio(ffmpeg, source, temp / "sounds" / background_id)
        output = temp / f"frames_{background_id}.js"
        argv = build_converter_args(
            source, output, ffmpeg, geom["stageWidth"], geom["wallHeight"], fps, palette, dither_method,
            self.keyframe_interval_var.get(), device, self.perceptual_var.get(), duration, self.tr.language,
        )
        self._run([*converter_command(), *argv, "--fit", "cover"])
        return {"id": background_id, "title": source.stem, "frames_js": output.read_text(encoding="utf-8"),
                "thumbnail": None, "sounds": sounds, "hidden": True}

    def _run(self, command: list[str]) -> None:
        self.messages.put("$ " + subprocess.list2cmdline(command))
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", **subprocess_kwargs(),
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.messages.put(line.rstrip())
        if process.wait() != 0:
            raise RuntimeError(self.tr("build.cmd_failed", code=process.returncode))

    def _build_pack(
        self, video_entries: list[dict], output: Path, pack_name: str, namespace: str,
        width: int, height: int, fps: float, duration: float | None, palette: str, dither_method: str,
        device: str, mode: str = DEFAULT_MODE, depth: int = DEFAULT_DEPTH, addon_language: str | None = None,
        anamorphic: bool = False, stage_background: str | None = None, mmd_extreme: bool = False,
    ) -> None:
        try:
            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                raise RuntimeError(self.tr("build.ffmpeg_missing"))
            required_files = [MANIFEST, MAIN_SCRIPT, CODEC_SCRIPT]
            if not getattr(sys, "frozen", False):
                required_files.append(CONVERTER)
            for required in required_files:
                if not required.is_file():
                    raise RuntimeError(self.tr("build.missing_file", path=required))

            with tempfile.TemporaryDirectory(prefix="block-video-player-") as temp_dir:
                temp = Path(temp_dir)
                videos = []
                total_videos = len(video_entries)
                for vi, entry in enumerate(video_entries, 1):
                    video = Path(entry["file_path"])
                    video_id = entry["video_id"]
                    thumb_sec = entry["thumb_sec"]

                    self.messages.put(self.tr("build.processing", index=vi, total=total_videos, id=video_id))

                    generated_data = temp / f"frames_{video_id}.js"
                    thumbnail = temp / f"thumb_{video_id}.png"

                    if video.suffix.lower() == PMX_SUFFIX:
                        # MMD: モデルと踊りから立体のアニメ (.vox) を作り、あとは .vox と同じに変換する
                        on_stage = entry.get("anamorphic", anamorphic)
                        extreme = entry.get("mmd_extreme", mmd_extreme)
                        mmd_height = min(height, mmd_height_limit(on_stage, extreme))
                        if extreme and on_stage:
                            self.messages.put(self.tr("build.mmd_extreme_stage", height=mmd_height,
                                                      ms=f"{mmd_stage_frame_ms(mmd_height):.0f}",
                                                      fps=mmd_stage_max_fps(mmd_height)))
                        elif extreme:
                            self.messages.put(self.tr("build.mmd_extreme", height=mmd_height))
                        video = self._mmd_to_vox(video, entry.get("motion"), temp / f"mmd_{video_id}.vox",
                                                 mmd_height, fps, duration)
                    is_vox = video.suffix.lower() == VOX_SUFFIX
                    # サムネイル生成 (.vox は立体を正面から見た画像)
                    if is_vox:
                        self.messages.put(self.tr("build.vox_preview"))
                        write_vox_thumbnail(video, thumbnail, palette=palette)
                    else:
                        self.messages.put(self.tr("build.thumbnail", sec=thumb_sec))
                        self._run([
                            ffmpeg, "-y", "-ss", str(thumb_sec), "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=256:256:force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2:black",
                            str(thumbnail),
                        ])
                    if not thumbnail.is_file() or thumbnail.stat().st_size == 0:
                        raise RuntimeError(self.tr("build.thumbnail_failed", id=video_id))

                    # だまし絵と背景の映像は、動画ごとの指定 (サンプルを作るスクリプト用) があればそれを使う
                    video_anamorphic = entry.get("anamorphic", anamorphic)
                    video_background = entry.get("stage_background", stage_background)

                    # だまし絵にする、動画から作る立体は縮める (舞台と背景の映像が立体に比例して広がり、重くなる)
                    video_width, video_height = width, height
                    if video_anamorphic and mode != "screen" and not is_vox:
                        video_width, video_height = stage_figure_size(width, height)
                        if (video_width, video_height) != (width, height):
                            self.messages.put(self.tr("build.stage_size", width=video_width, height=video_height,
                                                      max=STAGE_MAX_SIDE))
                    # 人物を切り抜く: 人物の範囲の動画を立体の形に使い、だまし絵なら人物を消した映像を背景にする
                    mask_video = None
                    if mode in PERSON_MODES and not is_vox:
                        mask_video, person_background = self._split_person(
                            video, temp / f"person_{video_id}", video_width, video_height, fps, duration, ffmpeg,
                            PERSON_MODES[mode])
                        if video_anamorphic and not video_background:
                            video_background = str(person_background)
                    # だまし絵の背景の映像 (立体のときだけ。人物の動画 ID + "_bg" の、一覧に出さない平面の動画にする)
                    three_d = is_vox or mode != "screen"
                    background_id = f"{video_id}_bg" if video_anamorphic and three_d and video_background else None
                    # 背景の映像があれば、その音声を鳴らす (人物の動画の音声は入れない)
                    sounds = [] if is_vox or background_id else self._extract_audio(
                        ffmpeg, video, temp / "sounds" / video_id)

                    # ブロックデータ変換
                    self.messages.put(self.tr("build.converting"))
                    argv = build_converter_args(
                        video, generated_data, ffmpeg, video_width, video_height, fps, palette, dither_method,
                        self.keyframe_interval_var.get(), device, self.perceptual_var.get(), duration,
                        self.tr.language, mode, depth, video_anamorphic,
                    )
                    if background_id:
                        argv += ["--stage-background", background_id]
                    if mask_video:
                        argv += ["--mask-video", str(mask_video)]
                    self._run([*converter_command(), *argv])
                    frames_js = generated_data.read_text(encoding="utf-8")
                    videos.append({
                        "id": video_id, "title": video.stem, "frames_js": frames_js,
                        "thumbnail": thumbnail.read_bytes(), "sounds": sounds, "width": video_width,
                        "height": video_height,
                    })
                    if background_id:
                        videos.append(self._convert_stage_background(
                            Path(video_background), background_id, frames_js, temp, ffmpeg, fps, palette,
                            dither_method, device, duration,
                        ))

                self.messages.put(self.tr("build.videos_index"))
                video_index_entries = build_addon(output, videos, pack_name, namespace, addon_language)

            video_names = ", ".join(e["id"] for e in video_index_entries)
            self.messages.put((
                "success",
                self.tr("build.success", output=output, videos=video_names, count=total_videos, ns=namespace),
            ))
        except Exception as error:
            self.messages.put(("error", str(error)))

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if isinstance(message, tuple):
                    self.building = False
                    self.progress.stop()
                    self.build_button.configure(state="normal")
                    self.language_box.configure(state="readonly")
                    kind, text = message
                    ok = kind == "success"
                    if ok:
                        self.last_output = self.building_output
                        for button in self.after_buttons:
                            button.configure(state="normal")
                        self._append_log(text)
                        self._append_log(self.play_guide_text())
                    else:
                        self._append_log(self.tr("log.error_prefix") + text)
                    if self.wizard is not None:
                        self.wizard.build_finished(self.last_output, ok)
                    if ok:
                        messagebox.showinfo(self.tr("build.done_title"), text + "\n" + self.tr("build.import_hint"))
                    else:
                        messagebox.showerror(self.tr("build.failed_title"), text)
                else:
                    self._append_log(message)
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    # --- はじめてガイド・できたアドオン ---
    def _open_wizard(self) -> None:
        from gui_wizard import SetupWizard

        if self.wizard is not None and self.wizard.window.winfo_exists():
            self.wizard.window.lift()
            return
        self.wizard = SetupWizard(self)

    def ffmpeg_path(self) -> str | None:
        return get_ffmpeg_path()

    def probe_size(self, path: str | Path) -> tuple[int, int] | None:
        return probe_video_size(path)

    def suggest_size(self, source_width: int, source_height: int, long_side: int) -> tuple[int, int]:
        return suggest_size(source_width, source_height, long_side)

    def apply_recommended(self, settings: dict) -> None:
        """はじめてガイドの勧める設定 (幅・高さ・fps) にする。"""
        self.width_var.set(settings["width"])
        self.height_var.set(settings["height"])
        self.fps_var.set(settings["fps"])
        self._on_fps_changed()
        self.messages.put(self.tr("wizard.applied", **settings))

    def start_build_from_wizard(self) -> None:
        if not self.building:
            self._start_build()

    def video_ids(self) -> list[str]:
        return [str(self.video_tree.item(iid)["values"][0]) for iid in self.video_tree.get_children()]

    def play_guide_text(self) -> str:
        """作ったアドオンを Minecraft に入れて再生するまでの案内。"""
        from gui_wizard import play_commands

        namespace = self.namespace_var.get().strip() or "badapple"
        commands = "\n".join("  " + command for command in play_commands(namespace, self.video_ids() or ["<ID>"]))
        return self.tr("after.guide", output=self.last_output or "", commands=commands, ns=namespace)

    def open_output_folder(self) -> None:
        from gui_wizard import open_folder

        if self.last_output is None:
            return
        target = self.last_output if self.last_output.exists() else self.last_output.parent
        open_folder(target)

    def import_output(self) -> None:
        from gui_wizard import import_to_minecraft

        if self.last_output is None:
            return
        if not import_to_minecraft(self.last_output):
            messagebox.showwarning(self.tr("after.import"), self.tr("after.import_failed", path=self.last_output))

    def _show_play_guide(self) -> None:
        messagebox.showinfo(self.tr("after.guide_button"), self.play_guide_text())

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def run_converter_mode(argv: list[str]) -> int:
    """EXE 版の変換モード。GUI から `BlockVideoPlayer.exe --run-converter ...` として起動される。"""
    if sys.stdout is None:  # windowed EXE で標準出力が無い場合
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    import convert

    return convert.main(argv)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == RUN_CONVERTER_FLAG:
        sys.exit(run_converter_mode(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == RUN_PERSON_FLAG:
        if sys.stdout is None:  # windowed EXE で標準出力が無い場合
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            sys.stderr = sys.stdout
        from mvcodec.person import main as person_main
        sys.exit(person_main(sys.argv[2:]))
    PackBuilderApp().mainloop()
