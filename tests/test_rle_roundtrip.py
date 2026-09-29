import sys
import os
import numpy as np

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mvcodec.encode import extract_rle_chunks_fast, encode_varint


def encode_frame(chunks, width):
    """convert.py と同様のチャンクエンコード (varint列を生成)"""
    encoded_data = bytearray()
    for start_x, y, length, color in chunks:
        delta = y * width + start_x
        data_val = (delta << 13) | ((length - 1) << 7) | (color & 0x7f)
        encode_varint(data_val, encoded_data)
    return encoded_data


def decode_frame(encoded_data, prev_frame, width, height):
    """main.js (applyBinarySlice) と同一のデコードロジック"""
    decoded = prev_frame.copy()
    pos = 0
    data_len = len(encoded_data)

    while pos < data_len:
        val = 0
        shift = 0
        while pos < data_len:
            b = encoded_data[pos]
            pos += 1
            val |= (b & 0x7f) << shift
            if (b & 0x80) == 0:
                break
            shift += 7

        delta = val >> 13
        length = ((val >> 7) & 0x3f) + 1
        level = val & 0x7f

        curr_idx = delta
        x = curr_idx % width
        y = curr_idx // width

        for i in range(length):
            decoded[y * width + (x + i)] = level

    return decoded


def test_roundtrip(width, height, flat_frame, prev_frame):
    chunks = extract_rle_chunks_fast(flat_frame, prev_frame, width, height)
    encoded = encode_frame(chunks, width)
    decoded = decode_frame(encoded, prev_frame, width, height)
    assert np.array_equal(decoded, flat_frame), (
        f"Roundtrip failed for {width}x{height}!\n"
        f"Mismatches: {np.sum(decoded != flat_frame)} / {width * height} pixels"
    )
    # lengthが全て1〜64の範囲内であることを確認
    for start_x, y, length, color in chunks:
        assert 1 <= length <= 64, f"Chunk length {length} out of range [1, 64] at ({start_x}, {y})"


def test_solid_frames():
    """幅128, 256の単色フレーム(全面同色)の往復テスト"""
    for size in [128, 256]:
        prev = np.full(size * size, -1, dtype=np.int32)
        frame = np.full(size * size, 5, dtype=np.int32)
        test_roundtrip(size, size, frame, prev)
        print(f"PASS: solid frame {size}x{size}")


def test_width_64_backward_compatibility():
    """幅64の既存挙動テスト (単色・境界長・ランダム)"""
    size = 64
    # 単色
    prev = np.full(size * size, -1, dtype=np.int32)
    frame = np.full(size * size, 3, dtype=np.int32)
    test_roundtrip(size, size, frame, prev)

    # 差分更新
    frame2 = frame.copy()
    frame2[100:164] = 7  # 64ピクセル更新 (1行分)
    test_roundtrip(size, size, frame2, frame)

    # ランダム
    rng = np.random.default_rng(42)
    frame_rand = rng.integers(0, 16, size=size * size, dtype=np.int32)
    test_roundtrip(size, size, frame_rand, prev)
    print("PASS: width 64 compatibility")


def test_boundary_run_lengths():
    """63, 64, 65, 128, 129, 200等の境界長ランのテスト"""
    width, height = 256, 10
    prev = np.full(width * height, 0, dtype=np.int32)

    for run_len in [63, 64, 65, 127, 128, 129, 192, 200, 256]:
        frame = prev.copy()
        frame[width * 2 + 10 : width * 2 + 10 + run_len] = 12
        test_roundtrip(width, height, frame, prev)
        print(f"PASS: run_length={run_len}")


if __name__ == "__main__":
    test_width_64_backward_compatibility()
    test_solid_frames()
    test_boundary_run_lengths()
    print("All tests passed successfully!")
