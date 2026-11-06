// ブラウザ版 (web/) と同じ Pyodide (32bit の WebAssembly の Python) で、convert.py ・ pack_builder.py ・ image_mode/ を最後まで動かす。
// 64bit の Python では起きない型の違い (np.intp が 32bit など) をブラウザを使わずに見つけるため (CI で実行)。
// 使い方: node tests/js/pyodide_smoke.mjs <scripts/build_web.py で作ったフォルダ>
import { loadPyodide } from "pyodide";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const site = process.argv[2];
const pyodide = await loadPyodide();
await pyodide.loadPackage(["numpy", "pillow"]);
for (const path of JSON.parse(readFileSync(join(site, "files.json"), "utf8"))) {
  pyodide.FS.mkdirTree("/app/" + path.split("/").slice(0, -1).join("/"));
  pyodide.FS.writeFile("/app/" + path, readFileSync(join(site, "app", path)));
}
// ブラウザ版と同じく、変換の進み具合を JS の関数で受け取る
pyodide.runPython(`
import sys
sys.path.insert(0, "/app")
import convert
`);
const progress = [];
pyodide.globals.get("convert").PROGRESS_HOOK = (done, total) => progress.push([done, total]);
const result = pyodide.runPython(`
import sys, io, zipfile, json
sys.path.insert(0, "/app")
import numpy as np
from PIL import Image
import convert, pack_builder
from mvcodec.decode import iter_decoded_frames

# 動く模様の 20 コマを PPM で書く (ブラウザ版と同じ形式)
import os
os.makedirs("/work/frames", exist_ok=True)
for t in range(20):
    y, x = np.mgrid[0:36, 0:64]
    img = np.stack([(x * 4 + t * 7) % 256, (y * 6 + t * 3) % 256, ((x + y) * 3) % 256], -1).astype(np.uint8)
    Image.fromarray(img).save(f"/work/frames/f{t:03d}.ppm")
counts = {}
for mode in ("screen", "relief"):
    out = f"/work/{mode}.js"
    assert convert.main(["--frames-dir", "/work/frames", "--output", out, "--width", "64", "--height", "36", "--fps", "10",
                         "--palette", "auto", "--dither-method", "blue_noise" if mode == "screen" else "none",
                         "--device", "cpu", "--threads", "1", "--mode", mode, "--depth", "8"]) == 0
    text = open(out, encoding="utf-8").read()
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    counts[mode] = sum(1 for _ in iter_decoded_frames(data))
    addon = pack_builder.build_addon_bytes([{"id": mode, "title": mode, "frames_js": text, "thumbnail": None, "sounds": []}],
                                           "Smoke", "smoke")
    assert "BP/scripts/frames_%s.js" % mode in zipfile.ZipFile(io.BytesIO(addon)).namelist()
# MagicaVoxel の立体アニメ (ブラウザ版は .vox をそのまま渡し、サムネイルも Python で作る)
from mvcodec.volume import write_vox
vox_palette = np.zeros((256, 4), np.uint8)
vox_palette[0] = (230, 40, 40, 255)
models = [((8, 3, 4), np.array([[x, y, z, 1] for x in range(t, t + 3) for y in range(3) for z in range(4)], np.uint8))
          for t in range(5)]
with open("/work/walk.vox", "wb") as f:
    f.write(write_vox(models, vox_palette))
thumb = pack_builder.vox_thumbnail_png("/work/walk.vox", 256, "auto")
assert convert.main(["--input-vox", "/work/walk.vox", "--mode", "vox", "--output", "/work/vox.js", "--fps", "10",
                     "--palette", "auto", "--device", "cpu", "--threads", "1"]) == 0
text = open("/work/vox.js", encoding="utf-8").read()
counts["vox"] = sum(1 for _ in iter_decoded_frames(json.loads(text[text.index("{"):text.rindex("}") + 1])))
pack_builder.build_addon_bytes([{"id": "walk", "title": "walk", "frames_js": text, "thumbnail": thumb, "sounds": []}],
                               "Smoke", "smoke")
# だまし絵 (--view anamorphic) の .vox は、閉じた空洞を埋める (Pyodide には scipy が無いので、numpy だけの処理が動く)
shell = np.array([[x, y, z, 1] for x in range(1, 8) for y in range(1, 8) for z in range(1, 8) if {x, y, z} & {1, 7}], np.uint8)
with open("/work/shell.vox", "wb") as f:
    f.write(write_vox([((9, 9, 9), shell)], vox_palette))
assert convert.main(["--input-vox", "/work/shell.vox", "--mode", "vox", "--view", "anamorphic", "--output", "/work/shell.js",
                     "--fps", "10", "--palette", "auto", "--device", "cpu", "--threads", "1"]) == 0
text = open("/work/shell.js", encoding="utf-8").read()
*_, (shell_volume, _rgb) = iter_decoded_frames(json.loads(text[text.index("{"):text.rindex("}") + 1]))
counts["shell"] = int((shell_volume != 127).sum())
# 画像（テクスチャ）再生方式。ブラウザ版と同じ PPM → PNG → 別のアドオン (web/worker.js の imageFrames と同じ経路)
from image_mode.build_image_addon import build_image_addon_bytes, image_namespace, image_pack_name
from image_mode.frame_extract import png_from_ppm, png_size

frames = [png_from_ppm(open(f"/work/frames/f{t:03d}.ppm", "rb").read(), 64, 36) for t in range(20)]
image_addon = build_image_addon_bytes([{"id": "clip", "title": "clip", "frames": frames,
                                        "width": 64, "height": 36, "fps": 10}],
                                      image_pack_name("Smoke"), image_namespace("smoke"),
                                      description="画像方式")
image_archive = zipfile.ZipFile(io.BytesIO(image_addon))
names = image_archive.namelist()
assert "BP/entities/clip.json" in names and "RP/render_controllers/clip.json" in names
assert png_size(image_archive.read("RP/textures/smoke_img/clip/frame_0.png")) == (64, 36)
counts["image_frames"] = sum(1 for name in names if name.startswith("RP/textures/smoke_img/clip/frame_"))
json.dumps(counts)
`);
const counts = JSON.parse(result);
console.log(JSON.stringify(counts));
// web/worker.js が画像方式のアドオンを作る runPython をそのまま実行する (global の名前の衝突や、
// JS の Uint8Array → Python の bytes の変換は 64bit の Python では検証できない)
const imageFrames = pyodide.runPython("frames").toJs(); // worker.js の imageFrames() と同じ形の Uint8Array の一覧
pyodide.globals.set("img_videos", pyodide.toPy([{ id: "clip", title: "clip", frames: imageFrames, width: 64, height: 36, fps: 10 }]));
pyodide.globals.set("img_pack_name", "Smoke");
pyodide.globals.set("img_namespace", "smoke");
pyodide.globals.set("img_lang", "en");
const imageAddon = pyodide.runPython(`
from gui_i18n import Translator
from image_mode.build_image_addon import build_image_addon_bytes, image_namespace, image_pack_name

videos = []
for v in img_videos:
    v = dict(v)
    videos.append({"id": str(v["id"]), "title": str(v["title"]),
                   "frames": [bytes(f) for f in v["frames"]],
                   "width": int(v["width"]), "height": int(v["height"]), "fps": float(v["fps"])})
build_image_addon_bytes(videos, image_pack_name(img_pack_name), image_namespace(img_namespace),
                        description=Translator(img_lang)("pack.image_desc"))
`).toJs(); // worker.js の done の imageAddon と同じ (Uint8Array)
console.log("image addon bytes", imageAddon.length);
if (imageAddon.length === 0) process.exit(1);
if (counts.screen !== 20 || counts.relief !== 20 || counts.vox !== 5) process.exit(1);
// 7×7×7 の殻の中の閉じた空洞 (5×5×5) が埋まって、詰まった立方体 (343 マス) になる
if (counts.shell !== 343) process.exit(1);
// 画像（テクスチャ）方式は 20 フレームの PNG が RP のテクスチャに並ぶ
if (counts.image_frames !== 20) process.exit(1);
// 最後の進み具合は「全部済んだ」(各変換の最後に必ず呼ばれる)
console.log("progress calls", progress.length);
if (progress.length === 0 || progress.some(([done, total]) => done > total)) process.exit(1);
