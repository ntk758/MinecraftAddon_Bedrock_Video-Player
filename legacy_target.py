"""ブラウザ版だけが使う、旧版の Minecraft 向けのアドオンへの書き換え。

v6.32.0 から main.js は Script API 2.6.0 (Minecraft 1.26.10 以降) を使う。それより古い Minecraft でも遊べるよう、ブラウザ版は
同じ main.js の印 (// <legacy:名前> 〜 // </legacy:名前>) の範囲を 1.x の書き方に置き換えたアドオンも作れる。Windows 版は
このモジュールを使わない (出力は変わらない)。印が 1 組ずつ無ければ例外にする (main.js を変えて印を壊したら、テストで分かる)。

旧版で使えないもの: 独自のコマンド (チート無しの /<接頭辞>:setup など。/scriptevent はチートが要る)、TickingAreaManager
(tickingarea コマンドに戻す。ワールド全体の 10 区画の上限を使う)。
"""
import json
import re

# 出力先の Minecraft。id はブラウザ版の選択肢の値
TARGETS = {
    "latest": {"engine": (1, 26, 10), "server": "2.6.0", "ui": "2.0.0"},
    "legacy": {"engine": (1, 21, 80), "server": "1.19.0", "ui": "1.2.0"},
}

_IMPORTS = 'import { world, system, BlockPermutation, BlockVolume, ListBlockVolume } from "@minecraft/server";\n'

# v6.31.0 の main.js と同じ、tickingarea コマンドでの常時読み込み (TickingAreaManager は 2.6.0 から)
_TICKING = '''// 以前の版の区画を外して、tickingarea コマンドで常時読み込みの区画を登録する (旧版向け。上限はワールド全体で 10 区画)
const MAX_TICKING_AREAS = 8;

function removeRegisteredTickingAreas(dimension) {
  let names = [];
  try {
    names = JSON.parse(world.getDynamicProperty(TICKING_NAMES_KEY) ?? "[]");
  } catch (e) { /* 壊れた値は無視する */ }
  for (const name of Array.isArray(names) ? names : []) {
    try {
      dimension.runCommand(`tickingarea remove ${name}`);
    } catch (e) { /* 既に無い */ }
  }
  world.setDynamicProperty(TICKING_NAMES_KEY, "[]");
}

function ensureTickingArea(dimension, anchor, videoData) {
  if (!videoData) return { added: 0, needed: 0 };
  removeRegisteredTickingAreas(dimension);
  const prefix = `${TICKING_AREA_NAME}_${anchor.x}_${anchor.y}_${anchor.z}`;
  for (let i = 0; i < 16; i++) {
    try {
      dimension.runCommand(`tickingarea remove ${prefix}_${i}`);
    } catch (e) { /* 未登録 */ }
  }
  const box = worldBoxOf(videoData, anchor);
  const chunk0X = Math.floor(box.x0 / 16);
  const chunk0Z = Math.floor(box.z0 / 16);
  const chunk1X = Math.floor(box.x1 / 16);
  const chunk1Z = Math.floor(box.z1 / 16);
  const step = TICKING_AREA_CHUNKS_PER_SIDE;
  const areas = [];
  for (let cz = chunk0Z; cz <= chunk1Z; cz += step) {
    for (let cx = chunk0X; cx <= chunk1X; cx += step) {
      areas.push([Math.max(box.x0, cx * 16), Math.max(box.z0, cz * 16),
        Math.min(box.x1, (cx + step) * 16 - 1), Math.min(box.z1, (cz + step) * 16 - 1)]);
    }
  }
  let added = 0;
  const names = [];
  for (const [index, [fromX, fromZ, toX, toZ]] of areas.slice(0, MAX_TICKING_AREAS).entries()) {
    const name = `${prefix}_${index}`;
    try {
      dimension.runCommand(`tickingarea add ${fromX} ${anchor.y} ${fromZ} ${toX} ${anchor.y} ${toZ} ${name}`);
      names.push(name);
      added++;
    } catch (e) {
      console.warn(`[${EVENT_NAMESPACE}] tickingarea add failed: ${e}`);
    }
  }
  world.setDynamicProperty(TICKING_NAMES_KEY, JSON.stringify(names));
  return { added, needed: areas.length };
}

// コマンドで登録した区画はワールドを開き直しても残るので、再生のたびに登録し直さない
function ensureTickingAreaForPlayback(vp) {}
'''

_SLIDER = '''// フォームにスライダーを足す (@minecraft/server-ui 1.x の書き方)
function addSlider(form, label, minimum, maximum, step, value) {
  return form.slider(label, minimum, maximum, step, value);
}
'''

REPLACEMENTS = {"imports": _IMPORTS, "ticking": _TICKING, "slider": _SLIDER, "commands": ""}


def _replace_region(text: str, name: str, new: str) -> str:
    begin = re.compile(rf"^// <legacy:{name}>.*\n", re.M)
    end = re.compile(rf"^// </legacy:{name}>\n", re.M)
    starts, ends = list(begin.finditer(text)), list(end.finditer(text))
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].end():
        raise ValueError(f"main.js の印 legacy:{name} が 1 組ではありません ({len(starts)} / {len(ends)})")
    return text[:starts[0].start()] + new + text[ends[0].end():]


def main_text(text: str, target: str = "legacy") -> str:
    """main.js の文字列を target 向けにする。latest は印の行だけ残した元のまま。"""
    if target == "latest":
        return text
    if target not in TARGETS:
        raise ValueError(f"未知の出力先: {target}")
    text = text.replace("\r\n", "\n")
    for name, new in REPLACEMENTS.items():
        text = _replace_region(text, name, new)
    return text


def manifest_text(text: str, target: str = "legacy") -> str:
    """manifest.json の文字列の、必要な Minecraft の版と Script API の版を target に合わせる。"""
    spec = TARGETS[target]
    manifest = json.loads(text)
    manifest["header"]["min_engine_version"] = list(spec["engine"])
    versions = {"@minecraft/server": spec["server"], "@minecraft/server-ui": spec["ui"]}
    for dependency in manifest.get("dependencies", []):
        if dependency.get("module_name") in versions:
            dependency["version"] = versions[dependency["module_name"]]
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def lang_text(text: str, target: str = "legacy") -> str:
    """ゲーム内の案内のコマンド (/%1:setup など) を、旧版で使える /scriptevent %1:setup にする。"""
    if target == "latest":
        return text
    return re.sub(r"/%(\d)", r"/scriptevent %\1", text)
