"""画像モード: 動画のコマを atlas (シート) の PNG に並べ、テクスチャのメモリ量を見積もる。

ブロックモードの変換 (convert.py) とは独立していて、ffmpeg で取り出した RGB のコマを格子状に並べるだけ。
シート 1 枚の大きさは max_sheet 以内で、最後のシートも縮めずに全シート同じ大きさにする
(uv_anim のオフセットや textures 配列のシートを、1 つの大きさで計算できるように)。
"""
from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
from PIL import Image

# Microsoft Learn「Texture Budgets」(https://learn.microsoft.com/en-us/minecraft/creator/documents/texturebudgets) の
# アドオンのテクスチャ予算。ページの MB は 1024*1024 バイト (MiB) に近いので、ここでは MB = MiB として扱う。
# メモリは「幅 × 高さ × 4 バイト」で数える (Learn の規則)。
MIB = 1024 * 1024
TIER_BUDGETS_MB = {1: 150, 2: 225, 3: 300, 4: 600, 5: 800}
# Learn の推奨: アドオンのテクスチャハンドル (テクスチャの枚数) は 800 未満。上限は 4,000
RECOMMENDED_HANDLES = 800
MAX_HANDLES = 4000
# Tier 2 は多くのスマートフォン・PS4・Xbox One の水準
PHONE_TIER = 2
TOP_TIER = 5


