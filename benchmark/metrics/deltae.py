try:
    from skimage.color import rgb2lab, deltaE_ciede2000
except ImportError:
    deltaE_ciede2000 = None
    rgb2lab = None

def calculate_deltae(img1, img2):
    if deltaE_ciede2000 is None or rgb2lab is None:
        return float('nan')
    lab1 = rgb2lab(img1)
    lab2 = rgb2lab(img2)
    delta_e = deltaE_ciede2000(lab1, lab2)
    return delta_e.mean()
