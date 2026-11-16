// ブラウザ版 (web/) が使う npm のライブラリを web/vendor/ に写す。GitHub Pages では npm を動かさないので、
// 配布物を写したものをリポジトリに入れ、外部のサーバー (CDN) からは読み込まない (web/vendor/wasm-media-encoders と同じ考え方)。
//
//   npm ci                              # package.json / package-lock.json の版を入れる
//   node scripts/vendor_web_libs.mjs    # web/vendor/motion と web/vendor/lucide を作り直す
//
// - Motion: 配布物のブラウザ用 (UMD、グローバルの Motion) をそのまま写す。React 用の API は使わない
// - Lucide: 画面で使うアイコン (ICONS) だけを取り出して self.LUCIDE_ICONS に入れる (全部の 4000 個余りは入れない)。
//   描くのは web/ui.js の UI.icon。使うアイコンを増やしたら ICONS に足してこのスクリプトを動かし直す
//   (tests/test_web_ui.py が、画面で使う名前と ICONS が一致することを確かめる)
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODULES = join(ROOT, "node_modules");
const VENDOR = join(ROOT, "web", "vendor");

export const ICONS = [
  "arrow-down", "box", "camera", "check", "chevron-down", "circle-check", "circle-dot", "circle-x", "cloud-download", "cpu",
  "download", "file-plus", "film", "gamepad-2", "globe", "hard-drive", "image", "info", "laptop", "layout-grid", "menu",
  "package", "palette", "pause", "person-standing", "play", "send", "settings", "sliders-horizontal", "square", "timer",
  "upload", "x", "zap",
];
// 見出しのフォント (Bricolage Grotesque、太さが可変。ラテン文字とその拡張だけ。OFL-1.1)
const FONT_PACKAGE = "@fontsource-variable/bricolage-grotesque";
const FONT_FILES = ["bricolage-grotesque-latin-wght-normal.woff2", "bricolage-grotesque-latin-ext-wght-normal.woff2"];

const sha256 = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const version = (name) => JSON.parse(readFileSync(join(MODULES, name, "package.json"), "utf8")).version;

// ---- Motion
const motionDir = join(VENDOR, "motion");
mkdirSync(motionDir, { recursive: true });
copyFileSync(join(MODULES, "motion", "dist", "motion.js"), join(motionDir, "motion.min.js"));
copyFileSync(join(MODULES, "motion", "LICENSE.md"), join(motionDir, "LICENSE.md"));

// ---- Lucide (使うアイコンだけ)
const lucideDir = join(VENDOR, "lucide");
mkdirSync(lucideDir, { recursive: true });
const icons = {};
for (const name of ICONS) {
  const module = await import(pathToFileURL(join(MODULES, "lucide", "dist", "esm", "icons", `${name}.mjs`)).href);
  icons[name] = module.default;
}
const lucideVersion = version("lucide");
writeFileSync(join(lucideDir, "icons.js"),
  `/**\n * Lucide v${lucideVersion} (ISC) の ${ICONS.length} 個のアイコン。scripts/vendor_web_libs.mjs が書いた (手で書き換えない)。\n` +
  ` * https://lucide.dev  ライセンスは同じフォルダの LICENSE。\n */\n` +
  `self.LUCIDE_ICONS = ${JSON.stringify(icons)};\n`, "utf8");
copyFileSync(join(MODULES, "lucide", "LICENSE"), join(lucideDir, "LICENSE"));

// ---- 見出しのフォント
const fontDir = join(VENDOR, "fonts");
mkdirSync(fontDir, { recursive: true });
for (const file of FONT_FILES) copyFileSync(join(MODULES, FONT_PACKAGE, "files", file), join(fontDir, file));
copyFileSync(join(MODULES, FONT_PACKAGE, "LICENSE"), join(fontDir, "LICENSE"));
const fontVersion = version(FONT_PACKAGE);
const fontRows = FONT_FILES.map((file) =>
  `| \`fonts/${file}\` | npm の ${FONT_PACKAGE} の \`files/\` (そのまま) | ${fontVersion}・OFL-1.1 | \`${sha256(join(fontDir, file))}\` |`).join("\n");

// ---- README (版と SHA-256。tests/test_web_ui.py が確かめる)
const motionVersion = version("motion");
writeFileSync(join(VENDOR, "README.md"), `# web/vendor の npm のライブラリ

ブラウザ版の画面 (web/ui.js) が使う。GitHub Pages では npm を動かさないので、配布物をここに写し、外部のサーバーからは読み込まない。
作り直すには \`npm ci\` のあと \`node scripts/vendor_web_libs.mjs\` (版は package.json / package-lock.json)。

| ファイル | 元 | 版・ライセンス | SHA-256 |
|---|---|---|---|
| \`motion/motion.min.js\` | npm の motion の \`dist/motion.js\` (そのまま) | ${motionVersion}・MIT | \`${sha256(join(motionDir, "motion.min.js"))}\` |
| \`lucide/icons.js\` | npm の lucide の \`dist/esm/icons/*.mjs\` から ${ICONS.length} 個 | ${lucideVersion}・ISC | \`${sha256(join(lucideDir, "icons.js"))}\` |
${fontRows}

- Motion は Vanilla JavaScript の API (\`Motion.animate\`・\`Motion.inView\`・\`Motion.stagger\`) だけを使う。読み込めなくても画面は CSS の動きで動く。
- wasm-media-encoders は別 (\`wasm-media-encoders/README.md\`)。
`, "utf8");

console.log(`motion ${motionVersion}, lucide ${lucideVersion} (${ICONS.length} icons) -> web/vendor`);
