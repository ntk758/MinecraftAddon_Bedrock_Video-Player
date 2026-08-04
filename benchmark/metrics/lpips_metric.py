try:
    import torch
    import lpips
    loss_fn_alex = lpips.LPIPS(net='alex')
except ImportError:
    loss_fn_alex = None

def calculate_lpips(img1, img2):
    if loss_fn_alex is None:
        return float('nan')
    t_orig = torch.from_numpy(img1).permute(2, 0, 1).unsqueeze(0).float() / 127.5 - 1.0
    t_dec = torch.from_numpy(img2).permute(2, 0, 1).unsqueeze(0).float() / 127.5 - 1.0
    with torch.no_grad():
        val = loss_fn_alex(t_orig, t_dec).item()
    return val
