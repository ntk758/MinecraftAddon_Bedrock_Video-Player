"""ブロックの色の表 (mvcodec/block_colors.py) を、Mojang 公式の bedrock-samples のテクスチャから作る。

使い方:
    git clone --depth 1 --filter=blob:none --sparse https://github.com/Mojang/bedrock-samples.git
    cd bedrock-samples
    git sparse-checkout set resource_pack/textures/blocks metadata/vanilladata_modules
    git fetch --depth 1 --filter=blob:none origin tag v1.21.80.3
    cd ..
    python scripts/generate_block_colors.py --samples bedrock-samples

色の決め方:
- 上面 (rgb: 平面スクリーンは上から見る) と側面 (side: 立体は横から見る) のテクスチャの全画素を、それぞれ線形の明るさで
  平均して sRGB に戻す (遠くから見たときにブロック 1 個が見える色に近い)。アニメーションするテクスチャは全部のコマの平均。
  原木や柱のように上面と側面で見た目が違うブロックは、立体では側面の色で選ぶ (mvcodec/color.py)
- 候補は「ブロック 1 マスの不透明な立方体で、重力で落ちず、時間で変化せず (酸化・枯れる・溶ける・草が広がる・覆うと土に
  なる)、まわりに作用しない (ダメージ・レッドストーン信号・爆発・水を吸う・虫が出る)」もの。CANDIDATES に書いたものを
  先に (色が近いときに優先して残す)、残りは blocks.json の全ブロックから自動で集める (discover_candidates):
  形が立方体でないもの・上の性質に当てはまるものを名前で除き (EXCLUDE_WORDS。INCLUDE で例外)、上面か側面のテクスチャに
  透明な画素があるもの (花・葉・ガラス・格子など) を除く。チートを有効にして使うアドオンなので、鉱石や鉱物のブロックも使う
- 光るブロック (group "light") は「全ブロック」には入れるが、自動パレットの候補には入れない (mvcodec/color.py)。
  雪のブロック (group "snow") は光るブロックの明かりで溶けるので、逆に自動パレットにだけ入れる (一番白いブロックなので)
- アドオンの対応する一番古い版 (min_engine_version 1.21.80) に無いブロックは使わない (置けないと土になる)
- 上面も側面も色がほとんど同じブロック (OkLab の距離 × 100 が DEDUPE_DISTANCE 未満) は、先に書いた方だけ残す
- ステンドグラス (group "glass") は半透明なので、不透明な画素ほど重く (アルファで重みを付けて) 平均し、平均のアルファも書く。
  「全ブロック」や自動パレットには入れず、ホログラム用の「ステンドグラス」パレットだけで使う (mvcodec/color.py)。
  不透明なブロックと色が近くても別のものとして残す
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
    + [("mineral", b, None) for b in (
        "amethyst_block", "raw_copper_block", "waxed_copper",
        "waxed_exposed_copper", "waxed_weathered_copper", "waxed_oxidized_copper", "resin_block")]
    + [("snow", "snow", None)]
    + [("nature", b, None) for b in (
        "moss_block", "pale_moss_block", "coarse_dirt", "clay", "packed_ice", "blue_ice", "honeycomb_block",
        "nether_wart_block", "warped_wart_block", "soul_soil", "dead_brain_coral_block")]
    + [("glass", f"{c}_stained_glass", None) for c in COLORS]
)
# 半透明で、不透明なブロックとは別に扱うグループ
TRANSLUCENT_GROUPS = ("glass",)

# 自動で集めるときに除くブロック (名前に含まれる語)。形が立方体でないもの、重力で落ちる・時間で変わる・まわりに作用するもの、
# 見えない・操作用のブロック。雪は光るブロックの明かりで溶け、氷は溶け、草・菌糸・ナイリウムは覆うと土に変わり、
# 土は草が広がり、銅は酸化し (ろう引きは除かない)、サンゴは乾くと死に、葉は消え、スポンジは水を吸い、
# 観察者・ディスペンサー・音符ブロックなどはブロックの変化や信号で動き、虫食いブロックからは虫が出て、マグマはダメージを与える
EXCLUDE_WORDS = (
    "slab", "stairs", "wall", "fence", "door", "button", "pressure_plate", "sign", "pane", "carpet", "bed", "banner",
    "candle", "rail", "torch", "lantern", "chain", "bars", "flower", "sapling", "plant", "vine", "head", "skull", "_fan",
    "bud", "cluster", "pot", "rod", "lever", "ladder", "scaffolding", "cake", "cactus", "kelp", "seagrass", "roots",
    "mushroom", "fungus", "sprouts", "tripwire", "string", "repeater", "comparator", "daylight", "hopper", "cauldron",
    "anvil", "bell", "grindstone", "stonecutter", "lectern", "brewing", "enchanting", "portal", "dragon", "conduit",
    "sea_pickle", "turtle_egg", "sniffer_egg", "frog", "web", "tall", "grass", "fern", "bush", "lily", "dripleaf",
    "spore", "azalea", "vein", "lichen", "layer", "chest", "shulker", "campfire", "composter", "beacon", "spawner",
    "vault", "heavy_core", "pointed", "lightning", "weighted", "detector", "barrier", "light_block", "structure",
    "jigsaw", "command", "border", "allow", "deny", "invisible", "moving", "piston", "observer", "dispenser", "dropper",
    "crafter", "note", "redstone", "tnt", "sculk_sensor", "sculk_shrieker", "sculk_catalyst", "respawn", "sponge",
    "magma", "budding", "infested", "creaking", "copper_bulb", "trial", "decorated", "frame", "hanging", "stem", "wart",
    "crop", "carrot", "potato", "beetroot", "wheat", "cocoa", "chorus", "berry", "berries", "pitcher", "petals",
    "litter", "firefly", "egg", "farmland", "path", "dirt", "mycelium", "nylium", "powder", "sand", "gravel",
    "suspicious", "ice", "snow", "coral", "leaves", "glass", "honey_block", "slime", "lit_", "unlit_", "element_",
    "compound", "lab_table", "material_reducer", "underwater", "colored_torch", "hard_", "camera", "info_update",
    "reserved", "unknown", "glow_", "client_request", "chalkboard", "netherreactor", "fire", "water", "lava", "air",
    "bubble", "deprecated", "ghast",
)
# EXCLUDE_WORDS に当てはまっても使うもの (落ちない砂岩・ソウルサンド、変化しない土・サンゴ・氷・キノコのブロック、幹、
# 明かりをともしたカボチャ、信号が無ければ消えたままのレッドストーンランプ、乾燥した昆布ブロック (暗い緑))
INCLUDE = {
    "sandstone", "chiseled_sandstone", "cut_sandstone", "smooth_sandstone", "red_sandstone", "chiseled_red_sandstone",
    "cut_red_sandstone", "smooth_red_sandstone", "soul_sand", "coarse_dirt", "packed_ice", "blue_ice",
    "red_mushroom_block", "brown_mushroom_block", "mushroom_stem", "nether_wart_block", "warped_wart_block",
    "crimson_stem", "warped_stem", "stripped_crimson_stem", "stripped_warped_stem", "lit_pumpkin",
    "dead_brain_coral_block", "dead_bubble_coral_block", "dead_fire_coral_block", "dead_horn_coral_block",
    "dead_tube_coral_block", "redstone_lamp", "dried_kelp_block",
}
# 自動で集めたブロックのうち、光るもの (自動パレットの候補には入れない)
LIGHT_BLOCKS = {"lit_pumpkin", "glowstone", "sea_lantern", "shroomlight", "ochre_froglight", "verdant_froglight",
                "pearlescent_froglight", "crying_obsidian"}


def infer_group(block):
    """自動で集めたブロックのグループ (パレットの名前と、色が近いときの優先順に使う)。"""
    if block in LIGHT_BLOCKS:
        return "light"
    if block.endswith("_concrete"):
        return "concrete"
    if "terracotta" in block or block == "hardened_clay":
        return "terracotta"
    if block.endswith("_wool"):
        return "wool"
    if any(w in block for w in ("log", "wood", "planks", "hyphae", "stem", "bamboo")):
        return "wood"
    if any(w in block for w in ("ore", "copper", "iron", "gold", "diamond", "emerald", "lapis", "coal", "netherite",
                                "amethyst", "quartz", "debris", "raw_")):
        return "mineral"
    if any(w in block for w in ("stone", "brick", "deepslate", "tuff", "basalt", "blackstone", "andesite", "diorite",
                                "granite", "calcite", "purpur", "prismarine", "obsidian", "netherrack", "end_",
                                "tile", "mud", "sandstone", "dripstone")):
        return "stone"
    return "other"


def discover_candidates(blocks, current, oldest, listed):
    """blocks.json の全ブロックから、CANDIDATES に無い候補 (グループ, ブロック, None) を名前で集める (透明の判定は後で)。"""
    found = []
    for key, value in blocks.items():
        if key == "format_version" or key in listed or not isinstance(value, dict) or "textures" not in value:
            continue
        if f"minecraft:{key}" not in current or f"minecraft:{key}" not in oldest:
            continue
        if key not in INCLUDE and any(word in key for word in EXCLUDE_WORDS):
            continue
        # 銅は酸化するので、ろう引きしたものだけ
        if "copper" in key and not key.startswith("waxed_") and not key.startswith("raw_"):
            continue
        found.append((infer_group(key), key, None))
    return sorted(found, key=lambda item: item[1])


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

    def face_pixels(key, face):
        """面 (up / south) のテクスチャの画素 (N, 4)。見つからなければ None。"""
        textures = blocks[key]["textures"]
        if isinstance(textures, str):
            name = textures
        else:
            order = ("up", "side") if face == "up" else ("south", "side", "north", "east", "up")
            name = next((textures[f] for f in order if f in textures), None)
        if name not in terrain:
            return None
        entry = terrain[name]["textures"]
        path = entry[0] if isinstance(entry, list) else entry
        path = path["path"] if isinstance(path, dict) else path
        file = next((rp / (path + ext) for ext in (".png", ".tga") if (rp / (path + ext)).exists()), None)
        if file is None:
            return None
        return np.asarray(Image.open(file).convert("RGBA"), dtype=np.float64).reshape(-1, 4)

    def mean_color(img):
        # アニメーションするテクスチャ (縦に並んだコマ) は全部のコマの平均。半透明の画素はアルファで重みを付ける
        alpha = img[:, 3] / 255.0
        weights = alpha if alpha.sum() > 0 else np.ones_like(alpha)
        linear = (srgb_to_linear(img[:, :3]) * weights[:, None]).sum(axis=0) / weights.sum()
        return linear_to_srgb(linear), round(float(alpha.mean()), 2)

    listed = {block for _group, block, _key in CANDIDATES}
    candidates = [(g, b, k, True) for g, b, k in CANDIDATES]
    candidates += [(g, b, k, False) for g, b, k in discover_candidates(blocks, current, oldest, listed)]
    rows, skipped = [], []
    for group, block, key, explicit in candidates:
        block_id = f"minecraft:{block}"
        if block_id not in current or block_id not in oldest:
            skipped.append(f"{block} (1.21.80 に無い)" if block_id in current else f"{block} (存在しない)")
            continue
        up, side = face_pixels(key or block, "up"), face_pixels(key or block, "south")
        if up is None or side is None:
            skipped.append(f"{block} (テクスチャが見つからない)")
            continue
        translucent = group in TRANSLUCENT_GROUPS
        # 自動で集めたものは、上面・側面のどちらかに透明な画素があれば立方体として見えないので使わない
        if not explicit and min(up[:, 3].min(), side[:, 3].min()) < 250:
            skipped.append(f"{block} (透明な部分がある)")
            continue
        rgb, alpha = mean_color(up)
        side_rgb, _ = mean_color(side)
        lab, side_lab = oklab(rgb) * 100, oklab(side_rgb) * 100
        twin = next((r for r in rows if (r["group"] in TRANSLUCENT_GROUPS) == translucent
                     and np.linalg.norm(r["lab"] - lab) < DEDUPE_DISTANCE
                     and np.linalg.norm(r["side_lab"] - side_lab) < DEDUPE_DISTANCE), None)
        if twin is not None:
            skipped.append(f"{block} ({twin['block']} とほぼ同じ色)")
            continue
        rows.append({"group": group, "block": block_id, "rgb": rgb, "side": side_rgb, "lab": lab, "side_lab": side_lab,
                     "alpha": alpha if translucent else None})

    lines = [
        '"""ブロックの見た目の平均色 (自動生成: scripts/generate_block_colors.py。手で書き換えない)。',
        "",
        f"出典: Mojang/bedrock-samples {version} のテクスチャの上面 (rgb) と側面 (side) を線形の明るさで平均した sRGB。",
        f"1.21.80 ({MIN_VERSION_TAG}) にもあるブロックだけ。{len(rows)} 色 (うち半透明 "
        f"{sum(r['group'] in TRANSLUCENT_GROUPS for r in rows)} 色。alpha は平均の不透明度)。",
        '"""',
        "",
        "BLOCK_COLORS = [",
    ]
    for row in rows:
        alpha = f', "alpha": {row["alpha"]}' if row["alpha"] is not None else ""
        lines.append(f'    {{"group": "{row["group"]}", "block": "{row["block"]}", "rgb": {tuple(row["rgb"])}, '
                     f'"side": {tuple(row["side"])}{alpha}}},')
    lines += ["]", ""]
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(rows)} colors -> {args.output}")
    for item in skipped:
        print("  skipped:", item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
