# Research: Motion Vector Compression in Minecraft Bedrock Video Playback

## Executive Summary

This research document details the design and theoretical impact of implementing **Motion Vector Estimation (H.264 style inter-frame compensation)** within the Minecraft Bedrock JavaScript Scripting API environment.

In traditional video streaming within Minecraft, inter-frame compression relies on simple delta encoding (transmitting only changed pixels). However, when the source video experiences camera panning, tilting, or continuous object movement, almost 100% of screen pixels change from frame to frame, causing delta frame sizes to explode. 

By maintaining an in-memory screen buffer (`Uint8Array`) within JavaScript, the decoder can perform **motion compensation** via commands such as `COPY dx, dy, length`. This transfers reference block regions from prior frame buffers to the active frame, drastically reducing JSON payload sizes over the network at the cost of JavaScript memory operations and array parsing.

---

## 1. Context & Architectural Challenge

### 1.1 The Network Payload Bottleneck
Minecraft Bedrock's Script API communicates frame update instructions to the world via command execution, block state setting, or dynamic structure updates. Transmitting payload updates via JSON websockets or script events encounters severe network/IPC limits:
- High frame bitrates lead to high packet counts and network jitter.
- Large JSON payload payloads incur significant serialization/deserialization CPU costs on both server and client.

### 1.2 Failure of Basic Delta Encoding
Under static camera angles, delta encoding (`frame[n] - frame[n-1]`) achieves high compression ratios (~80-95% reduction). However, during a horizontal pan:
$$\text{Pixel Differences} \approx 100\%$$
Even though the visual content is identical (shifted by $dx$ pixels), basic delta encoding treats every pixel as a newly modified color, re-transmitting the entire frame payload.

---

## 2. Motion Vector Concept for Minecraft

```
+------------------------------------+        +------------------------------------+
| Reference Frame (t-1)              |        | Current Frame (t)                  |
|                                    |        |                                    |
|    +---------+                     |        |            +---------+             |
|    | Object  |                     |-----\  |            | Object  |             |
|    | (x, y)  |                     |-----/  |            | (x+dx,  |             |
|    +---------+                     |        |            |   y+dy) |             |
|                                    |        |            +---------+             |
+------------------------------------+        +------------------------------------+
```

### 2.1 Macroblock Matching
The encoder divides the video frame into $N \times M$ macroblocks (e.g., $8 \times 8$ or $16 \times 16$ pixels). For each block in frame $t$:
1. The encoder searches a localized window in frame $t-1$ around position $(x, y)$.
2. If a matching block is found within an acceptable Sum of Absolute Differences (SAD) threshold, a **Motion Vector** $(dx, dy)$ is emitted instead of raw block pixel data.

### 2.2 Run-Length Command Encoding
Rather than sending explicit pixel colors or raw block palettes, the frame payload consists of a sequence of stream commands:
- **`COPY dx, dy, length`**: Copy a contiguous run of `length` pixels from the reference buffer offset by $(dx, dy)$.
- **`RAW [color_0, color_1, ...]`**: Fallback command for intra-coded macroblocks or newly exposed imagery where no motion candidate matches.

---

## 3. JavaScript Decoder Implementation

### 3.1 Dual-Buffer Strategy
The JavaScript runtime maintains two fixed-size byte buffers in memory:

```typescript
// Screen dimensions
const WIDTH = 128;
const HEIGHT = 72;
const TOTAL_PIXELS = WIDTH * HEIGHT;

// Dual Uint8Array buffers for block palette indices
const prevBuffer = new Uint8Array(TOTAL_PIXELS);
const currBuffer = new Uint8Array(TOTAL_PIXELS);
```

### 3.2 Command Processing Loop
When a frame packet arrives, JavaScript parses the encoded command stream and constructs `currBuffer` directly from `prevBuffer` and `RAW` payloads:

```typescript
type Command = 
  | { type: 'COPY'; dx: number; dy: number; length: number; destIndex: number }
  | { type: 'RAW'; data: Uint8Array; destIndex: number };

function decodeFrame(commands: Command[]): void {
  for (let i = 0; i < commands.length; i++) {
    const cmd = commands[i];
    
    if (cmd.type === 'COPY') {
      const { dx, dy, length, destIndex } = cmd;
      for (let offset = 0; offset < length; offset++) {
        const destPos = destIndex + offset;
        const destX = destPos % WIDTH;
        const destY = Math.floor(destPos / WIDTH);
        
        const srcX = destX - dx;
        const srcY = destY - dy;
        const srcPos = srcY * WIDTH + srcX;
        
        currBuffer[destPos] = prevBuffer[srcPos];
      }
    } else if (cmd.type === 'RAW') {
      currBuffer.set(cmd.data, cmd.destIndex);
    }
  }
  
  // Swap buffers for next frame reference
  prevBuffer.set(currBuffer);
}
```

---

## 4. Tradeoff & Performance Analysis

| Metric | Basic Delta Encoding | Motion Vector Encoding (`COPY` commands) |
| :--- | :--- | :--- |
| **Network Payload Size** | Extremely Large during motion (up to 50KB/frame) | Small during motion (1KB - 5KB/frame) |
| **JSON Deserialization** | Heavy JSON parsing of large arrays | Lightweight parsing of compact command tuples |
| **JS CPU Execution** | Low (direct array blitting) | Moderate to High (coordinate math & buffer lookup) |
| **Memory Footprint** | Single frame state | Dual buffer footprint ($2 \times W \times H$ bytes) |

### 4.1 Payload Reduction Ratio
During horizontal camera panning across complex terrain:
- **Uncompressed / Delta Frame Payload**: ~35,000 JSON characters.
- **Motion Vector Frame Payload**: ~1,200 JSON characters (`[COPY 2, 0, 8960]`).
- **Payload Compression Ratio**: **~96.5% reduction** in network payload.

### 4.2 JS Execution Overhead
While network transfer and JSON parsing time drop dramatically, array index lookups and coordinate calculations in JavaScript introduce CPU execution time:
- V8 / QuickJS array iteration over thousands of `COPY` lookups must be optimized using typed arrays (`Uint32Array` blitting) or linear memory offsets where possible.

---

## 5. Future Optimization Directions

1. **Sub-pixel Motion Vectors**: Half-pixel interpolation for smoother camera motion (increases JS processing cost).
2. **Linear Memory Offset `COPY`**: Encoding commands directly as linear buffer offset `COPY srcOffset, length` to eliminate $2D \rightarrow 1D$ coordinate math during runtime decoding.
3. **Hybrid Tile-Motion Architecture**: Combining motion vectors with spatial tile dictionaries for optimal bitrate adaptation.
