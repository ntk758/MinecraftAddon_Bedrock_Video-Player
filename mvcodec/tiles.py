"""タイル辞書 (Tile Dictionary)。

盤面を tile×tile のタイルに区切り、過去に表示したタイルの内容を辞書に覚えておく。同じ内容のタイルが
再び現れたら「辞書の何番目を貼る」という短い命令で送る (背景・文字・キャラクターの再登場、
ディザ模様が元に戻る場面などで効く)。

辞書はエンコーダとデコーダ (mvcodec/decode.py, codec.js) が同じ規則で作るので、辞書そのものは送らない:
  - キーフレーム: 辞書を空にしてから、そのフレームの全タイルを登録
  - それ以外: 直前の盤面から内容が変わったタイルを、行優先の順に登録
  - 盤面の端からはみ出すタイル (幅や高さが tile の倍数でない部分) は対象外
  - 辞書が DICT_MAX_ENTRIES に達したら、次のキーフレームまで登録しない
参照は「新しい方から数えて何番目か」(0 が最新) で表す。
"""
import numpy as np

TILE_SIZE = 8
DICT_MAX_ENTRIES = 32768
# 予測とこの数以上の画素が違うタイルだけを辞書参照の候補にする (少ない違いはランの方が安い)
MIN_MISMATCH = 4


class TileDictionary:
    def __init__(self, width, rows, tile=TILE_SIZE, max_entries=DICT_MAX_ENTRIES, index_contents=True):
        self.width = width
        self.rows = rows
        self.tile = tile
        self.tiles_x = width // tile
        self.tiles_y = rows // tile
        self.max_entries = max_entries
        self.index_contents = index_contents  # エンコーダは内容 → 番号の逆引きを使う
        self.reset()

    def reset(self):
        self.entries = []
        self.latest = {}

    def __len__(self):
        return len(self.entries)

    def tile_view(self, board2d, t):
        ty, tx = divmod(t, self.tiles_x)
        s = self.tile
        return board2d[ty * s:(ty + 1) * s, tx * s:(tx + 1) * s]

    def _add(self, content):
        if len(self.entries) >= self.max_entries:
            return
        content = np.ascontiguousarray(content, dtype=np.int16)
        if self.index_contents:
            self.latest[content.tobytes()] = len(self.entries)
        self.entries.append(content)

    def update(self, prev, new, is_keyframe):
        """フレームを表示した後に呼ぶ。prev は直前の盤面 (キーフレームでは使わない)。"""
        new2d = np.asarray(new).reshape(self.rows, self.width)
        if is_keyframe:
            self.reset()
            for t in range(self.tiles_x * self.tiles_y):
                self._add(self.tile_view(new2d, t))
            return
        prev2d = np.asarray(prev).reshape(self.rows, self.width)
        for t in range(self.tiles_x * self.tiles_y):
            tile = self.tile_view(new2d, t)
            if not np.array_equal(tile, self.tile_view(prev2d, t)):
                self._add(tile)

    def recency_of(self, content):
        """同じ内容の一番新しい登録の「新しい方から何番目か」。無ければ None。"""
        index = self.latest.get(np.ascontiguousarray(content, dtype=np.int16).tobytes())
        return None if index is None else len(self.entries) - 1 - index

    def get(self, recency):
        return self.entries[len(self.entries) - 1 - recency]

    def find_ops(self, target, predicted):
        """予測に貼ると得になるタイル参照 [(タイル番号, recency)] を探し、貼った後の予測を返す。"""
        target2d = np.asarray(target).reshape(self.rows, self.width)
        pred2d = np.asarray(predicted).reshape(self.rows, self.width).copy()
        ops = []
        for t in range(self.tiles_x * self.tiles_y):
            tile = self.tile_view(target2d, t)
            ptile = self.tile_view(pred2d, t)
            if int((tile != ptile).sum()) < MIN_MISMATCH:
                continue
            recency = self.recency_of(tile)
            if recency is None:
                continue
            ops.append((t, recency))
            ptile[...] = tile
        return ops, pred2d.reshape(-1)

    def apply_ops(self, out, ops):
        """デコーダ側: 予測 out (1 次元) にタイル参照を貼る。"""
        out2d = out.reshape(self.rows, self.width)
        for t, recency in ops:
            self.tile_view(out2d, t)[...] = self.get(recency)
        return out
