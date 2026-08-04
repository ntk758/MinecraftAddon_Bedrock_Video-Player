def generate_report(results):
    encode_time = results.get("encode_time", 0)
    size_mb = results.get("size_mb", 0)
    fps = results.get("fps", 0)
    
    blocks = min(int(encode_time), 50)
    time_viz = "█" * blocks + (f" (+ {int(encode_time)-50}s)" if encode_time > 50 else "")
    
    print("\n### Benchmark Results\n")
    print("| Metric | Value |")
    print("|---|---|")
    print(f"| **Encode Time (s)** | {encode_time:.2f} <br/> `{time_viz}` |")
    print(f"| **Output Size (MB)** | {size_mb:.2f} |")
    print(f"| **Average PSNR** | {results.get('psnr', float('nan')):.2f} |")
    print(f"| **Average SSIM** | {results.get('ssim', float('nan')):.4f} |")
    print(f"| **Average MS-SSIM** | {results.get('ms_ssim', float('nan')):.4f} |")
    if 'lpips' in results:
        print(f"| **Average LPIPS** | {results['lpips']:.4f} |")
    print(f"| **Average ΔE2000** | {results.get('deltae', float('nan')):.2f} |")
    print(f"| **FPS Evaluated** | {fps} |")
