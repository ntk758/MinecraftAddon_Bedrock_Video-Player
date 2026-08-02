def encode_varint(val, byte_arr):
    while val >= 0x80:
        byte_arr.append((val & 0x7f) | 0x80)
        val >>= 7
    byte_arr.append(val & 0x7f)

def bytearray_to_utf16_str(byte_arr):
    if not byte_arr:
        return ""
    bits = "".join(f"{b:08b}" for b in byte_arr)
    
    rem = len(bits) % 15
    pad_len = (15 - rem) if rem != 0 else 0
    if pad_len > 0:
        bits += "0" * pad_len
        
    chars = [chr(0x1000 + pad_len)]
    for i in range(0, len(bits), 15):
        val_15 = int(bits[i:i+15], 2)
        chars.append(chr(0x1000 + val_15))
        
    return "".join(chars)

def extract_rle_chunks(frame, prev_frame, width, height, num_colors):
    chunks = []
    # 2D RLE (X軸方向走査)
    for y in range(height):
        x = 0
        while x < width:
            idx = y * width + x
            if frame[idx] != prev_frame[idx]:
                start_x = x
                color = frame[idx]
                length = 1
                while x + 1 < width and frame[idx + 1] == color:
                    x += 1
                    idx += 1
                    length += 1
                chunks.append((start_x, y, length, color))
            x += 1
    return chunks
