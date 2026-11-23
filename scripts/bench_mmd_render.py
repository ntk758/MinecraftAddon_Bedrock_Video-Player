"""Rendered Mode (MMD を動画に描く) の段ごとの重さを PC で測る (docs/research/mmd_dual_path_design_2026-11.md の 6 章)。

    python scripts/bench_mmd_render.py --pmx model.pmx --motion dance.vmd --seconds 10 -o bench.json

段ごとに、まばたきと口の表情・周りを回るカメラ・自作の曲 (scripts/make_mmd_showcase.py と同じ作り方) で描き、
1 コマの時間・全体の時間・ファイルの大きさ・CPU (システム全体)・RAM・音声と映像のずれを記録する。
描き手は CPU だけなので GPU のメモリは使わない (エンコードの h264_amf だけが GPU)。Minecraft の中の fps は測れない。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mvcodec import mmd  # noqa: E402
from mvcodec.mmd_video import RENDER_PRESETS, VideoJob, render_video  # noqa: E402


def table(rows: list[dict]) -> str:
    lines = ["| 段 | 大きさ | fps | 縦横の倍率 | コマ | 1 コマ (s、1 プロセス) | 描く (s) | 姿勢 (s) | エンコード (s) | プロセス | "
             "CPU (%) | RAM (MiB、1 プロセス) | MP4 (MiB) | PNG の合計 (MiB) | 音声のずれ (s) |",
             "|" + "---|" * 15]
    for r in rows:
        t = r["timings"]
        lines.append(f"| {r['preset']} | {r['width']}×{r['height']} | {r['fps']:g} | {r['supersample']} | {r['frames']} | "
                     f"{t.get('frame_s_mean', '-')} | {t['render_s']} | {t['pose_s']} | {t['encode_s']} | {r['jobs']} | "
                     f"{r.get('cpu_percent_system')} | {r.get('worker_peak_ram_mib')} | {r['output_bytes'] / 2**20:.1f} | "
                     f"{r['frames_bytes'] / 2**20:.0f} | {r.get('av_offset_s', '-')} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    import make_mmd_showcase as showcase

    parser = argparse.ArgumentParser(description="Rendered Mode の段ごとの重さを測ります")
    parser.add_argument("--pmx", required=True)
    parser.add_argument("--motion", required=True)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--presets", default=",".join(RENDER_PRESETS))
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--out-dir", default=None, help="MP4 を残すフォルダー (既定は一時フォルダー)")
    parser.add_argument("-o", "--output", default=None, help="結果の JSON")
    args = parser.parse_args(argv)
    model = mmd.read_pmx(args.pmx)
    height = float(model.positions[:, 1].max())
    rows = []
    with tempfile.TemporaryDirectory(prefix="mmd-bench-") as temp:
        temp = Path(temp)
        out_dir = Path(args.out_dir) if args.out_dir else temp
        out_dir.mkdir(parents=True, exist_ok=True)
        song, face, camera = temp / "song.wav", temp / "face.vmd", temp / "camera.vmd"
        showcase.write_song(song, args.seconds)
        showcase.face_vmd(face, args.seconds, "まばたき", "あ")
        # 全身が入る距離 (縦の視野角 30 度で高さの約 1.1 倍) で、胸の高さを見て回る
        showcase.camera_vmd(camera, args.seconds, 20.0, height * 0.5, height * 2.2)
        for preset in args.presets.split(","):
            job = VideoJob(pmx=args.pmx, motion=args.motion, output=str(out_dir / f"{preset}.mp4"), audio=str(song),
                           face_motions=[str(face)], camera_motion=str(camera), preset=preset, jobs=args.jobs)
            report = render_video(job)
            rows.append(report)
            print(json.dumps({k: v for k, v in report.items() if k != "warnings"}, ensure_ascii=False), flush=True)
    print(table(rows))
    if args.output:
        Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
