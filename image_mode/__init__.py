"""画像（テクスチャ）再生方式 (docs/IMAGE_MODE.md)。

ブロック方式 (convert.py / main.js / codec.js / pack_builder.py) とは別の作り方で、フレームの PNG を Resource Pack に
同梱し、エンティティプロパティ `<namespace>:frame` と render controller のテクスチャ配列で再生する。

このフォルダはパッケージとして読み込む (Windows 版の EXE とブラウザ版の Python はこう使う)。
`python image_mode/build_image_addon.py` を直接実行したときは、上のフォルダを sys.path に足して同じ名前で読む。
"""
