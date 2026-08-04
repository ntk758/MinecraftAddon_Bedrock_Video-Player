# Research: Tile Dictionary Spatial Compression

## Executive Summary

This document describes the design and algorithm for a **Tile Dictionary Compression Scheme** tailored for rendering video in Minecraft Bedrock. 

In video sequences containing synthetic content, game scenes, or repetitive environments (such as grass fields, sky boxes, standard UI elements, or textures), large portions of the screen repeat identical spatial pixel patterns. By dividing each frame into fixed grid tiles (e.g., $16 \times 16$ or $8 \times 8$ pixels), hashing unique tiles into a shared **Tile Dictionary**, and referencing tiles by index, we eliminate spatial redundancy across both single frames and temporal frame sequences.

---

## 1. Concept & Motivation

### 1.1 Spatial Redundancy in Minecraft Content
In typical video playback scenarios inside Minecraft:
- Background terrain (e.g., green grass blocks, blue sky, stone walls) consists of repeated block textures.
- Static overlays and UI elements contain large uniform or repeating sub-grids.

Transmitting these duplicate block arrangements pixel-by-pixel wastes bandwidth. Instead of sending $256$ block palette indices for every $16 \times 16$ region, we can assign a $16$-bit **Tile ID** to unique $16 \times 16$ pattern blocks and transmit a grid of Tile IDs.

```
Raw Frame (128x64 pixels)              Tile Grid (8x4 Tiles of 16x16)
+------------------------+             +---+---+---+---+---+---+---+---+
| Sky | Sky | Sky | Sky  |             | T0| T0| T0| T0| T0| T0| T0| T0|
| Sky | Grass|Grass| Sky |   ----->    | T0| T1| T1| T0| T0| T1| T1| T0|
|Grass|Grass|Grass|Grass |             | T1| T1| T1| T1| T1| T1| T1| T1|
| Dirt| Dirt| Dirt| Dirt |             | T2| T2| T2| T2| T2| T2| T2| T2|
+------------------------+             +---+---+---+---+---+---+---+---+
                                       Dictionary: T0=Sky, T1=Grass, T2=Dirt
```

---

## 2. Encoder Pipeline Architecture (Python/Node.js)

The offline pre-processor / encoder analyzes the video input stream and builds dictionary definitions.

### 2.1 Grid Slicing & Hashing
1. Divide a video frame of dimension $W \times H$ into sub-tiles of size $S \times S$ (typically $S=16$ or $S=8$).
2. For each tile at position $(tx, ty)$:
   - Extract the $S \times S$ byte array of mapped block IDs.
   - Compute a fast cryptographic or non-cryptographic hash (e.g., FNV-1a, xxHash, or MD5) over the byte buffer:
     $$\text{Hash} = \text{xxHash64}(\text{TileBuffer})$$
3. Query the active frame dictionary map:
   - **If Hash exists**: Reuse existing `TileID`.
   - **If Hash is new**: Assign a new `TileID`, add the $S \times S$ block array to the dictionary payload, and register the hash.

### 2.2 Tile Dictionary Data Structure
The encoder produces two data streams per frame (or per scene segment):
1. **Dictionary Updates**: New tiles registered in this frame.
2. **Tile Map**: 2D array of Tile IDs matching the grid dimensions $(\frac{W}{S} \times \frac{H}{S})$.

---

## 3. Decoder Architecture in JavaScript (Bedrock Script API)

The in-game JavaScript runtime maintains an active dictionary storage and decodes tile maps into block updates.

### 3.1 Dictionary Management Data Structures

```typescript
const TILE_SIZE = 16;
const GRID_X = 8; // e.g., 128 / 16
const GRID_Y = 4; // e.g., 64 / 16

// Map of TileID -> Uint8Array(256) block color/type entries
const tileDictionary = new Map<number, Uint8Array>();

interface FramePacket {
  newTiles?: { id: number; data: number[] }[];
  tileGrid: number[]; // GRID_X * GRID_Y tile indices
}
```

### 3.2 JavaScript Decoding Logic

```typescript
function decodeTileFrame(packet: FramePacket, frameBuffer: Uint8Array): void {
  // 1. Register newly introduced dictionary entries
  if (packet.newTiles) {
    for (let i = 0; i < packet.newTiles.length; i++) {
      const tile = packet.newTiles[i];
      tileDictionary.set(tile.id, new Uint8Array(tile.data));
    }
  }

  // 2. Reconstruct screen buffer from tile grid
  const { tileGrid } = packet;
  
  for (let gy = 0; gy < GRID_Y; gy++) {
    for (let gx = 0; gx < GRID_X; gx++) {
      const tileId = tileGrid[gy * GRID_X + gx];
      const tileData = tileDictionary.get(tileId);

      if (!tileData) {
        console.warn(`Missing tile definition for tile ID: ${tileId}`);
        continue;
      }

      // Blit 16x16 tile data into main frame buffer
      const startX = gx * TILE_SIZE;
      const startY = gy * TILE_SIZE;

      for (let py = 0; py < TILE_SIZE; py++) {
        const destOffset = (startY + py) * (GRID_X * TILE_SIZE) + startX;
        const srcOffset = py * TILE_SIZE;
        
        // Copy 16 contiguous pixels
        frameBuffer.set(tileData.subarray(srcOffset, srcOffset + TILE_SIZE), destOffset);
      }
    }
  }
}
```

---

## 4. Bandwidth vs Parsing Efficiency

### 4.1 Theoretical Compression Ratios
For a $128 \times 64$ screen with $S=16$ ($8 \times 4 = 32$ total tiles):

- **Uncompressed Frame**: $128 \times 64 = 8,192$ bytes.
- **Tile Dictionary Frame (32 grid entries)**:
  - If 70% of tiles repeat (only 10 unique tiles):
  - Grid map: $32 \text{ entries} \times 2 \text{ bytes} = 64$ bytes.
  - New tile payload (first frame / keyframe): $10 \times 256 = 2,560$ bytes.
  - **Subsequent frames (zero new tiles)**: **64 bytes total payload** ($99.2\%$ compression ratio!).

### 4.2 Tradeoff Summary

| Metric | Raw Frame Transmission | Tile Dictionary Compression |
| :--- | :--- | :--- |
| **Payload Size (Static/Repeating)** | 8.1 KB / frame | ~0.1 KB - 0.5 KB / frame |
| **JS Decoding Speed** | Fast (linear byte copy) | Very Fast (32 block blits vs 8,192 lookups) |
| **Encoder Complexity** | O(1) trivial conversion | O(N) spatial hashing & block comparison |
| **Lossy Adaptations** | N/A | High (Perceptual tile hashing allows fuzzy block matching) |

---

## 5. Advanced Enhancements

1. **Global vs Local Dictionary**:
   - *Global*: Dictionary persists across entire video file (ideal for loopable animations, anime, or retro games).
   - *Local*: Dictionary flushed every keyframe to cap memory overhead.
2. **Lossy Tile Matching (Perceptual Hashing)**:
   - Use Mean Squared Error (MSE) or SSIM comparison between tiles to group visually indistinguishable tiles, boosting compression further.
