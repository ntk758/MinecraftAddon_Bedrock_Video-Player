try:
    from skimage.metrics import structural_similarity
except ImportError:
    structural_similarity = None

def calculate_ssim(img1, img2):
    if structural_similarity is None:
        return float('nan')
    return structural_similarity(img1, img2, channel_axis=-1, data_range=255)
