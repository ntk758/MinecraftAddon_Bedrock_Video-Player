// ブラウザ版 (web/) と同じ Pyodide (32bit の WebAssembly の Python) で、convert.py と pack_builder.py を最後まで動かす。
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
json.dumps(counts)
`);
const counts = JSON.parse(result);
console.log(JSON.stringify(counts));
if (counts.screen !== 20 || counts.relief !== 20 || counts.vox !== 5) process.exit(1);
