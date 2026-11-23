"""MMD のモデル (PMX) を、姿勢・表情・カメラに合わせて 1 コマの絵に描く (Rendered Mode の描き手)。

numpy だけのソフトウェアラスタライザー (docs/research/mmd_dual_path_design_2026-11.md の 3.2)。Blender・OpenGL は使わない。
姿勢 (Poser・IK・物理)・モーフ・カメラは mvcodec.mmd と、画像モードの人物 (image_mode/rig_anim.PosedDance) と同じ物を使う。

描くもの: 頂点の変形 (BDEF1・BDEF2・BDEF4。SDEF は BDEF2、QDEF は BDEF4 として読まれる)、頂点モーフ、材質の色・テクスチャ・
透明 (はっきりした透明は捨て、半透明は奥から順に重ねる)、両面の印、トゥーン (モデルのトゥーンの画像。共有トゥーンは 2 段の陰で近づける)、
スフィア (乗算・加算)、鏡面、輪郭線 (エッジの印・太さ・色。裏返した殻)、背景 (色・画像・透明)、アンチエイリアス (縦横 N 倍で描いて縮める)。
描かないもの: セルフシャドウと地面の影、UV モーフ・材質モーフ、MME のエフェクト、追加 UV、サブテクスチャのスフィア。

座標は MMD のまま (左手系、y が上、モデルの正面は -Z)。カメラの空間は x が右・y が上・z が奥。画面は y が下。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from mvcodec import mmd

# MMD の既定の照明: 光の進む向きと色 (MMD の「照明」の初期値 154/255)
LIGHT_DIRECTION = (-0.5, -1.0, 0.5)
LIGHT_COLOUR = 154 / 255
FLAG_DOUBLE_SIDED = 0x01
FLAG_EDGE = 0x10
SPHERE_MULTIPLY, SPHERE_ADD = 1, 2
# これより不透明な画素は不透明として深さを書き、これより透明な画素は捨てる。間は半透明として重ねる
ALPHA_OPAQUE = 0.98
ALPHA_DISCARD = 0.02
# 輪郭線の太さ: エッジの太さ 1・頂点の倍率 1 で、高さ 720 の画面の何画素か (大きさが変わっても同じ見た目の太さ)
EDGE_PIXELS_AT_720 = 1.0
# カメラより手前のこの距離 (MMD の単位) までにある三角形は描かない (切り取りはしない)
NEAR = 0.5
# 1 回に調べる画素の候補の数 (メモリを抑える)。これより大きな三角形は行ごとに分ける
MAX_CANDIDATES = 2_000_000
# 共有トゥーン (MMD 本体の toon01〜10.bmp) は配れないので、陰の色を近い灰色にする
SHARED_TOON_SHADOW = (0.80, 0.80, 0.84)
TOON_SOFTNESS = 0.08


@dataclass
class Camera:
    """目・注視点・上の向き (MMD の座標) と、縦の視野角 (度)。"""
    eye: np.ndarray
    target: np.ndarray
    up: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0, 0.0]))
    fov: float = 30.0

    def basis(self) -> np.ndarray:
        """カメラの空間の軸 (右・上・奥) を行に並べた (3, 3)。"""
        forward = np.asarray(self.target, np.float64) - np.asarray(self.eye, np.float64)
        norm = np.linalg.norm(forward)
        if norm < 1e-9:
            raise ValueError("カメラの目と注視点が同じ場所です")
        forward /= norm
        right = np.cross(np.asarray(self.up, np.float64), forward)
        if np.linalg.norm(right) < 1e-9:  # 真上・真下を見るとき
            right = np.cross(np.array([0.0, 0.0, 1.0]), forward)
        right /= np.linalg.norm(right)
        up = np.cross(forward, right)
        return np.stack([right, up, forward])


def camera_from_vmd(keys, frame: float) -> Camera:
    """VMD のカメラのキーを frame (MMD の 30 fps のフレーム) で補間したカメラ。"""
    centre, rotation, distance, fov = mmd.sample_camera(keys, frame)
    eye = mmd.camera_eye(centre, rotation, distance)
    x, y, z = (-float(v) for v in rotation)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    up = ry @ rx @ rz @ np.array([0.0, 1.0, 0.0])
    return Camera(eye, np.asarray(centre, np.float64), up, float(max(1.0, min(170.0, fov))))


def framing_camera(points: np.ndarray, aspect: float, fov: float = 30.0, margin: float = 1.12) -> Camera:
    """points (N, 3) が全部入る、正面 (-Z) からのカメラ (カメラの VMD が無いとき)。"""
    lo, hi = points.min(axis=0), points.max(axis=0)
    centre = (lo + hi) / 2
    half_h = (hi[1] - lo[1]) / 2 * margin
    half_w = (hi[0] - lo[0]) / 2 * margin
    tan = math.tan(math.radians(fov) / 2)
    distance = max(half_h / tan, half_w / (tan * aspect)) + (hi[2] - lo[2]) / 2
    eye = centre + np.array([0.0, 0.0, -distance])
    return Camera(eye, centre, np.array([0.0, 1.0, 0.0]), fov)


@dataclass
class RenderSettings:
    width: int
    height: int
    supersample: int = 2
    background: tuple = (32, 36, 48, 255)   # RGBA (0〜255)。アルファ 0 で透明
    background_image: np.ndarray | None = None  # (H, W, 3) float 0〜1。あれば色の代わり
    edges: bool = True
    light_direction: tuple = LIGHT_DIRECTION
    light_colour: float = LIGHT_COLOUR


def _load_texture(path: Path):
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGBA"), np.float32) / 255.0
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _sample(texture: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """テクスチャ (H, W, 4) を uv (N, 2) で双線形に引く (繰り返し)。"""
    h, w = texture.shape[:2]
    x = uv[:, 0] * w - 0.5
    y = uv[:, 1] * h - 0.5
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0)[:, None].astype(np.float32)
    fy = (y - y0)[:, None].astype(np.float32)
    x0 = x0.astype(np.int64) % w
    y0 = y0.astype(np.int64) % h
    x1 = (x0 + 1) % w
    y1 = (y0 + 1) % h
    top = texture[y0, x0] * (1 - fx) + texture[y0, x1] * fx
    bottom = texture[y1, x0] * (1 - fx) + texture[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


class Scene:
    """描くモデル 1 つ (材質・テクスチャ・面) の準備。姿勢は毎コマ渡す。"""

    def __init__(self, model, base_dir, *, log=None):
        self.model = model
        self.warnings: list[str] = []
        base_dir = Path(base_dir)
        textures = []
        for name in model.textures:
            image = _load_texture(mmd.find_texture(base_dir, name))
            if image is None:
                self.warnings.append(f"テクスチャを読めませんでした: {name}")
            textures.append(image)
        self.textures = textures
        mats = model.materials
        count = len(mats)
        self.diffuse = np.array([m["diffuse"] for m in mats], np.float32).reshape(count, 4)
        self.ambient = np.array([m.get("ambient", (0.5, 0.5, 0.5)) for m in mats], np.float32).reshape(count, 3)
        self.specular = np.array([m.get("specular", (0, 0, 0)) for m in mats], np.float32).reshape(count, 3)
        self.shininess = np.array([m.get("shininess", 0.0) for m in mats], np.float32)
        self.flags = np.array([m.get("flags", 0) for m in mats], np.int64)
        self.edge_colour = np.array([m.get("edge_colour", (0, 0, 0, 1)) for m in mats], np.float32).reshape(count, 4)
        self.edge_size = np.array([m.get("edge_size", 0.0) for m in mats], np.float32)
        faces = model.faces
        self.face_material = np.zeros(len(faces), np.int64)
        start = 0
        for index, m in enumerate(mats):
            n = m["face_count"] // 3
            self.face_material[start:start + n] = index
            start += n

        def texture_of(index):
            return textures[index] if 0 <= index < len(textures) else None

        self.material_texture = [texture_of(m["texture"]) for m in mats]
        self.material_sphere = [texture_of(m.get("sphere", -1)) if m.get("sphere_mode", 0) in (SPHERE_MULTIPLY, SPHERE_ADD)
                                else None for m in mats]
        self.sphere_mode = [m.get("sphere_mode", 0) for m in mats]
        # トゥーン: モデルのトゥーンの画像なら、縦の 1 列 (上が明るい) を表にする。共有トゥーンは灰色の陰
        self.toon = []
        for m in mats:
            toon = texture_of(m.get("toon", -1))
            if toon is not None:
                column = toon[:, toon.shape[1] // 2, :3]
                self.toon.append(column)
            elif m.get("shared_toon", -1) >= 0:
                self.toon.append("shared")
            else:
                self.toon.append(None)
        # 透けるかもしれない材質 (色のアルファか、テクスチャのアルファ)
        self.may_be_transparent = np.array([
            self.diffuse[i, 3] < ALPHA_OPAQUE or (tex is not None and float(tex[..., 3].min()) < ALPHA_OPAQUE)
            for i, tex in enumerate(self.material_texture)], bool)
        self.winding = self._winding()
        if log:
            for message in self.warnings:
                log(f"注意: {message}")

    def _winding(self) -> float:
        """面の向き: 頂点の法線と同じ向きになる、面の頂点の回り方の符号 (PMX は普通は +1)。法線が意味の無いモデルでは +1。"""
        model = self.model
        normals = model.normals
        if normals is None or len(model.faces) == 0 or np.allclose(normals, normals[0]):
            return 1.0
        tri = model.positions[model.faces].astype(np.float64)
        geometric = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        vertex = normals[model.faces].sum(axis=1)
        agree = np.einsum("ij,ij->i", geometric, vertex)
        return 1.0 if (agree > 0).sum() >= (agree < 0).sum() else -1.0

    def posed(self, skin: np.ndarray, morph_weights: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
        """姿勢 skin (B, 4, 4) と表情の重みで、頂点と法線 (V, 3) を変形する (線形ブレンドスキニング)。"""
        model = self.model
        positions = model.positions.astype(np.float64)
        if morph_weights:
            positions = positions + mmd.morph_vertex_offsets(model, morph_weights)
        skin = np.asarray(skin, np.float64)
        blended = np.einsum("vk,vkij->vij", model.bone_weights.astype(np.float64), skin[model.bone_indices])
        out = np.einsum("vij,vj->vi", blended[:, :3, :3], positions) + blended[:, :3, 3]
        normals = model.normals if model.normals is not None else np.zeros_like(positions)
        n = np.einsum("vij,vj->vi", blended[:, :3, :3], normals.astype(np.float64))
        length = np.linalg.norm(n, axis=1, keepdims=True)
        n = np.where(length > 1e-9, n / np.maximum(length, 1e-9), np.array([0.0, 1.0, 0.0]))
        return out, n


@dataclass
class _Mesh:
    scene: Scene
    tri: np.ndarray        # (N,) モデルの面の番号
    back: np.ndarray       # (N,) 裏から見ている (両面の材質だけ残る)
    edge: np.ndarray       # (N,) 輪郭線の殻
    screen: np.ndarray     # (N, 3, 2) 画面の座標 (画素)
    depth: np.ndarray      # (N, 3) カメラの空間の z
    cam_vertices: np.ndarray
    cam_normals: np.ndarray


def _project(points: np.ndarray, width: int, height: int, focal: float) -> np.ndarray:
    z = points[..., 2]
    x = width / 2 + points[..., 0] / z * focal
    y = height / 2 - points[..., 1] / z * focal
    return np.stack([x, y], axis=-1)


def _build_mesh(scene: Scene, vertices: np.ndarray, normals: np.ndarray, camera: Camera, width: int, height: int,
                focal: float, edges: bool, edge_pixels: float) -> _Mesh:
    basis = camera.basis()
    cam = (vertices - np.asarray(camera.eye, np.float64)) @ basis.T
    cam_n = normals @ basis.T
    faces = scene.model.faces
    material = scene.face_material
    corners = cam[faces]                                  # (F, 3, 3)
    in_front = (corners[:, :, 2] > NEAR).all(axis=1)
    screen = _project(corners, width, height, focal)
    area = ((screen[:, 1, 0] - screen[:, 0, 0]) * (screen[:, 2, 1] - screen[:, 0, 1])
            - (screen[:, 2, 0] - screen[:, 0, 0]) * (screen[:, 1, 1] - screen[:, 0, 1]))
    # 画面は y が下なので、画面の上で時計回り (area > 0) がこちら向き (winding が +1 のとき)
    front = area * scene.winding > 0
    double = (scene.flags[material] & FLAG_DOUBLE_SIDED) != 0
    visible = in_front & (front | double) & (np.abs(area) > 1e-12)
    xs, ys = screen[..., 0], screen[..., 1]
    visible &= (xs.max(axis=1) >= 0) & (xs.min(axis=1) < width) & (ys.max(axis=1) >= 0) & (ys.min(axis=1) < height)
    tri = np.nonzero(visible)[0]
    parts_tri = [tri]
    parts_back = [~front[tri]]
    parts_edge = [np.zeros(len(tri), bool)]
    parts_screen = [screen[tri]]
    parts_depth = [corners[tri, :, 2]]
    if edges:
        size = scene.edge_size[material]
        has_edge = ((scene.flags[material] & FLAG_EDGE) != 0) & (size > 0) & in_front & (scene.edge_colour[material, 3] > 0)
        edge_tri = np.nonzero(has_edge)[0]
        if len(edge_tri):
            # 頂点を法線の向きへ、画面で edge_pixels × 太さ × 頂点の倍率の画素だけ広げる (遠くても同じ太さ)
            vscale = scene.model.edge_scales if scene.model.edge_scales is not None else np.ones(len(vertices))
            f = faces[edge_tri]
            push = (size[edge_tri, None] * vscale[f] * edge_pixels * corners[edge_tri, :, 2] / focal)[..., None]
            shell = corners[edge_tri] + cam_n[f] * push
            ok = (shell[:, :, 2] > NEAR).all(axis=1)
            shell_screen = _project(shell, width, height, focal)
            shell_area = ((shell_screen[:, 1, 0] - shell_screen[:, 0, 0]) * (shell_screen[:, 2, 1] - shell_screen[:, 0, 1])
                          - (shell_screen[:, 2, 0] - shell_screen[:, 0, 0]) * (shell_screen[:, 1, 1] - shell_screen[:, 0, 1]))
            keep = ok & (shell_area * scene.winding < 0) & (np.abs(shell_area) > 1e-12)  # 殻は裏の面だけ
            parts_tri.append(edge_tri[keep])
            parts_back.append(np.zeros(int(keep.sum()), bool))
            parts_edge.append(np.ones(int(keep.sum()), bool))
            parts_screen.append(shell_screen[keep])
            parts_depth.append(shell[keep, :, 2])
    return _Mesh(scene, np.concatenate(parts_tri), np.concatenate(parts_back), np.concatenate(parts_edge),
                 np.concatenate(parts_screen), np.concatenate(parts_depth), cam, cam_n)


def _rasterize(screen: np.ndarray, width: int, height: int):
    """三角形 (N, 3, 2) の中の画素の候補を、MAX_CANDIDATES ずつ出す: (三角形, 画素の番号, w0, w1, w2) (画面の重心座標)。"""
    xs, ys = screen[..., 0], screen[..., 1]
    x0 = np.clip(np.floor(xs.min(axis=1) - 0.5).astype(np.int64) + 1, 0, width - 1)
    x1 = np.clip(np.ceil(xs.max(axis=1) - 0.5).astype(np.int64), 0, width - 1)
    y0 = np.clip(np.floor(ys.min(axis=1) - 0.5).astype(np.int64) + 1, 0, height - 1)
    y1 = np.clip(np.ceil(ys.max(axis=1) - 0.5).astype(np.int64), 0, height - 1)
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    ok = (bw > 0) & (bh > 0)
    ids = np.nonzero(ok)[0]
    # 大きな三角形は、行を分けて複数の仕事にする
    rows_per_job = np.maximum(1, MAX_CANDIDATES // np.maximum(bw[ids], 1))
    jobs = np.ceil(bh[ids] / rows_per_job).astype(np.int64)
    job_tri = np.repeat(ids, jobs)
    first_job = np.cumsum(jobs) - jobs
    k = np.arange(len(job_tri)) - np.repeat(first_job, jobs)
    job_rows = np.repeat(rows_per_job, jobs)
    job_y0 = y0[job_tri] + k * job_rows
    job_h = np.minimum(job_rows, y1[job_tri] - job_y0 + 1)
    job_w = bw[job_tri]
    job_x0 = x0[job_tri]
    sizes = job_w * job_h
    # 辺の式 (w0 = a0 x + b0 y + c0 など) を三角形ごとに
    px, py = xs[job_tri], ys[job_tri]
    area = (px[:, 1] - px[:, 0]) * (py[:, 2] - py[:, 0]) - (px[:, 2] - px[:, 0]) * (py[:, 1] - py[:, 0])
    a0 = (py[:, 1] - py[:, 2]) / area
    b0 = (px[:, 2] - px[:, 1]) / area
    c0 = (px[:, 1] * py[:, 2] - px[:, 2] * py[:, 1]) / area
    a1 = (py[:, 2] - py[:, 0]) / area
    b1 = (px[:, 0] - px[:, 2]) / area
    c1 = (px[:, 2] * py[:, 0] - px[:, 0] * py[:, 2]) / area
    start = 0
    total = len(sizes)
    while start < total:
        cum = np.cumsum(sizes[start:])
        stop = start + max(1, int(np.searchsorted(cum, MAX_CANDIDATES, side="right")))
        stop = min(stop, total)
        sel = np.arange(start, stop)
        n = sizes[sel]
        job = np.repeat(sel, n)
        offset = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        w = job_w[job]
        x = job_x0[job] + offset % w
        y = job_y0[job] + offset // w
        cx = x + 0.5
        cy = y + 0.5
        w0 = a0[job] * cx + b0[job] * cy + c0[job]
        w1 = a1[job] * cx + b1[job] * cy + c1[job]
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if inside.any():
            yield (job_tri[job[inside]], y[inside] * width + x[inside], w0[inside], w1[inside], w2[inside])
        start = stop


def _fragment_alpha(mesh: _Mesh, tri: np.ndarray, bary: np.ndarray) -> np.ndarray:
    """透けるかもしれない材質の画素のアルファ (色のアルファ × テクスチャのアルファ)。輪郭線は輪郭線の色のアルファ。"""
    scene = mesh.scene
    face = mesh.tri[tri]
    material = scene.face_material[face]
    alpha = np.where(mesh.edge[tri], scene.edge_colour[material, 3], scene.diffuse[material, 3]).astype(np.float32)
    check = np.nonzero(~mesh.edge[tri] & scene.may_be_transparent[material])[0]
    if len(check):
        uv = np.einsum("nk,nkj->nj", bary[check], scene.model.uvs[scene.model.faces[face[check]]].astype(np.float64))
        for m in np.unique(material[check]):
            texture = scene.material_texture[m]
            if texture is None:
                continue
            sel = check[material[check] == m]
            local = np.nonzero(material[check] == m)[0]
            alpha[sel] *= _sample(texture[..., 3:4], uv[local])[:, 0]
    return alpha


def _shade(mesh: _Mesh, tri: np.ndarray, bary: np.ndarray, settings: RenderSettings,
           light_cam: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """画素の色 (N, 3) とアルファ (N,) (MMD の既定のシェーダーに近い式)。"""
    scene = mesh.scene
    face = mesh.tri[tri]
    material = scene.face_material[face]
    colour = np.zeros((len(tri), 3), np.float32)
    alpha = np.ones(len(tri), np.float32)
    edge = mesh.edge[tri]
    if edge.any():
        colour[edge] = scene.edge_colour[material[edge], :3]
        alpha[edge] = scene.edge_colour[material[edge], 3]
    body = np.nonzero(~edge)[0]
    if not len(body):
        return colour, alpha
    f = scene.model.faces[face[body]]
    b = bary[body]
    normal = np.einsum("nk,nkj->nj", b, mesh.cam_normals[f])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    normal[mesh.back[tri[body]]] *= -1
    position = np.einsum("nk,nkj->nj", b, mesh.cam_vertices[f])
    to_eye = -position / np.maximum(np.linalg.norm(position, axis=1, keepdims=True), 1e-9)
    uv = np.einsum("nk,nkj->nj", b, scene.model.uvs[f].astype(np.float64))
    to_light = -light_cam
    lit = np.clip(normal @ to_light, -1.0, 1.0)
    mat = material[body]
    light = settings.light_colour
    base = np.clip(scene.diffuse[mat, :3] * light + scene.ambient[mat], 0.0, 1.0)
    a = scene.diffuse[mat, 3].copy()
    for m in np.unique(mat):
        sel = np.nonzero(mat == m)[0]
        texture = scene.material_texture[m]
        if texture is not None:
            texel = _sample(texture, uv[sel])
            base[sel] *= texel[:, :3]
            a[sel] *= texel[:, 3]
        sphere = scene.material_sphere[m]
        if sphere is not None:
            n = normal[sel]
            suv = np.stack([n[:, 0] * 0.5 + 0.5, -n[:, 1] * 0.5 + 0.5], axis=1)
            s = _sample(sphere, suv)[:, :3]
            base[sel] = base[sel] * s if scene.sphere_mode[m] == SPHERE_MULTIPLY else base[sel] + s
        toon = scene.toon[m]
        if toon is not None:
            v = np.clip(0.5 - lit[sel] * 0.5, 0.0, 1.0)
            if isinstance(toon, str):
                t = np.clip((0.5 - v) / TOON_SOFTNESS + 0.5, 0.0, 1.0)[:, None]
                shade = t + (1 - t) * np.asarray(SHARED_TOON_SHADOW, np.float32)
            else:
                rows = np.clip((v * (len(toon) - 1)).astype(np.int64), 0, len(toon) - 1)
                shade = toon[rows]
            base[sel] *= shade
        if scene.shininess[m] > 0 and scene.specular[m].any():
            half = to_light + to_eye[sel]
            half /= np.maximum(np.linalg.norm(half, axis=1, keepdims=True), 1e-9)
            power = np.maximum(np.einsum("ij,ij->i", normal[sel], half), 0.0) ** scene.shininess[m]
            base[sel] += (scene.specular[m] * light)[None, :] * power[:, None]
    colour[body] = np.clip(base, 0.0, 1.0)
    alpha[body] = np.clip(a, 0.0, 1.0)
    return colour, alpha


def render(meshes: list[tuple[Scene, np.ndarray, np.ndarray]], camera: Camera, settings: RenderSettings) -> np.ndarray:
    """meshes ([(Scene, 頂点 (V, 3), 法線 (V, 3))…]) を camera から描いた RGBA (H, W, 4) の uint8。"""
    s = max(1, int(settings.supersample))
    width, height = settings.width * s, settings.height * s
    focal = (height / 2) / math.tan(math.radians(camera.fov) / 2)
    edge_pixels = EDGE_PIXELS_AT_720 * height / 720
    basis = camera.basis()
    light = np.asarray(settings.light_direction, np.float64)
    light_cam = (light / np.linalg.norm(light)) @ basis.T
    built = [_build_mesh(scene, v, n, camera, width, height, focal, settings.edges, edge_pixels)
             for scene, v, n in meshes]
    pixels = width * height
    zbuf = np.full(pixels, np.inf)
    owner = np.full(pixels, -1, np.int64)        # 一番手前の不透明な画素のメッシュ
    owner_tri = np.zeros(pixels, np.int64)
    owner_bary = np.zeros((pixels, 3), np.float32)
    clear = []  # 半透明の画素: (メッシュ, 三角形, 画素, 深さ, 重心座標)
    for index, mesh in enumerate(built):
        if not len(mesh.tri):
            continue
        for tri, pix, w0, w1, w2 in _rasterize(mesh.screen, width, height):
            z = mesh.depth[tri]
            inv = w0 / z[:, 0] + w1 / z[:, 1] + w2 / z[:, 2]
            depth = 1.0 / inv
            bary = np.stack([w0 / z[:, 0], w1 / z[:, 1], w2 / z[:, 2]], axis=1) / inv[:, None]
            near = depth < zbuf[pix]
            if not near.any():
                continue
            tri, pix, depth, bary = tri[near], pix[near], depth[near], bary[near]
            alpha = _fragment_alpha(mesh, tri, bary)
            solid = alpha >= ALPHA_OPAQUE
            soft = (alpha > ALPHA_DISCARD) & ~solid
            if soft.any():
                clear.append((index, tri[soft], pix[soft], depth[soft], bary[soft].astype(np.float32)))
            if not solid.any():
                continue
            tri, pix, depth, bary = tri[solid], pix[solid], depth[solid], bary[solid]
            order = np.lexsort((depth, pix))
            pix_sorted = pix[order]
            first = np.ones(len(order), bool)
            first[1:] = pix_sorted[1:] != pix_sorted[:-1]
            pick = order[first]
            better = depth[pick] < zbuf[pix[pick]]
            pick = pick[better]
            target = pix[pick]
            zbuf[target] = depth[pick]
            owner[target] = index
            owner_tri[target] = tri[pick]
            owner_bary[target] = bary[pick]
    # 背景 (色は乗算済みアルファで持つ)
    out = np.zeros((pixels, 4), np.float32)
    if settings.background_image is not None:
        image = np.asarray(Image.fromarray((np.clip(settings.background_image, 0, 1) * 255).astype(np.uint8)).resize(
            (width, height), Image.Resampling.BILINEAR), np.float32) / 255.0
        out[:, :3] = image.reshape(-1, 3)
        out[:, 3] = 1.0
    else:
        bg = np.asarray(settings.background, np.float32) / 255.0
        out[:, 3] = bg[3]
        out[:, :3] = bg[:3] * bg[3]
    for index, mesh in enumerate(built):
        sel = np.nonzero(owner == index)[0]
        if not len(sel):
            continue
        colour, _ = _shade(mesh, owner_tri[sel], owner_bary[sel].astype(np.float64), settings, light_cam)
        out[sel, :3] = colour
        out[sel, 3] = 1.0
    if clear:
        _composite(out, built, clear, zbuf, settings, light_cam)
    image = out.reshape(height, width, 4)
    if s > 1:
        image = image.reshape(settings.height, s, settings.width, s, 4).mean(axis=(1, 3))
    rgb = np.where(image[..., 3:4] > 1e-6, image[..., :3] / np.maximum(image[..., 3:4], 1e-6), 0.0)
    result = np.concatenate([rgb, image[..., 3:4]], axis=-1)
    return np.clip(np.round(result * 255), 0, 255).astype(np.uint8)


def _composite(out: np.ndarray, built: list[_Mesh], clear: list, zbuf: np.ndarray, settings: RenderSettings,
               light_cam: np.ndarray) -> None:
    """半透明の画素を、不透明な画素より手前のものだけ、奥から順に重ねる。"""
    mesh_id = np.concatenate([np.full(len(c[1]), c[0]) for c in clear])
    tri = np.concatenate([c[1] for c in clear])
    pix = np.concatenate([c[2] for c in clear])
    depth = np.concatenate([c[3] for c in clear])
    bary = np.concatenate([c[4] for c in clear])
    front = depth < zbuf[pix]
    mesh_id, tri, pix, depth, bary = mesh_id[front], tri[front], pix[front], depth[front], bary[front]
    if not len(pix):
        return
    colour = np.zeros((len(pix), 3), np.float32)
    alpha = np.zeros(len(pix), np.float32)
    for index in np.unique(mesh_id):
        sel = np.nonzero(mesh_id == index)[0]
        c, a = _shade(built[index], tri[sel], bary[sel].astype(np.float64), settings, light_cam)
        colour[sel], alpha[sel] = c, a
    order = np.lexsort((-depth, pix))  # 画素ごとに奥から
    pix, colour, alpha = pix[order], colour[order], alpha[order]
    first = np.ones(len(pix), bool)
    first[1:] = pix[1:] != pix[:-1]
    group_start = np.maximum.accumulate(np.where(first, np.arange(len(pix)), 0))
    rank = np.arange(len(pix)) - group_start
    for r in range(int(rank.max()) + 1):
        sel = rank == r
        p = pix[sel]
        a = alpha[sel, None]
        out[p, :3] = out[p, :3] * (1 - a) + colour[sel] * a
        out[p, 3] = out[p, 3] * (1 - a[:, 0]) + a[:, 0]
