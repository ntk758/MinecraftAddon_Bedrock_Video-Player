# web/vendor の npm のライブラリ

ブラウザ版の画面 (web/ui.js) が使う。GitHub Pages では npm を動かさないので、配布物をここに写し、外部のサーバーからは読み込まない。
作り直すには `npm ci` のあと `node scripts/vendor_web_libs.mjs` (版は package.json / package-lock.json)。

| ファイル | 元 | 版・ライセンス | SHA-256 |
|---|---|---|---|
| `motion/motion.min.js` | npm の motion の `dist/motion.js` (そのまま) | 14.14.0・MIT | `521cfef8910fdd997463ea9881d4b790e017331e3d2f79c02adfc4d5fe1f2cae` |
| `lucide/icons.js` | npm の lucide の `dist/esm/icons/*.mjs` から 34 個 | 1.73.0・ISC | `f5a7979cfa745a941d765e76ded0dab6b58a6a18c341ebbf5ad6e7c2641496bb` |
| `fonts/bricolage-grotesque-latin-wght-normal.woff2` | npm の @fontsource-variable/bricolage-grotesque の `files/` (そのまま) | 5.3.0・OFL-1.1 | `a97804dc9fbe5fc972a08018c5eda4dab7ef2346f64c57e61419d05e6de4ea1c` |
| `fonts/bricolage-grotesque-latin-ext-wght-normal.woff2` | npm の @fontsource-variable/bricolage-grotesque の `files/` (そのまま) | 5.3.0・OFL-1.1 | `e808f16b56f84dc7933e498cc8a41d4a5cd853adc6eef154584baa5de1008b15` |

- Motion は Vanilla JavaScript の API (`Motion.animate`・`Motion.inView`・`Motion.stagger`) だけを使う。読み込めなくても画面は CSS の動きで動く。
- wasm-media-encoders は別 (`wasm-media-encoders/README.md`)。
