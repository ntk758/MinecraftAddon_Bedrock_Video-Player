"""ブロックの色の表 (mvcodec/block_colors.py) を、Mojang 公式の bedrock-samples のテクスチャから作る。

使い方:
    git clone --depth 1 --filter=blob:none --sparse https://github.com/Mojang/bedrock-samples.git
    cd bedrock-samples
    git sparse-checkout set resource_pack/textures/blocks metadata/vanilladata_modules
    git fetch --depth 1 --filter=blob:none origin tag v1.21.80.3
    cd ..
    python scripts/generate_block_colors.py --samples bedrock-samples

色の決め方:
- 上面 (スクリーンは上から見る) のテクスチャの全画素を、線形の明るさで平均して sRGB に戻す
  (遠くから見たときにブロック 1 個が見える色に近い)。アニメーションするテクスチャは全部のコマの平均
- 候補は「ブロック 1 マスの不透明な立方体で、重力で落ちず、時間で変化せず (酸化・枯れる・溶ける・草が生える)、
  まわりに作用しない (ダメージ・レッドストーン信号)、面ごとの見た目の差や模様が小さい」もの (CANDIDATES)。
  資源や経験値になるブロック (金・ダイヤモンド・エメラルド・ネザライト・鉄・ラピスラズリ・石炭、その原石、スカルク) は、
  サバイバルでスクリーンを掘ると無限に得られてしまうので入れない
- 光るブロック (group "light") は「全ブロック」には入れるが、自動パレットの候補には入れない (mvcodec/color.py)
- アドオンの対応する一番古い版 (min_engine_version 1.21.80) に無いブロックは使わない (置けないと土になる)
- 色がほとんど同じブロック (OkLab の距離 × 100 が DEDUPE_DISTANCE 未満) は、先に書いた方だけ残す
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "mvcodec" / "block_colors.py"
MIN_VERSION_TAG = "v1.21.80.3"
DEDUPE_DISTANCE = 2.0

COLORS = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
          "purple", "blue", "brown", "green", "red", "black"]

# (グループ, ブロック ID (minecraft: を除く), blocks.json のキー (違うときだけ))。グループの順に優先して残す
CANDIDATES = (
    [("concrete", f"{c}_concrete", None) for c in COLORS]
    + [("terracotta", "hardened_clay", None)]
    + [("terracotta", f"{c}_terracotta", None) for c in COLORS]
    + [("light", "sea_lantern", "seaLantern"), ("light", "glowstone", None), ("light", "shroomlight", None),
       ("light", "pearlescent_froglight", None), ("light", "verdant_froglight", None),
       ("light", "ochre_froglight", None)]
    + [("wool", f"{c}_wool", None) for c in COLORS]
    + [("wood", f"{w}_planks", None) for w in ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove",
                                               "cherry", "bamboo", "crimson", "warped", "pale_oak")]
    + [("wood", f"stripped_{w}_wood", None) for w in ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
                                                      "mangrove", "cherry", "pale_oak")]
    + [("wood", "stripped_crimson_hyphae", None), ("wood", "stripped_warped_hyphae", None)]
    + [("stone", b, None) for b in (
        "stone", "smooth_stone", "stone_bricks", "mossy_stone_bricks", "andesite", "polished_andesite", "diorite",
        "polished_diorite", "granite", "polished_granite", "calcite", "tuff", "polished_tuff", "tuff_bricks",
        "dripstone_block", "cobbled_deepslate", "polished_deepslate", "deepslate_bricks", "deepslate_tiles",
        "smooth_basalt", "polished_blackstone", "polished_blackstone_bricks", "end_stone", "end_bricks",
        "purpur_block", "prismarine", "prismarine_bricks", "dark_prismarine", "smooth_quartz", "quartz_bricks",
        "brick_block", "mud_bricks", "packed_mud", "nether_brick", "red_nether_brick", "netherrack", "obsidian",
        "resin_bricks")]
    + [("light", "crying_obsidian", None)]
    # 金・ダイヤモンド・エメラルド・ネザライト・鉄・ラピスラズリ・石炭のブロックとその原石、スカルク (経験値が出る) は入れない
    # (スクリーンは変わったブロックを毎フレーム置き直すので、サバイバルで掘ると資源や経験値を無限に得られてしまう)
    + [("mineral", b, None) for b in (
        "amethyst_block", "raw_copper_block", "waxed_copper",
        "waxed_exposed_copper", "waxed_weathered_copper", "waxed_oxidized_copper", "resin_block")]
    + [("nature", b, None) for b in (
        "moss_block", "pale_moss_block", "coarse_dirt", "clay", "packed_ice", "blue_ice", "snow", "honeycomb_block",
        "nether_wart_block", "warped_wart_block", "soul_soil", "dead_brain_coral_block")]
)


def load_json(path):
    text = re.sub(r"^\s*//.*$", "", Path(path).read_text(encoding="utf-8"), flags=re.M)
    return json.loads(text)


def srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return [int(v) for v in np.clip(np.round(c * 255), 0, 255)]


def oklab(rgb):
    lin = srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]])
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]])
    return m2 @ np.cbrt(m1 @ lin)


def block_names(samples, ref=None):
    path = "metadata/vanilladata_modules/mojang-blocks.json"
    if ref is None:
        data = load_json(samples / path)
    else:
        text = subprocess.run(["git", "-C", str(samples), "show", f"{ref}:{path}"], capture_output=True, text=True,
                              encoding="utf-8", check=True).stdout
        data = json.loads(text)
    return {item["name"] for item in data["data_items"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", required=True, help="bedrock-samples のフォルダ")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args(argv)
    samples = Path(args.samples)
    rp = samples / "resource_pack"
    blocks = load_json(rp / "blocks.json")
    terrain = load_json(rp / "textures" / "terrain_texture.json")["texture_data"]
    current = block_names(samples)
    oldest = block_names(samples, MIN_VERSION_TAG)
    # 版は bedrock-samples のコミットの題名 (例: "1.26.50.4")
    version = subprocess.run(["git", "-C", str(samples), "log", "-1", "--format=%s"], capture_output=True, text=True,
                             encoding="utf-8").stdout.strip() or "?"

    def face_color(key):
        textures = blocks[key]["textures"]
        name = textures if isinstance(textures, str) else textures.get("up", textures.get("side"))
        entry = terrain[name]["textures"]
        path = entry[0] if isinstance(entry, list) else entry
        path = path["path"] if isinstance(path, dict) else path
        file = next(rp / (path + ext) for ext in (".png", ".tga") if (rp / (path + ext)).exists())
        # アニメーションするテクスチャ (縦に並んだコマ) は全部のコマの平均
        img = np.asarray(Image.open(file).convert("RGBA"), dtype=np.float64)
        return linear_to_srgb(srgb_to_linear(img[..., :3].reshape(-1, 3)).mean(axis=0))

    rows, skipped = [], []
    for group, block, key in CANDIDATES:
        block_id = f"minecraft:{block}"
        if block_id not in current or block_id not in oldest:
            skipped.append(f"{block} (1.21.80 に無い)" if block_id in current else f"{block} (存在しない)")
            continue
        rgb = face_color(key or block)
        lab = oklab(rgb) * 100
        twin = next((r for r in rows if np.linalg.norm(r["lab"] - lab) < DEDUPE_DISTANCE), None)
        if twin is not None:
            skipped.append(f"{block} ({twin['block']} とほぼ同じ色)")
            continue
        rows.append({"group": group, "block": block_id, "rgb": rgb, "lab": lab})

    lines = [
        '"""ブロックの見た目の平均色 (自動生成: scripts/generate_block_colors.py。手で書き換えない)。',
        "",
        f"出典: Mojang/bedrock-samples {version} のテクスチャの上面を線形の明るさで平均した sRGB。",
        f"1.21.80 ({MIN_VERSION_TAG}) にもあるブロックだけ。{len(rows)} 色。",
        '"""',
        "",
        "BLOCK_COLORS = [",
    ]
    for row in rows:
        lines.append(f'    {{"group": "{row["group"]}", "block": "{row["block"]}", "rgb": ({row["rgb"][0]}, '
                     f'{row["rgb"][1]}, {row["rgb"][2]})}},')
    lines += ["]", ""]
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(rows)} colors -> {args.output}")
    for item in skipped:
        print("  skipped:", item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