def _subprocess_kwargs() -> dict:
    """Windows の GUI (EXE) から呼ばれたときにコンソール窓が開かないようにする (convert.py と同じ)。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def build_video_filter(width: int, height: int, fps: float, fit: str = "pad") -> str:
    """アスペクト比を保って width × height にする ffmpeg フィルタ。pad は黒の余白、cover は中央で切り取る。"""
    if fit not in ("pad", "cover"):
        raise ValueError(f"fit は pad か cover にしてください: {fit}")
    filters = [f"fps={fps:g}"]
    if fit == "cover":
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase")
        filters.append(f"crop={width}:{height}")
    else:
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    return ",".join(filters)


def _trim_args(start: float | None, end: float | None) -> list[str]:
    """開始・終了 (秒) を ffmpeg の -ss / -t にする。-i の前に置く (入力側で切るのでコマがずれない)。"""
    if start is not None and start < 0:
        raise ValueError("start は 0 以上にしてください")
    if end is not None and end <= (start or 0):
        raise ValueError("end は start より後にしてください")
    args: list[str] = []
    if start:
        args += ["-ss", f"{start:g}"]
    if end is not None:
        args += ["-t", f"{end - (start or 0):g}"]
    return args


def read_frames(path, width: int, height: int, fps: float, start: float | None = None, end: float | None = None,
                fit: str = "pad", ffmpeg: str = "ffmpeg") -> Iterator[np.ndarray]:
    """動画を width × height・fps に直して、H×W×3 (uint8) のコマを 1 枚ずつ返す。start / end は秒。"""
    cmd = [ffmpeg, "-v", "error", *_trim_args(start, end), "-i", str(path), "-an",
           "-vf", build_video_filter(width, height, fps, fit), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_subprocess_kwargs())
    frame_size = width * height * 3
    completed = False
    try:
        while True:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
        completed = True
    finally:
        if not completed:
            proc.kill()
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        proc.stderr.close()
        returncode = proc.wait()
    if completed and returncode != 0:
        raise RuntimeError(f"ffmpeg がエラー終了しました (code {returncode}): {stderr.strip()}")


def grid_size(width: int, height: int, max_sheet: int, gutter: int) -> tuple[int, int]:
    """シート 1 枚 (max_sheet 四方以内) に入るマスの列数・行数。マスの間だけに gutter を入れる (端には入れない)。"""
    cols = (max_sheet + gutter) // (width + gutter)
    rows = (max_sheet + gutter) // (height + gutter)
    if cols < 1 or rows < 1:
        raise ValueError(
            f"1 コマ ({width}x{height}) が max_sheet ({max_sheet}x{max_sheet}) のシートに入りません。"
            "コマの大きさを小さくするか --max-sheet を大きくしてください")
    return cols, rows


def _put_cell(sheet: np.ndarray, frame: np.ndarray, index: int, cols: int, width: int, height: int, gutter: int) -> None:
    """マス index にコマを置く。右と下の gutter は、そのコマの端の画素を繰り返して埋める
    (拡大縮小・補間のときに隣のコマの色が混ざらないように。黒で空けておくと縁に黒い線が出る)。"""
    x = (index % cols) * (width + gutter)
    y = (index // cols) * (height + gutter)
    sheet_h, sheet_w = sheet.shape[:2]
    sheet[y:y + height, x:x + width] = frame
    if gutter:
        # 右端・下端のマスは gutter の分がシートの外になるので、はみ出さない範囲だけ書く
        gx = min(gutter, sheet_w - (x + width))
        gy = min(gutter, sheet_h - (y + height))
        if gx > 0:
            sheet[y:y + height, x + width:x + width + gx] = frame[:, -1:, :]
        if gy > 0:
            sheet[y + height:y + height + gy, x:x + width] = frame[-1:, :, :]
        if gx > 0 and gy > 0:
            sheet[y + height:y + height + gy, x + width:x + width + gx] = frame[-1, -1, :]


def pack_sheets(frames: Iterable[np.ndarray], width: int, height: int, max_sheet: int = 2048,
                gutter: int = 1) -> tuple[list[Image.Image], dict]:
    """コマを左上から右へ、行ごとに下へ、シートが埋まったら次のシートへ並べる。

    マス i の左上は x = (i % cols) * (width + gutter)、y = (i // cols) * (height + gutter)。
    シートが 2 枚以上になるときは全シート max_sheet 以内でマスを最大に詰めた同じ大きさ (最後のシートも縮めない)。
    コマが 1 シートに収まるときだけ、無駄なメモリを使わないよう、コマの数に合わせてシートを小さくする。
    戻り値は (PIL の RGB 画像のリスト, layout)。layout は sheet_width / sheet_height / cols / rows / cells_per_sheet /
    sheets / frame_count (と width / height / gutter)。
    """
    if width < 1 or height < 1:
        raise ValueError("width と height は 1 以上にしてください")
    if gutter < 0:
        raise ValueError("gutter は 0 以上にしてください")
    max_cols, max_rows = grid_size(width, height, max_sheet, gutter)
    full_cells = max_cols * max_rows

    iterator = iter(frames)
    first: list[np.ndarray] = []
    for frame in iterator:
        first.append(_check_frame(frame, width, height))
        if len(first) == full_cells:
            break
    if not first:
        raise ValueError("コマが 1 枚もありません")

    sheets: list[Image.Image] = []
    count = len(first)
    if count < full_cells:
        # 読み終わった (full_cells に達していない) = 1 シートに収まる: コマの数に合わせた小さいシートにする (行はなるべく少なく、列は最大)
        cols = min(max_cols, count)
        rows = math.ceil(count / cols)
        sheet = _blank_sheet(cols, rows, width, height, gutter)
        for index, frame in enumerate(first):
            _put_cell(sheet, frame, index, cols, width, height, gutter)
        sheets.append(Image.fromarray(sheet, "RGB"))
        return sheets, _layout(sheet, cols, rows, count, 1, count, width, height, gutter)

    # 2 枚以上: 最大の格子のシートを同じ大きさで作る。ここまでで読んだ 1 枚ぶんの先頭 + 続き
    cols, rows = max_cols, max_rows
    pending = first  # 先頭の full_cells 枚 (続きは iterator にある)
    sheet = _blank_sheet(cols, rows, width, height, gutter)
    in_sheet = 0
    count = 0

    def flush() -> None:
        nonlocal sheet, in_sheet
        sheets.append(Image.fromarray(sheet, "RGB"))
        sheet = _blank_sheet(cols, rows, width, height, gutter)
        in_sheet = 0

    def add(frame: np.ndarray) -> None:
        nonlocal in_sheet, count
        _put_cell(sheet, frame, in_sheet, cols, width, height, gutter)
        in_sheet += 1
        count += 1
        if in_sheet == full_cells:
            flush()

    for frame in pending:
        add(frame)
    for frame in iterator:
        add(_check_frame(frame, width, height))
    if in_sheet:
        sheets.append(Image.fromarray(sheet, "RGB"))
    return sheets, _layout(np.asarray(sheets[0]), cols, rows, full_cells, len(sheets), count, width, height, gutter)


def _check_frame(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    frame = np.asarray(frame)
    if frame.shape != (height, width, 3) or frame.dtype != np.uint8:
        raise ValueError(f"コマは {height}x{width}x3 の uint8 にしてください: {frame.shape} {frame.dtype}")
    return frame


def _blank_sheet(cols: int, rows: int, width: int, height: int, gutter: int) -> np.ndarray:
    return np.zeros((rows * (height + gutter) - gutter, cols * (width + gutter) - gutter, 3), dtype=np.uint8)


def _layout(sheet: np.ndarray, cols: int, rows: int, cells: int, sheets: int, frame_count: int,
            width: int, height: int, gutter: int) -> dict:
    return {"sheet_width": int(sheet.shape[1]), "sheet_height": int(sheet.shape[0]), "cols": cols, "rows": rows,
            "cells_per_sheet": cols * rows, "sheets": sheets, "frame_count": frame_count,
            "width": width, "height": height, "gutter": gutter}


def memory_report(layout: dict | list[dict], allow_over_budget: bool = False) -> dict:
    """シートのテクスチャが使うメモリの見積もりと、アドオンの予算 (Tier 別) に対する警告。

    texels = 全シートの画素数、bytes = texels * 4 (Learn の規則)、handles = シートの枚数。
    Tier 2 の予算を超えると warning (多くのスマートフォン・PS4・Xbox One)、Tier 5 を超えると error
    (allow_over_budget なら warning にとどめる)、ハンドルが 800 を超えると warning (4,000 超は error)。
    layout は 1 つの動画の layout か、その list (アドオンに入れる全動画。予算はアドオン全体に対する値なので合計する)。
    """
    layouts = layout if isinstance(layout, list) else [layout]
    texels = sum(item["sheet_width"] * item["sheet_height"] * item["sheets"] for item in layouts)
    nbytes = texels * 4
    handles = sum(item["sheets"] for item in layouts)
    budgets = {f"tier{tier}": {"mib": mb, "bytes": mb * MIB, "over": nbytes > mb * MIB}
               for tier, mb in TIER_BUDGETS_MB.items()}
    warnings: list[str] = []
    errors: list[str] = []
    mib = nbytes / MIB
    if nbytes > TIER_BUDGETS_MB[TOP_TIER] * MIB:
        message = (f"テクスチャが {mib:.0f} MiB で、アドオンの最大の予算 Tier {TOP_TIER} "
                   f"({TIER_BUDGETS_MB[TOP_TIER]} MB) を超えます")
        (warnings if allow_over_budget else errors).append(message)
    elif nbytes > TIER_BUDGETS_MB[PHONE_TIER] * MIB:
        warnings.append(f"テクスチャが {mib:.0f} MiB で、Tier {PHONE_TIER} の予算 ({TIER_BUDGETS_MB[PHONE_TIER]} MB: "
                        "多くのスマートフォン・PS4・Xbox One) を超えます。これらの端末では読み込めない・落ちることがあります")
    if handles > MAX_HANDLES:
        (warnings if allow_over_budget else errors).append(
            f"テクスチャハンドルが {handles} 個で、上限の {MAX_HANDLES} を超えます")
    elif handles > RECOMMENDED_HANDLES:
        warnings.append(f"テクスチャハンドルが {handles} 個で、推奨の {RECOMMENDED_HANDLES} 未満を超えます")
    report = {"texels": texels, "bytes": nbytes, "mib": round(mib, 2), "handles": handles,
              "sheet_sizes": sorted({(item["sheet_width"], item["sheet_height"]) for item in layouts}),
              "budgets": budgets, "warnings": warnings, "errors": errors}
    report["table"] = format_memory_report(report)
    return report


def format_memory_report(report: dict) -> str:
    """memory_report の結果を表示用の複数行の文字列にする。"""
    sizes = ", ".join(f"{w}x{h}" for w, h in report["sheet_sizes"])
    lines = [
        f"テクスチャ: {report['handles']} 枚 ({sizes}) = "
        f"{report['texels']:,} 画素 = {report['bytes']:,} バイト ({report['bytes'] / MIB:.1f} MiB)",
        "予算 (Microsoft Learn Texture Budgets のアドオン):",
    ]
    for name, budget in report["budgets"].items():
        used = report["bytes"] / budget["bytes"] * 100
        lines.append(f"  {name.replace('tier', 'Tier ')}: {budget['mib']:>4} MB  {used:6.1f}%  {'超過' if budget['over'] else 'OK'}")
    lines.append(f"ハンドル: {report['handles']} (推奨 {RECOMMENDED_HANDLES} 未満、上限 {MAX_HANDLES})")
    for message in report["warnings"]:
        lines.append(f"警告: {message}")
    for message in report["errors"]:
        lines.append(f"エラー: {message}")
    return "\n".join(lines)


def extract_audio_chunks(path, out_dir, chunk_seconds: float = 4, start: float | None = None,
                         end: float | None = None, ffmpeg: str = "ffmpeg") -> list[tuple[int, str, bytes]]:
    """音声を chunk_seconds 秒ごとの .ogg (44.1kHz ステレオ) に切り出す。音声のない動画なら []。

    ffmpeg の設定は Windows アプリ (video_player_gui.py の _extract_audio) と同じ。
    戻り値は [(チャンクの番号, "ogg", bytes), ...] (番号順)。out_dir に chunk_<n>.ogg も残る。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("chunk_*.ogg"):
        old.unlink()
    cmd = [ffmpeg, "-y", "-v", "error", *_trim_args(start, end), "-i", str(path),
           "-f", "segment", "-segment_time", f"{chunk_seconds:g}",
           "-vn", "-acodec", "libvorbis", "-ar", "44100", "-ac", "2", "-b:a", "192k",
           str(out_dir / "chunk_%d.ogg")]
    result = subprocess.run(cmd, capture_output=True, **_subprocess_kwargs())
    files = sorted(out_dir.glob("chunk_*.ogg"), key=lambda f: int(f.stem.split("_")[1]))
    if result.returncode != 0 and not files:
        message = result.stderr.decode("utf-8", errors="replace")
        # 音声のない動画は「出力に stream がない」で終わる。それは無音として扱う
        if "does not contain any stream" in message or "Stream map" in message:
            return []
        raise RuntimeError(f"音声の切り出しに失敗しました (code {result.returncode}): {message.strip()}")
    return [(int(f.stem.split("_")[1]), "ogg", f.read_bytes()) for f in files]
