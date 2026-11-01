# wasm-media-encoders 0.7.0 (Ogg Vorbis)

ブラウザ版の変換ツールが、音声を Windows 版と同じ Ogg Vorbis にするために使う (web/app.js)。
npm の [wasm-media-encoders](https://www.npmjs.com/package/wasm-media-encoders) 0.7.0 (MIT、作者 arseneyr) の
配布物から、次の 2 つをそのまま写した (変更なし)。外部のサーバーからは読み込まない。

| ファイル | 元の場所 (npm の tarball) | SHA-256 |
|---|---|---|
| `WasmMediaEncoder.min.js` | `package/dist/umd/WasmMediaEncoder.min.js` | `dd4e17abf5377dfecc726d6ec5e7b72dab01cf3522974278e5347f4e68480fb3` |
| `ogg.wasm` | `package/wasm/ogg.wasm` | `cd1f50349e58e650e33e3f0b850a785ed612c4abaa4756e520e50b2583cf07ed` |

tarball: `https://registry.npmjs.org/wasm-media-encoders/-/wasm-media-encoders-0.7.0.tgz`。
値は tests/test_web_audio.py が確かめる。更新するときは、上の表とテストの値を一緒に書き換える。
