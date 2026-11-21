// 壊れた・書き換えたデータで、codec.js が回り続けたり巨大な配列を作ったりしない (外部の監査 #200)。
// tests/test_screen_system.py と同じく、tests/test_muse_runtime.py から `node --test` で実行する。
import { test } from "node:test";
import assert from "node:assert/strict";
import { decodeFrameInto, unpackGop } from "../../codec.js";

const HUGE = [0xff, 0xff, 0xff, 0xff, 0x0f]; // varint で 2^32 - 1

function video(extra = {}) {
  return { format: "varint_rle_v6", width: 8, height: 8, mv_block: 4, mv_range: 2, ...extra };
}

test("a huge motion vector count is refused instead of looping", () => {
  const bytes = new Uint8Array([3, ...HUGE]); // キーフレーム + 動きベクトル、数が 2^32 - 1
  const board = new Uint8Array(64);
  const out = new Uint8Array(64);
  const started = Date.now();
  assert.throws(() => decodeFrameInto(bytes, 0, bytes.length, board, out, video()), /motion vectors/);
  assert.ok(Date.now() - started < 1000);
});

test("motion vectors that run past the frame are refused", () => {
  const bytes = new Uint8Array([3, 2, 0]); // 2 個と言って 1 個の途中まで
  assert.throws(() => decodeFrameInto(bytes, 0, bytes.length, new Uint8Array(64), new Uint8Array(64), video()), /past the frame/);
});

test("a huge stream size is refused before allocating", () => {
  const container = new Uint8Array([1, 1, 1, ...HUGE]);
  assert.throws(() => unpackGop(container), /symbols in one stream/);
});
