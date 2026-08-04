# Standardized Video Player Evaluation Dataset (Bedrock Video Suite)

## Overview & Purpose

To quantitatively benchmark, evaluate, and compare video codec implementations, compression algorithms, and runtime performance in the Minecraft Bedrock Video Player, we define a standardized **Evaluation Dataset Suite**.

Without a fixed set of test sequences, codec metrics like bitrate reduction, decoding FPS, and visual degradation cannot be objectively compared between releases. This dataset represents diverse visual patterns, motion profiles, and edge-case scenes typical of real-world video content.

---

## 1. Test Sequence Clips Specification

The suite consists of six standardized 10-second test video clips encoded at standard resolution targets ($128 \times 72$ @ 20 FPS and $256 \times 144$ @ 30 FPS).

| Clip ID | Target Content Type | Primary Visual Characteristics | Target Stress Test Purpose |
| :--- | :--- | :--- | :--- |
| **`anime`** | 2D Animation / Cel Shading | Bold line art, flat color regions, discrete color palettes | Tests spatial tile dictionary deduplication & flat color run-length encoding. |
| **`movie`** | Live Action Cinema | Low light scenes, subtle lighting changes, organic film noise | Tests low-contrast noise tolerance, dark palette mapping, & intra-frame stability. |
| **`game`** | High-Motion Gameplay | Fast 3D camera rotation, continuous background scrolling | Tests motion vector estimation (`COPY dx, dy, length`) & temporal delta efficiency. |
| **`ui_text`** | Graphical UI & Typography | Sharp vector edges, small high-contrast text fonts | Tests legibility retention, high-frequency boundary preservation, & sub-pixel aliasing. |
| **`gradient`** | Synthetic Color Ramps | Smooth dusk/dawn skies, radial gradient lighting | Tests block palette quantization artifacts, dithering performance, & color banding. |
| **`stress`** | High-Entropy Particle Noise | Falling snow, confetti, rain, chaotic particles | Tests worst-case bitrate ceiling, buffer overflow handling, & dynamic fallback mechanisms. |

---

## 2. Objective Quality Evaluation Metrics

When testing a new codec version or optimization pass against the benchmark dataset, the pre-processor and playback logger compute three core objective visual metrics:

### 2.1 SSIM (Structural Similarity Index Measure)
- **Definition**: Evaluates structural integrity, luminance, and contrast similarity between the original video frame and the block-mapped rendered frame inside Minecraft.
- **Formula**:
  $$\text{SSIM}(x, y) = \frac{(2\mu_x\mu_y + c_1)(2\sigma_{xy} + c_2)}{(\mu_x^2 + \mu_y^2 + c_1)(\sigma_x^2 + \sigma_y^2 + c_2)}$$
- **Target Threshold**: $\text{SSIM} \ge 0.85$ for general content; $\text{SSIM} \ge 0.92$ for `ui_text`.

### 2.2 LPIPS (Learned Perceptual Image Patch Similarity)
- **Definition**: Measures deep perceptual distance using feature activation maps. Unlike pixel-wise MSE, LPIPS aligns closely with human visual preference when block textures substitute real pixel colors.
- **Target Threshold**: Lower score indicates higher perceptual similarity ($\text{LPIPS} \le 0.18$).

### 2.3 $\Delta E_{00}$ (CIEDE2000 Color Difference)
- **Definition**: Quantifies the color error introduced when mapping 24-bit RGB video colors to Minecraft's discrete block palette (e.g., Concrete, Wool, Terracotta IDs).
- **Formula**: Evaluates perceptual difference in $L^*a^*b^*$ color space.
- **Target Threshold**: $\Delta E_{00} \le 3.0$ across active palette selections.

---

## 3. Benchmarking Framework & Protocol

```
+------------------+     +------------------------+     +------------------------+
| Test Sequence    | --> | Codec Under Test (CUT) | --> | Reconstructed Frame    |
| (e.g. `anime`)   |     +------------------------+     +------------------------+
+------------------+                 |                              |
         |                           v                              v
         |                 +------------------+           +------------------+
         +---------------->| Compute Metrics  |<----------| Bedrock Renderer |
                           | SSIM/LPIPS/DeltaE|           | Buffer Snapshot  |
                           +------------------+           +------------------+
```

### 3.1 Standard Test Execution Steps
1. **Source Normalization**: Input clip scaled to target resolution ($128 \times 72$ @ 20 FPS, 1:1 pixel aspect ratio, uncompressed YUV420p / PNG frames).
2. **Codec Encoding Pass**: Execute encoding pipeline to generate `.mcvideo` / JSON packet stream.
3. **Metrics Calculation**:
   - Calculate output **File Size (KB)** and average **Bitrate (kbps)**.
   - Run offline metric evaluator (`SSIM`, `LPIPS`, $\Delta E_{00}$) frame-by-frame against source PNGs.
4. **Bedrock Playback Benchmark**:
   - Load frame stream in Minecraft Bedrock Script API runtime.
   - Record **JS Decode Time per Frame (ms)**, **Tick Delay Rate**, and **Memory Footprint (MB)**.

---

## 4. Benchmark Reporting Template

Every proposed pull request or codec change must attach a benchmark summary table adhering to the standard dataset format:

```markdown
### Codec Benchmark Summary (v2.1-motion-vectors vs v2.0-baseline)

| Clip ID | Baseline Bitrate | CUT Bitrate | Bitrate Delta | Baseline SSIM | CUT SSIM | JS Parse Time |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `anime` | 420 kbps | 85 kbps | **-79.7%** | 0.91 | 0.90 | 1.1 ms |
| `movie` | 510 kbps | 340 kbps | **-33.3%** | 0.84 | 0.84 | 1.8 ms |
| `game` | 680 kbps | 190 kbps | **-72.0%** | 0.88 | 0.87 | 1.4 ms |
| `ui_text` | 310 kbps | 290 kbps | **-6.4%** | 0.95 | 0.95 | 0.8 ms |
| `gradient`| 280 kbps | 260 kbps | **-7.1%** | 0.89 | 0.89 | 0.9 ms |
| `stress` | 950 kbps | 820 kbps | **-13.6%** | 0.79 | 0.78 | 2.6 ms |
```
