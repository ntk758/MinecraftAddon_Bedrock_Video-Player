"""ブロックの色の表 (mvcodec/block_colors.py) と、それを使うパレットのテスト。"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.block_colors import BLOCK_COLORS  # noqa: E402
from mvcodec.color import ALL_BLOCKS, AUTO_PALETTE_CANDIDATES, BLOCK_RGB, PALETTES  # noqa: E402
from mvcodec.encode import MAX_PALETTE_COLORS  # noqa: E402

# 置くと困るブロック: 重力で落ちる・時間で変化する・まわりに作用する (チートを使うアドオンなので、鉱石などの資源のブロックは使う)
# (砂岩・ソウルサンドは落ちない。氷は溶けるが packed_ice・blue_ice は溶けない。銅はろう引きしたものだけ酸化しない)
FORBIDDEN = ("concrete_powder", "gravel", "redstone", "magma", "tnt", "leaves", "grass", "dirt_with", "mycelium", "nylium",
             "farmland", "coral_block", "snow_layer", "powder_snow", "sponge", "observer", "piston", "dispenser", "dropper", "note", "infested",
             "budding", "anvil", "dragon_egg", "scaffolding", "pointed_dripstone", "sculk_sensor", "sculk_catalyst",
             "respawn_anchor", "creaking")


def is_forbidden(name):
    if name.startswith("dead_") or name == "redstone_lamp":
        return False
    if "sand" in name and not ("sandstone" in name or name == "soul_sand"):
        return True
    if name == "ice" or name == "dirt":
        return True
    if "copper" in name and not name.startswith(("waxed_", "raw_")):
        return True
    return any(word in name for word in FORBIDDEN)


def test_every_block_palette_fits_volumes_and_is_safe():
    ids = [item["block"] for item in BLOCK_COLORS]
    assert len(ids) == len(set(ids))
    # 立体は空気に 1 つ使うので、1 つのパレットは 127 色まで (全ブロックからは色が離れた 127 色を選ぶ)
    assert len(PALETTES["all"]) == MAX_PALETTE_COLORS - 1
    assert len(PALETTES["all"]) < len(AUTO_PALETTE_CANDIDATES) < len(BLOCK_COLORS)
    for block_id in ids:
        assert block_id.startswith("minecraft:")
        assert not is_forbidden(block_id.split(":", 1)[1]), block_id
    for item in BLOCK_COLORS:
        assert all(0 <= c <= 255 for c in item["rgb"]) and all(0 <= c <= 255 for c in item["side"])
    # 資源のブロックも使う
    assert {"minecraft:diamond_block", "minecraft:gold_block"} & set(ids)
    # 雪のブロックは光るブロックの明かりで溶けるので、光るブロックと同じパレットには入れない (自動パレットには入る)
    for name, palette in PALETTES.items():
        groups = {item.get("group") for item in palette}
        assert not ({"light"} <= groups and any(item["block"] == "minecraft:snow" for item in palette)), name
    assert any(item["block"] == "minecraft:snow" for item in AUTO_PALETTE_CANDIDATES)


def test_volumes_choose_blocks_by_their_side_color():
    """原木のように上面と側面の見た目が違うブロックは、立体では側面 (樹皮) の色で選ぶ。"""
    from mvcodec.color import side_palette

    logs = [item for item in AUTO_PALETTE_CANDIDATES if item["name"].endswith("_log") and item["rgb"] != item["side"]]
    assert logs
    sides = side_palette(logs)
    assert [item["rgb"] for item in sides] == [item["side"] for item in logs]
    assert [item["block"] for item in sides] == [item["block"] for item in logs]


def test_old_palettes_use_measured_colors():
    """以前の目安の値 (白いコンクリートが 255, 255, 255 など) ではなく、テクスチャの平均色を使う。"""
    white = next(item for item in ALL_BLOCKS if item["block"] == "minecraft:white_concrete")
    assert white["rgb"] != (255, 255, 255)
    assert max(white["rgb"]) < 230
    for item in ALL_BLOCKS:
        assert BLOCK_RGB[item["block"]] == tuple(item["rgb"])
    # 自動パレットの候補には光るブロックを入れない (全ブロックには入る)
    assert all(item["group"] != "light" for item in AUTO_PALETTE_CANDIDATES)
    from mvcodec.color import OPAQUE_BLOCKS

    assert {item["block"] for item in AUTO_PALETTE_CANDIDATES} < {item["block"] for item in OPAQUE_BLOCKS}


@pytest.mark.parametrize("mode", ["screen", "relief"])
def test_convert_with_every_block_palette(tmp_path, mode):
    from mvcodec.decode import iter_decoded_frames, load_frame_data
    from test_codec_v4 import make_scene_frames

    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "all.js"
    assert convert.main(["--frames-dir", str(tmp_path / "frames"), "--output", str(out), "--width", "32",
                         "--height", "24", "--fps", "10", "--device", "cpu", "--palette", "all", "--mode", mode,
                         "--depth", "8", "--duration", "2"]) == 0
    data = load_frame_data(out)
    assert len(data["level_blocks"]) == len(PALETTES["all"])
    assert sum(1 for _ in iter_decoded_frames(data)) == data["frame_count"]


def test_glass_palette_is_only_for_holograms():
    """ステンドグラス (半透明) は専用のパレットだけにあり、全ブロック・自動パレットには入らない。"""
    glass = PALETTES["glass"]
    assert len(glass) == 16
    assert all(item["block"].endswith("_stained_glass") for item in glass)
    assert all(0 < item["alpha"] < 1 for item in BLOCK_COLORS if item["group"] == "glass")
    opaque = {item["block"] for item in PALETTES["all"]} | {item["block"] for item in AUTO_PALETTE_CANDIDATES}
    assert not opaque & {item["block"] for item in glass}
    assert all(BLOCK_RGB[item["block"]] == item["rgb"] for item in glass)


def test_glass_hologram_places_stained_glass(tmp_path):
    """ステンドグラスのパレットで立体を作り、モックの Minecraft で再生するとステンドグラスと空気だけが置かれる。"""
    import shutil

    from test_codec_v4 import make_scene_frames
    from test_main_js import run_addon

    if shutil.which("node") is None:
        pytest.skip("node が必要")
    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "glass.js"
    assert convert.main(["--frames-dir", str(tmp_path / "frames"), "--output", str(out), "--width", "24",
                         "--height", "16", "--fps", "10", "--device", "cpu", "--palette", "glass", "--mode", "relief",
                         "--depth", "6", "--duration", "1"]) == 0
    result = run_addon(tmp_path / "addon", out)
    placed = {block for block in result["finalBoard"] if block}
    assert placed and all(block.endswith("_stained_glass") or block == "minecraft:air" for block in placed), placed


def test_gui_labels_show_the_current_color_count():
    import json

    every, auto = str(len(PALETTES["all"])), str(len(AUTO_PALETTE_CANDIDATES))
    for path in (ROOT / "locales").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert every in data["palette.all"] and auto in data["palette.auto"], path.name


def test_auto_palette_without_torch_picks_colors_from_the_video(monkeypatch):
    """PyTorch が無くても、先頭の固定の色ではなく、映像に多い色のブロックを選ぶ。"""
    import numpy as np

    from mvcodec.auto_palette import generate_auto_palette

    monkeypatch.setitem(sys.modules, "torch", None)
    frame = np.zeros((16, 16, 3), np.uint8)
    frame[:8] = (130, 165, 245)   # 水色 (packed_ice に近い)
    frame[8:] = (40, 120, 120)    # 青緑 (warped_wart_block などに近い)
    picked = generate_auto_palette(iter([frame]), AUTO_PALETTE_CANDIDATES, max_colors=8, sample_stride=1)
    assert len(picked) == 8
    top = [item["rgb"] for item in picked[:2]]
    assert any(abs(r - 130) + abs(g - 165) + abs(b - 245) < 60 for r, g, b in top), top
    assert any(abs(r - 40) + abs(g - 120) + abs(b - 120) < 60 for r, g, b in top), top


def test_auto_color_count_picks_the_fewest_colors_that_keep_the_error():
    """--palette-colors auto: 色の誤差が一番多い色数の AUTO_COLOR_TOLERANCE 倍以内に収まる、一番少ない色数を選ぶ。"""
    from mvcodec.auto_palette import AUTO_COLOR_COUNTS, _select_color_count, generate_auto_palette

    candidates = AUTO_PALETTE_CANDIDATES
    # 3 色だけの映像: 24 色で 64 色と同じ誤差になる
    few = np.array([candidates[i]["rgb"] for i in (0, 5, 9)] * 400, dtype=np.float32)
    calls = []

    def select(count):
        calls.append(count)
        return candidates[:count] if count == AUTO_COLOR_COUNTS[-1] else [candidates[i] for i in (0, 5, 9)] + candidates[20:20 + count - 3]

    assert len(_select_color_count(few, select)) == AUTO_COLOR_COUNTS[0]
    assert calls[0] == AUTO_COLOR_COUNTS[-1]
    # 色の多い映像で、少ない色数では誤差が大きく増える: 一番多い色数のまま
    rng = np.random.default_rng(0)
    many = rng.integers(0, 256, (3000, 3)).astype(np.float32)
    largest = AUTO_COLOR_COUNTS[-1]
    chosen = _select_color_count(many, lambda count: candidates[:count] if count == largest else candidates[:2])
    assert len(chosen) == largest
    # 実際の選び方 (PyTorch が無ければ色の数え上げ) でも、候補の色数のどれかのパレットになる
    frames = [rng.integers(0, 256, (24, 32, 3), dtype=np.uint8) for _ in range(3)]
    palette = generate_auto_palette(frames, candidates, max_colors="auto", sample_stride=1)
    assert len(palette) in AUTO_COLOR_COUNTS


def test_auto_palette_is_not_shared_by_scenes_that_start_black():
    """黒い画面から始まる別々の場面には、それぞれのパレットを作る (以前は最初のコマだけで同じ場面とみなし、
    最初の場面のパレットを使い回していた)。"""
    import convert

    built = []

    def fake_palette(samples, candidates, **kwargs):
        built.append(len(samples))
        return candidates[:24]

    black = np.zeros((16, 16, 3), np.uint8)
    red = np.zeros((16, 16, 3), np.uint8); red[..., 0] = 200
    blue = np.zeros((16, 16, 3), np.uint8); blue[..., 2] = 200
    import mvcodec.auto_palette as ap
    original = ap.generate_auto_palette
    ap.generate_auto_palette = fake_palette
    try:
        build = convert.make_auto_palette_builder(None)
        build([black, red, red], black)
        build([black, blue, blue], black)
        build([black, red, red], black)  # 同じ見た目の場面は使い回す
    finally:
        ap.generate_auto_palette = original
    assert len(built) == 2


def test_dark_foliage_stays_green():
    """暗い木の葉・芝生の色は、明るさの近い茶色ではなく緑のブロックになる (減色は OkLab で色合いの差を重く見る)。
    RGB の距離では茶色 (はがした濃いオークの原木) の方が近い。"""
    from mvcodec.color import BLOCK_RGB, nearest_oklab_indices

    names = ["minecraft:green_concrete", "minecraft:stripped_dark_oak_log", "minecraft:black_concrete"]
    pal = np.array([BLOCK_RGB[n] for n in names], dtype=np.float32)
    foliage = np.array([[(45, 65, 25), (50, 70, 25), (35, 60, 20)]], dtype=np.uint8)
    rgb_nearest = ((foliage[0, :, None, :].astype(np.float32) - pal[None]) ** 2).sum(-1).argmin(1)
    assert (rgb_nearest == 1).all()
    assert (nearest_oklab_indices(foliage, pal) == 0).all()


def test_dried_kelp_block_is_a_dark_green_candidate():
    """乾燥した昆布ブロック (暗い緑) は、名前に kelp (植物の昆布を除く語) を含んでも候補に入る。"""
    assert any(b["block"] == "minecraft:dried_kelp_block" for b in AUTO_PALETTE_CANDIDATES)


def test_pattern_dither_mixes_hue_and_keeps_exact_colours_clean():
    """パターンディザ (Knoll): ちょうど合うブロックの無い色 (肌の桃色) は、色合いも含めて平均が元の色に近い混ざりになり、
    ちょうど合うブロックのある色 (暗い紺) は 1 色のまま (粒が出ない)。"""
    from mvcodec.block_rdo import rgb_to_oklab
    from mvcodec.color import BLOCK_RGB, knoll_dither_indices
    from mvcodec.dither_motion import dither_pattern

    names = ["minecraft:smooth_quartz", "minecraft:target", "minecraft:chiseled_sandstone", "minecraft:pale_oak_planks",
             "minecraft:blue_concrete"]
    pal = np.array([BLOCK_RGB[n] for n in names], dtype=np.float32)
    noise = dither_pattern("blue_noise", 32, 32) + 0.5
    skin = np.full((32, 32, 3), (246, 218, 201), np.uint8)
    out = knoll_dither_indices(skin, pal, noise)
    assert len(np.unique(out)) >= 2  # 混ざる
    lin = lambda c: np.where(c / 255 <= 0.04045, c / 255 / 12.92, ((c / 255 + 0.055) / 1.055) ** 2.4)  # noqa: E731
    mean = lin(pal[out]).reshape(-1, 3).mean(0)
    mean_rgb = np.where(mean <= 0.0031308, mean * 12.92, 1.055 * mean ** (1 / 2.4) - 0.055) * 255
    nearest_one = pal[np.argmin(((rgb_to_oklab(pal.astype(np.uint8)) - rgb_to_oklab(skin[:1, :1])[0, 0]) ** 2).sum(1))]
    err = lambda c: np.sqrt(((rgb_to_oklab(np.clip(c, 0, 255).astype(np.uint8)[None]) - rgb_to_oklab(skin[:1, 0])) ** 2).sum())  # noqa: E731
    assert err(mean_rgb) < err(nearest_one)  # 1 色で塗るより元の色に近い
    exact = np.full((32, 32, 3), BLOCK_RGB["minecraft:blue_concrete"], np.uint8)
    assert np.all(knoll_dither_indices(exact, pal, noise) == 4)


def test_pattern_dither_gpu_version_matches_cpu():
    torch = pytest.importorskip("torch")
    from mvcodec.color import knoll_dither_indices, knoll_dither_torch

    rng = np.random.default_rng(0)
    pal = np.array([b["rgb"] for b in AUTO_PALETTE_CANDIDATES[:48]], dtype=np.float32)
    img = rng.integers(0, 256, (24, 40, 3)).astype(np.uint8)
    noise = rng.random((24, 40)).astype(np.float32)
    cpu = knoll_dither_indices(img, pal, noise)
    gpu = knoll_dither_torch(torch.tensor(img, dtype=torch.float32), torch.tensor(pal), torch.tensor(noise)).numpy()
    assert (cpu == gpu).mean() > 0.99


def test_colour_without_a_close_block_gets_two_blocks_to_mix():
    """近いブロックの無い色 (暗い舞台のズボンの暗いオリーブ色) には、混ぜると近くなる 2 つのブロックを選ぶ。
    近いブロックがある色は、そのブロック 1 つ。"""
    from mvcodec.auto_palette import MIX_THRESHOLD, _blocks_for_color
    from mvcodec.block_rdo import _srgb_to_linear, rgb_to_oklab

    olive = np.array([39, 37, 26], np.float32)
    picked = _blocks_for_color(olive, AUTO_PALETTE_CANDIDATES, MIX_THRESHOLD)
    assert len(picked) == 2
    pair = _srgb_to_linear(np.array([AUTO_PALETTE_CANDIDATES[i]["rgb"] for i in picked], np.float32))
    mixes = pair[0] * (1 - np.arange(1, 8)[:, None] / 8) + pair[1] * (np.arange(1, 8)[:, None] / 8)
    mix_srgb = np.where(mixes <= 0.0031308, mixes * 12.92, 1.055 * mixes ** (1 / 2.4) - 0.055) * 255
    best_mix = np.linalg.norm(rgb_to_oklab(mix_srgb.astype(np.float32)) - rgb_to_oklab(olive[None]), axis=1).min()
    single = np.linalg.norm(rgb_to_oklab(np.array([b["rgb"] for b in AUTO_PALETTE_CANDIDATES], np.float32))
                            - rgb_to_oklab(olive[None]), axis=1).min()
    assert best_mix < single
    white_wool = next(i for i, b in enumerate(AUTO_PALETTE_CANDIDATES) if b["block"] == "minecraft:white_wool")
    assert _blocks_for_color(np.array(AUTO_PALETTE_CANDIDATES[white_wool]["rgb"], np.float32),
                             AUTO_PALETTE_CANDIDATES, MIX_THRESHOLD) == [white_wool]


def test_auto_palette_includes_the_mixing_pair():
    pytest.importorskip("torch")
    from mvcodec.auto_palette import MIX_THRESHOLD, _blocks_for_color, generate_auto_palette

    frame = np.zeros((48, 64, 3), np.uint8)
    frame[:, 32:] = (39, 37, 26)
    palette = generate_auto_palette([frame], AUTO_PALETTE_CANDIDATES, max_colors=8, sample_stride=1)
    pair = {AUTO_PALETTE_CANDIDATES[i]["block"] for i in _blocks_for_color(np.array([39, 37, 26], np.float32),
                                                                             AUTO_PALETTE_CANDIDATES, MIX_THRESHOLD)}
    assert pair <= {b["block"] for b in palette}
