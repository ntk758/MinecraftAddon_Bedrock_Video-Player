"""骨で動かす MMD の人物 (画像モード) の細かさごとの重さを PC で測る (docs/research/mmd_dual_path_design_2026-11.md の 6 章)。

    python scripts/bench_mmd_figure.py --pmx model.pmx --motion dance.vmd --seconds 10 --voxels 64,128,192,256,384,512

細かさごとに別のプロセスで作り (前の細かさのメモリが残らないように)、次を測る:
ボクセルにする時間・人物を作る時間 (板・シート・アニメーション)・プロセスのピークの RAM・テクスチャ (シートの画素 × 4)・
板の数・geometry のボーンの数・geometry とアニメーションの JSON の大きさ。Minecraft の中の fps は測れない (実機が要る)。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIB = 1024 * 1024


def measure(pmx: str, motion: str | None, seconds: float, voxels: int, texels: int, physics: str | None) -> dict:
    """1 つの細かさで人物を作って測る (このプロセスの中で)。"""
    import psutil

    from image_mode import rig as rigmod
    from image_mode import rig_anim

    process = psutil.Process()
    start = time.perf_counter()
    dance = rig_anim.PosedDance(pmx, motion, seconds=seconds, log=lambda *_: None, physics=physics)
    posed = time.perf_counter()
    model, materials = dance.model, dance.materials
    grid = rigmod.rest_grid(model, voxels)
    rest = rigmod.surface_voxels(model, materials, grid)
    voxelized = time.perf_counter()
    figure = rig_anim.figure_from_dance(dance, identifier="animation.bench.figure", figure_blocks=6, voxels=voxels,
                                        texels=texels, max_sheet=4096, log=lambda *_: None)
    built = time.perf_counter()
    memory = process.memory_info()
    peak = getattr(memory, "peak_wset", None) or memory.rss
    geometry = json.dumps(figure["bones"], separators=(",", ":"))
    return {
        "voxels": voxels, "texels": texels, "seconds": seconds, "physics": physics or "off",
        "pose_s": round(posed - start, 2), "voxelize_s": round(voxelized - posed, 2),
        "figure_s": round(built - voxelized, 2), "total_s": round(built - start, 2),
        "peak_ram_mib": round(peak / MIB, 1),
        "surface_voxels": int(rest.surface.sum()), "voxels_total": int(len(rest.cells)),
        "sheet": [figure["sheet_width"], figure["sheet_height"]],
        "texture_mib": round(figure["sheet_width"] * figure["sheet_height"] * 4 / MIB, 2),
        "quads": int(figure["quads"]), "bones": len(figure["bones"]), "rig_bones": int(figure["rig"]["bones"]),
        "geometry_kib": round(len(geometry) / 1024, 1),
        "animation_kib": round(len(figure["rig"]["animation"]) / 1024, 1),
        "keys": int(figure["rig"]["keys"]),
    }


def table(rows: list[dict]) -> str:
    head = ("| ボクセル | 板の画素 | ボクセルにする (s) | 人物を作る (s) | 合計 (s) | ピーク RAM (MiB) | 表面のボクセル | "
            "シート | テクスチャ (MiB) | 板 | ボーン | geometry (KiB) | アニメーション (KiB) |")
    lines = [head, "|" + "---|" * 13]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['voxels']} | {r['texels']} | 失敗: {r['error']} |" + " |" * 10)
            continue
        lines.append(f"| {r['voxels']} | {r['texels']} | {r['voxelize_s']} | {r['figure_s']} | {r['total_s']} | "
                     f"{r['peak_ram_mib']} | {r['surface_voxels']:,} | {r['sheet'][0]}×{r['sheet'][1]} | "
                     f"{r['texture_mib']} | {r['quads']:,} | {r['bones']} | {r['geometry_kib']:,} | {r['animation_kib']:,} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MMD の人物の細かさごとの重さを測ります")
    parser.add_argument("--pmx", required=True)
    parser.add_argument("--motion", default=None, help="踊り (.vmd)。無ければ動かない人物")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--voxels", default="64,128,192,256,384,512")
    parser.add_argument("--texels", default="2", help="板の画素 (カンマで複数)")
    parser.add_argument("--physics", default=None, choices=("light", "standard", "full"))
    parser.add_argument("-o", "--output", default=None, help="結果の JSON")
    parser.add_argument("--one", action="store_true", help=argparse.SUPPRESS)  # 子プロセス: 1 つだけ測って JSON を出す
    args = parser.parse_args(argv)
    if args.one:
        print(json.dumps(measure(args.pmx, args.motion, args.seconds, int(args.voxels), int(args.texels), args.physics)))
        return 0
    rows = []
    for texels in [int(t) for t in args.texels.split(",")]:
        for voxels in [int(v) for v in args.voxels.split(",")]:
            cmd = [sys.executable, __file__, "--one", "--pmx", args.pmx, "--seconds", str(args.seconds),
                   "--voxels", str(voxels), "--texels", str(texels)]
            if args.motion:
                cmd += ["--motion", args.motion]
            if args.physics:
                cmd += ["--physics", args.physics]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                error = (result.stderr.strip().splitlines() or ["?"])[-1][:200]
                rows.append({"voxels": voxels, "texels": texels, "error": error})
            else:
                rows.append(json.loads(result.stdout.strip().splitlines()[-1]))
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    print(table(rows))
    if args.output:
        Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all("error" not in r for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
