import numpy as np

def calculate_psnr(img1, img2):
    """Calculate Peak Signal-to-Noise Ratio (PSNR) between two images."""
    img1 = np.asarray(img1, dtype=np.float64)
    img2 = np.asarray(img2, dtype=np.float64)
    mse = np.mean((img1 - img2) ** 2)
    if mse == 0:
        return float('inf')
    max_pixel = 255.0
    psnr = 20 * np.log10(max_pixel / np.sqrt(mse))
    return psnr

def calculate_ssim_global(img1, img2, c1=6.5025, c2=58.5225):
    """
    Fast global SSIM approximation without OpenCV.
    For more accurate local SSIM, a sliding window with Gaussian weighting is needed,
    but this provides a good enough relative metric for RDO and Auto Benchmark.
    """
    img1 = np.asarray(img1, dtype=np.float64)
    img2 = np.asarray(img2, dtype=np.float64)
    
    mu1 = img1.mean()
    mu2 = img2.mean()
    
    sigma1_sq = np.var(img1)
    sigma2_sq = np.var(img2)
    sigma12 = np.cov(img1.flatten(), img2.flatten())[0, 1]
    
    ssim = ((2 * mu1 * mu2 + c1) * (2 * sigma12 + c2)) / ((mu1**2 + mu2**2 + c1) * (sigma1_sq + sigma2_sq + c2))
    return ssim
