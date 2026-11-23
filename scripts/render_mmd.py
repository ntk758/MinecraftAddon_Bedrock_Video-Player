"""PMX・VMD・音声から MMD を MP4 に描く (Rendered Mode)。--addon で、その動画を画像モードのアドオンにもする。

    python scripts/render_mmd.py --pmx model.pmx --motion dance.vmd --audio song.wav --preset standard -o dance.mp4
    python scripts/render_mmd.py ... -o dance.mp4 --addon dance.mcaddon --subpack-tiers 2,5

描いた動画は PC で見る動画です。Minecraft の中では、テクスチャの予算 (Tier) に入る大きさ・長さに縮めて入れます
(docs/research/mmd_dual_path_design_2026-11.md の 4 章)。途中で止めたら、同じコマンドでもう一度動かすと続きから描きます。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.mmd_video import ENCODERS, RENDER_PRESETS, RenderError, VideoJob, render_video  # noqa: E402

PHYSICS = ("off", "light", "standard", "full")


def _colour(text: str) -> tuple:
    value = text.lstrip("#")
    if len(value) != 6:
        raise argparse.ArgumentTypeError("色は #RRGGBB で書いてください")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError("色は #RRGGBB で書いてください") from None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="MMD を動画に描きます (Rendered Mode)")
    parser.add_argument("--pmx", required=True, help="モデル (.pmx)")
    parser.add_argument("--motion", default=None, help="踊り (.vmd か .bvh)")
    parser.add_argument("--face-motion", action="append", default=[], help="表情・口の VMD (何個でも)")
    parser.add_argument("--camera-motion", default=None, help="カメラの VMD (無ければ踊りの VMD のカメラ、それも無ければ正面の固定)")
    parser.add_argument("--audio", default=None, help="曲。動画の長さは曲の長さになる")
    parser.add_argument("--stage", default=None, help="ステージのモデル (.pmx。動かさない)")
    parser.add_argument("-o", "--output", required=True, help="出力の .mp4")
    parser.add_argument("--preset", default="standard", choices=tuple(RENDER_PRESETS),
                        help="大きさと fps: " + "、".join(f"{k} {w}x{h} {f} fps" for k, (w, h, f) in RENDER_PRESETS.items()))
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--seconds", type=float, default=None, help="長さ (秒)。既定は曲か踊りの長さ")
    parser.add_argument("--supersample", type=int, default=None, choices=(1, 2, 3, 4),
                        help="アンチエイリアス: 縦横 N 倍で描いて縮める (既定 2、1440p より大きいと 1)")
    parser.add_argument("--physics", default="standard", choices=PHYSICS)
    parser.add_argument("--no-expressions", action="store_true")
    parser.add_argument("--no-edges", action="store_true", help="輪郭線を描かない")
    parser.add_argument("--background", type=_colour, default=(32, 36, 48), help="背景の色 #RRGGBB")
    parser.add_argument("--background-image", default=None)
    parser.add_argument("--jobs", type=int, default=None, help="並べて描くプロセスの数 (既定は CPU とメモリから)")
    parser.add_argument("--encoder", default="auto", choices=ENCODERS)
    parser.add_argument("--crf", type=int, default=18, help="画質 (小さいほどきれい)")
    parser.add_argument("--work-dir", default=None, help="コマの作業フォルダー (既定は <出力>.frames)")
    parser.add_argument("--keep-frames", action="store_true", help="できたあともコマを消さない")
    parser.add_argument("--report", default=None, help="計測と確かめの結果の JSON")
    addon = parser.add_argument_group("画像モードのアドオン")
    addon.add_argument("--addon", default=None, help="描いた動画から作る .mcaddon")
    addon.add_argument("--target-tier", type=int, default=2, choices=(1, 2, 3, 4, 5))
    addon.add_argument("--subpack-tiers", default=None, help="Tier ごとの大きさを入れる (例 2,5)。端末が自分の Tier を選ぶ")
    addon.add_argument("--priority", default="smooth", choices=("none", "quality", "smooth", "length"),
                       help="予算に入らないときに守るもの (既定 smooth = fps を守り大きさを下げる)")
    addon.add_argument("--addon-width", type=int, default=None, help="アドオンの 1 コマの幅 (既定は描いた大きさ)")
    addon.add_argument("--id", default="mmd_render")
    addon.add_argument("--title", default=None)
    addon.add_argument("--namespace", default="bvpimg")
    addon.add_argument("--pack-name", default=None)
    addon.add_argument("--allow-over-budget", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    job = VideoJob(pmx=args.pmx, motion=args.motion, output=args.output, audio=args.audio,
                   face_motions=args.face_motion, camera_motion=args.camera_motion, stage=args.stage,
                   preset=args.preset, width=args.width, height=args.height, fps=args.fps, seconds=args.seconds,
                   supersample=args.supersample, physics=args.physics, expressions=not args.no_expressions,
                   edges=not args.no_edges, background=args.background, background_image=args.background_image,
                   jobs=args.jobs, encoder=args.encoder, crf=args.crf, work_dir=args.work_dir,
                   keep_frames=args.keep_frames)
    try:
        report = render_video(job)
    except (RenderError, OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    if args.addon:
        import convert_image_mode
        width = args.addon_width or report["width"]
        height = int(round(width * report["height"] / report["width"] / 2)) * 2
        cmd = [args.output, "-o", args.addon, "--width", str(width), "--height", str(height),
               "--fps", f"{report['fps']:g}", "--priority", args.priority, "--target-tier", str(args.target_tier),
               "--ids", args.id, "--titles", args.title or Path(args.output).stem, "--namespace", args.namespace,
               "--max-sheet", "4096" if width > 2048 or height > 2048 else "2048"]
        if args.pack_name:
            cmd += ["--pack-name", args.pack_name]
        if args.subpack_tiers:
            cmd += ["--subpack-tiers", args.subpack_tiers]
        if args.allow_over_budget:
            cmd += ["--allow-over-budget"]
        code = convert_image_mode.main(cmd)
        report["addon"] = {"path": args.addon, "exit_code": code}
        if code != 0:
            print("エラー: 画像モードのアドオンを作れませんでした (動画はできています)", file=sys.stderr)
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report.get("addon", {}).get("exit_code", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
