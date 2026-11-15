"""変換の既定値を 1 か所で定義する (データだけ。TODO.md の P2-6)。

- QUALITY_PRESETS: 画質のプリセット (幅, 高さ, fps)。Windows 版の画面 (video_player_gui.py) とブラウザ版 (web/app.js の同じ名前の表) が
  使う。ブラウザ版は JS の中に同じ表を持ち、tests/test_web_parity.py がこの表と一致することを確かめる。
- PROFILES: convert.py の --profile。
  - legacy: 初期 (v1〜v4) からの convert.py の既定。--profile を付けないときはこれ (今までの出力を変えない)。
  - recommended: Windows 版とブラウザ版の画面の既定 (プリセット「高画質」・自動パレット・Blue Noise)。
  個別の引数 (--width など) は、プロファイルより優先する。既定を recommended に変えるかは、次の大きな版で
  CHANGELOG と DECISIONS.md に書いてから決める。
"""

QUALITY_PRESETS = {
    "light": (64, 64, 20),
    "standard": (96, 96, 10),
    "high": (128, 128, 10),
    "detail": (128, 128, 5),
    "ultra": (256, 256, 5),
    "extreme": (512, 512, 2),
}
DEFAULT_PRESET = "high"
# 自動: 全ブロックの候補からシーンごとに色を選ぶ (docs/research/palette_space.md)
DEFAULT_PALETTE = "auto"
DEFAULT_DITHER = "blue_noise"

_RECOMMENDED_WIDTH, _RECOMMENDED_HEIGHT, _RECOMMENDED_FPS = QUALITY_PRESETS[DEFAULT_PRESET]

# convert.py の引数名 (argparse の dest) → 値
PROFILES = {
    "legacy": {"width": 64, "height": 64, "fps": 20.0, "palette": "full", "dither_method": "none"},
    "recommended": {"width": _RECOMMENDED_WIDTH, "height": _RECOMMENDED_HEIGHT, "fps": float(_RECOMMENDED_FPS),
                    "palette": DEFAULT_PALETTE, "dither_method": DEFAULT_DITHER},
}
DEFAULT_PROFILE = "legacy"
