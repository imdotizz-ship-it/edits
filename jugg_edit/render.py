"""Jugg-style COD highlight edit renderer.

Time-remaps gameplay onto a beat grid (velocity ramps, optical-flow slow-mo),
then layers camera shake, zoom punches, pseudo-3D camera moves, transitions,
glow, chromatic aberration, flashes and a colour grade. Output: 1080x1920 @ 60fps.

usage: python3 render.py CLIP MUSIC_WAV OUT_DIR
"""
import json
import math
import os
import subprocess
import sys
from multiprocessing import Pool

import cv2
import numpy as np

W, H = 1080, 1920
FPS = 60
SRC_FPS = 30.0
CONTENT_Y0, CONTENT_Y1 = 140, 1780          # source rows without the blurred bars
SRC_CX, SRC_CY = 540.0, (CONTENT_Y0 + CONTENT_Y1) / 2
BASE_SCALE = H / (CONTENT_Y1 - CONTENT_Y0) * 1.04
FOCAL = 1500.0

# ---- music grid ---------------------------------------------------------
PERIOD = 0.41788          # 143.58 bpm
DROP_MUSIC = 39.47        # drop position inside the trimmed song
BEATS_BEFORE_DROP = 32
M0 = DROP_MUSIC - BEATS_BEFORE_DROP * PERIOD
DROP_BEAT = 32
BASS_CUT_BEAT = 29
END_BEAT = 66
FADE_BEAT = 64.5


def bt(b):
    return b * PERIOD


T_END = bt(END_BEAT)

# scene ranges inside the source (hard cuts)
CLIPS = [(0.0, 3.26), (3.27, 9.72), (9.74, 27.86), (27.87, 33.36), (33.37, 37.99), (38.0, 87.3)]

# ---- edit decision list ---------------------------------------------------
# anchors: (source_time, beat, kind)  kind: cut | kill | drop | end
# kill_speed: absolute playback speed at the kill frame (slow-mo amount)
SHOTS = [
    dict(a=[(4.9, 0, "cut"), (6.20, 2, "kill"), (7.3, 4, "cut")], ks=0.35),
    dict(a=[(11.4, 4, "cut"), (12.80, 6, "kill"), (13.9, 8, "cut")], ks=0.35, tr="zoom"),
    dict(a=[(21.6, 8, "cut"), (22.70, 10, "kill"), (23.97, 12, "kill"), (24.9, 14, "cut")], ks=0.4, tr="whip"),
    dict(a=[(31.4, 14, "cut"), (32.80, 16, "kill"), (33.3, 18, "cut")], ks=0.3, tr="spin3d"),
    dict(a=[(33.6, 18, "cut"), (34.67, 20, "kill"), (35.9, 22, "cut")], ks=0.35, tr="glitch"),
    dict(a=[(38.2, 22, "cut"), (39.30, 24, "kill"), (40.4, 26, "cut")], ks=0.35, tr="zoom"),
    dict(a=[(70.9, 26, "cut"), (72.17, 28, "kill"), (74.70, 32, "drop"), (76.4, 36, "cut")], ks=0.4, tr="whip"),
    dict(a=[(42.7, 36, "cut"), (43.30, 37, "kill"), (44.34, 39, "kill"), (45.20, 41, "kill"), (46.0, 42, "cut")], ks=0.6, tr="zoom"),
    dict(a=[(47.7, 42, "cut"), (48.37, 43, "kill"), (49.00, 44, "kill"), (50.04, 46, "kill"), (50.9, 48, "cut")], ks=0.6, tr="spin3d"),
    dict(a=[(52.4, 48, "cut"), (53.07, 49, "kill"), (54.04, 51, "kill"), (55.54, 53, "kill"), (56.2, 54, "cut")], ks=0.6, tr="glitch"),
    dict(a=[(62.6, 54, "cut"), (63.14, 55, "kill"), (64.54, 57, "kill"), (65.2, 58, "cut")], ks=0.6, tr="whip"),
    dict(a=[(68.5, 58, "cut"), (69.00, 59, "kill"), (69.8, 60, "cut")], ks=0.6, tr="spin3d"),
    dict(a=[(81.3, 60, "cut"), (82.04, 61, "kill"), (84.6, 66, "end")], ks=0.3, tr="zoom"),
]
SLOPE = {"cut": 1.5, "end": 0.25}
TR_OUT, TR_IN = 0.12, 0.20     # transition half-windows (s)


# ---- time remap -------------------------------------------------------------
def shot_segments(shot):
    segs = []
    a = shot["a"]
    for (s0, b0, k0), (s1, b1, k1) in zip(a, a[1:]):
        e0, e1 = bt(b0), bt(b1)
        v = (s1 - s0) / (e1 - e0)

        def slope(kind):
            if kind == "kill":
                return shot["ks"] / v
            if kind == "drop":
                return 0.15 / v
            return SLOPE[kind]
        m0, m1 = slope(k0), slope(k1)
        n = math.hypot(m0, m1)
        if n > 2.9:                       # keep the Hermite curve monotonic
            m0, m1 = m0 * 2.9 / n, m1 * 2.9 / n
        segs.append((e0, e1, s0, s1, m0, m1))
    return segs


def src_time(segs, T):
    if T <= segs[0][0]:
        e0, e1, s0, s1, m0, _ = segs[0]
        return s0 + (T - e0) * m0 * (s1 - s0) / (e1 - e0)
    for e0, e1, s0, s1, m0, m1 in segs:
        if T <= e1:
            u = (T - e0) / (e1 - e0)
            f = (u**3 - 2 * u**2 + u) * m0 + (-2 * u**3 + 3 * u**2) + (u**3 - u**2) * m1
            return s0 + (s1 - s0) * f
    e0, e1, s0, s1, _, m1 = segs[-1]
    return s1 + (T - e1) * m1 * (s1 - s0) / (e1 - e0)


# ---- events -----------------------------------------------------------------
def build_events():
    kills, cuts = [], []
    for i, sh in enumerate(SHOTS):
        for s, b, k in sh["a"]:
            if k in ("kill", "drop"):
                kills.append((bt(b), k))
        if i > 0:
            cuts.append((bt(sh["a"][0][1]), sh.get("tr", "zoom")))
    return kills, cuts


KILLS, CUTS = build_events()


def ease_in(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def noise(t, seed, freq):
    # smooth pseudo-random signal in [-1, 1]
    r = np.random.RandomState(seed)
    fr = freq * (0.7 + 0.6 * r.rand(4))
    ph = r.rand(4) * 2 * math.pi
    return float(np.sum(np.sin(2 * math.pi * fr * t + ph)) / 2.2)


def fx_params(T):
    """All per-frame effect parameters at edit time T."""
    p = dict(z=1.0, dx=0.0, dy=0.0, rot=0.0, yaw=0.0, pitch=0.0, flash=0.0, ca=1.5,
             bloom=0.16, sat=1.06, expo=1.0, vig=0.35, glitch=0.0)
    beat = T / PERIOD

    # slow pseudo-3D camera drift (handheld virtual camera)
    p["yaw"] += 1.6 * math.sin(2 * math.pi * T / 3.3)
    p["pitch"] += 1.0 * math.sin(2 * math.pi * T / 4.1 + 1.0)
    p["rot"] += 0.6 * math.sin(2 * math.pi * T / 5.2 + 2.0)

    # beat bounce after the drop
    if beat >= DROP_BEAT:
        d = T - bt(math.floor(beat))
        p["z"] += 0.022 * math.exp(-d / 0.09)
        amp = 5.0 * math.exp(-d / 0.08)
        p["dx"] += amp * noise(T, 11, 22)
        p["dy"] += amp * noise(T, 12, 22)

    # build-up: bass cut before the drop
    if bt(BASS_CUT_BEAT) <= T < bt(DROP_BEAT):
        u = (T - bt(BASS_CUT_BEAT)) / (bt(DROP_BEAT) - bt(BASS_CUT_BEAT))
        p["sat"] *= 1 - 0.65 * u
        p["expo"] *= 1 - 0.22 * u
        p["vig"] += 0.45 * u
        p["z"] += 0.10 * ease_in(u) + 0.03 * u
        rumble = 4 + 10 * u
        p["dx"] += rumble * noise(T, 21, 30)
        p["dy"] += rumble * noise(T, 22, 30)
        p["bloom"] += 0.2 * u

    for tk, kind in KILLS:
        d = T - tk
        if d < -0.05 or d > 1.5:
            continue
        if kind == "drop":
            if d >= 0:
                e = math.exp(-d / 0.42)
                p["z"] += 0.20 * math.exp(-d / 0.30)
                p["dx"] += 75 * e * noise(T, 31, 16)
                p["dy"] += 75 * e * noise(T, 32, 16)
                p["rot"] += 4.5 * e * noise(T, 33, 12)
                p["pitch"] += 7 * math.exp(-d / 0.25)
                p["yaw"] += 4 * e * noise(T, 34, 6)
                p["flash"] += 1.0 * math.exp(-d / 0.16)
                p["ca"] += 22 * math.exp(-d / 0.30)
                p["bloom"] += 0.5 * math.exp(-d / 0.45)
                p["sat"] *= 1 + 0.12 * math.exp(-d / 0.5)
                p["glitch"] += 0.8 * math.exp(-d / 0.12)
        else:
            if d >= 0:
                e = math.exp(-d / 0.17)
                p["z"] += 0.065 * math.exp(-d / 0.14)
                p["dx"] += 24 * e * noise(T, 41 + int(tk * 10), 20)
                p["dy"] += 24 * e * noise(T, 42 + int(tk * 10), 20)
                p["rot"] += 1.3 * e * noise(T, 43 + int(tk * 10), 15)
                p["yaw"] += 2.5 * math.exp(-d / 0.2)
                p["flash"] += 0.22 * math.exp(-d / 0.06)
                p["ca"] += 7 * math.exp(-d / 0.18)
                p["bloom"] += 0.28 * math.exp(-d / 0.2)

    for tc, kind in CUTS:
        d = T - tc
        if -TR_OUT <= d < 0:          # outgoing shot
            u = ease_in((d + TR_OUT) / TR_OUT)
            if kind == "zoom":
                p["z"] *= 1 + 0.6 * u
            elif kind == "whip":
                p["dx"] -= 1.3 * W * u
                p["rot"] -= 6 * u
            elif kind == "spin3d":
                p["yaw"] += 55 * u
                p["rot"] += 20 * u
                p["z"] *= 1 + 0.3 * u
            elif kind == "glitch":
                p["glitch"] += 1.0 * u
                p["ca"] += 25 * u
                p["z"] *= 1 + 0.15 * u
        elif 0 <= d < TR_IN:          # incoming shot
            u = 1 - ease_out(d / TR_IN)
            if kind == "zoom":
                p["z"] *= 1 + 0.6 * u
                p["flash"] += 0.55 * math.exp(-d / 0.06)
            elif kind == "whip":
                p["dx"] += 1.3 * W * u
                p["rot"] += 6 * u
            elif kind == "spin3d":
                p["yaw"] -= 55 * u
                p["rot"] -= 20 * u
                p["z"] *= 1 + 0.3 * u
                p["flash"] += 0.25 * math.exp(-d / 0.06)
            elif kind == "glitch":
                p["glitch"] += 1.0 * u
                p["ca"] += 25 * u
                p["z"] *= 1 + 0.15 * u
            p["ca"] += 6 * u

    # intro flash and outro fade
    p["flash"] += 0.9 * math.exp(-T / 0.12)
    if T > bt(FADE_BEAT):
        p["expo"] *= max(0.0, 1 - (T - bt(FADE_BEAT)) / (T_END - bt(FADE_BEAT)))
    return p


def homography(p):
    z = BASE_SCALE * p["z"]
    r = math.radians(p["rot"])
    c, s = math.cos(r) * z, math.sin(r) * z
    A = np.array([[c, -s, W / 2 + p["dx"] - c * SRC_CX + s * SRC_CY],
                  [s, c, H / 2 + p["dy"] - s * SRC_CX - c * SRC_CY],
                  [0, 0, 1]])
    yaw, pitch = math.radians(p["yaw"]), math.radians(p["pitch"])
    Ry = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)], [0, math.sin(pitch), math.cos(pitch)]])
    K = np.array([[FOCAL, 0, W / 2], [0, FOCAL, H / 2], [0, 0, 1]])
    return K @ (Ry @ Rx) @ np.linalg.inv(K) @ A


def corner_motion(M0_, M1_):
    pts = np.array([[0, 0, 1], [W, 0, 1], [0, H, 1], [W, H, 1], [W / 2, H / 2, 1]], float).T
    i0 = np.linalg.inv(M0_) @ pts
    i1 = np.linalg.inv(M1_) @ pts
    i0, i1 = i0[:2] / i0[2], i1[:2] / i1[2]
    return float(np.max(np.hypot(*(i0 - i1)))) * BASE_SCALE


# ---- source frames + optical-flow interpolation -----------------------------
class Source:
    def __init__(self, path, t0, t1):
        self.i0 = max(0, int(math.floor(t0 * SRC_FPS)) - 2)
        n = int(math.ceil((t1 - self.i0 / SRC_FPS) * SRC_FPS)) + 3
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{self.i0 / SRC_FPS:.4f}", "-i", path,
               "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
        self.frames = np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080, 3)
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.flow_cache = {}
        gy, gx = np.mgrid[0:1920, 0:1080].astype(np.float32)
        self.gx, self.gy = gx, gy

    def nearest(self, t):
        k = int(round(t * SRC_FPS)) - self.i0
        return self.frames[min(max(k, 0), len(self.frames) - 1)]

    def _flows(self, k):
        if k not in self.flow_cache:
            if len(self.flow_cache) > 6:
                self.flow_cache.pop(next(iter(self.flow_cache)))
            a = cv2.cvtColor(cv2.resize(self.frames[k], (540, 960)), cv2.COLOR_BGR2GRAY)
            b = cv2.cvtColor(cv2.resize(self.frames[k + 1], (540, 960)), cv2.COLOR_BGR2GRAY)
            f01 = cv2.resize(self.dis.calc(a, b, None), (1080, 1920)) * 2
            f10 = cv2.resize(self.dis.calc(b, a, None), (1080, 1920)) * 2
            self.flow_cache[k] = (f01, f10)
        return self.flow_cache[k]

    def interp(self, t):
        fi = t * SRC_FPS - self.i0
        k = int(math.floor(fi))
        a = fi - k
        last = len(self.frames) - 1
        if k < 0:
            return self.frames[0]
        if k >= last:
            return self.frames[last]
        if a < 0.06:
            return self.frames[k]
        if a > 0.94:
            return self.frames[k + 1]
        f01, f10 = self._flows(k)
        ft0 = -(1 - a) * a * f01 + a * a * f10
        ft1 = (1 - a) ** 2 * f01 - a * (1 - a) * f10
        w0 = cv2.remap(self.frames[k], self.gx + ft0[..., 0], self.gy + ft0[..., 1],
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        w1 = cv2.remap(self.frames[k + 1], self.gx + ft1[..., 0], self.gy + ft1[..., 1],
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return cv2.addWeighted(w0, 1 - a, w1, a, 0)


# ---- look -------------------------------------------------------------------
def make_luts():
    x = np.arange(256) / 255.0
    curve = 0.5 + (np.tanh((x - 0.5) * 2.4) / np.tanh(1.2)) * 0.5       # S-curve
    curve = 0.92 * curve + 0.08 * x
    curve = np.clip(curve * 0.97 + 0.012, 0, 1)
    return (curve * 255).astype(np.uint8)


LUT = make_luts()
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
R2 = (((xx - W / 2) / (W / 2)) ** 2 * 0.6 + ((yy - H / 2) / (H / 2)) ** 2 * 0.8)
GRAIN = [np.random.RandomState(i).normal(0, 1, (H // 2, W // 2)).astype(np.float32) for i in range(6)]


def chroma_ab(img, px):
    if px < 0.5:
        return img
    k = px / 1100.0
    out = img.copy()
    for ch, sgn in ((2, 1), (0, -1)):
        s = 1 + sgn * k
        M = np.float32([[s, 0, W / 2 * (1 - s)], [0, s, H / 2 * (1 - s)]])
        out[..., ch] = cv2.warpAffine(img[..., ch], M, (W, H), borderMode=cv2.BORDER_REFLECT)
    return out


def glitch(img, amount, T):
    if amount < 0.05:
        return img
    r = np.random.RandomState(int(T * 60))
    out = img.copy()
    for _ in range(int(4 + 10 * amount)):
        y0 = r.randint(0, H - 40)
        h = r.randint(8, int(30 + 140 * amount))
        dx = int(r.randint(-1, 2) * amount * r.randint(20, 140))
        out[y0:y0 + h] = np.roll(out[y0:y0 + h], dx, axis=1)
        if r.rand() < 0.4:
            out[y0:y0 + h, :, r.randint(0, 3)] *= 0.4
    return out


def grade(img_u8, p, T):
    img = cv2.LUT(img_u8, LUT).astype(np.float32) / 255.0
    L = img[..., 0] * 0.114 + img[..., 1] * 0.587 + img[..., 2] * 0.299
    Ls = L[..., None]
    img = Ls + p["sat"] * (img - Ls)
    # split toning: teal shadows, warm highlights
    sh = (1 - L) ** 2
    hl = L ** 2
    img[..., 0] += 0.045 * sh - 0.025 * hl
    img[..., 1] += 0.018 * sh + 0.010 * hl
    img[..., 2] += -0.020 * sh + 0.040 * hl
    # bloom
    hi = np.maximum(img - 0.62, 0)
    small = cv2.resize(hi, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    b1 = cv2.GaussianBlur(small, (0, 0), 6)
    b2 = cv2.GaussianBlur(cv2.resize(small, (W // 16, H // 16), interpolation=cv2.INTER_AREA), (0, 0), 5)
    glow = cv2.resize(b1, (W, H)) + cv2.resize(b2, (W, H)) * 1.5
    img = img + glow * p["bloom"] * np.float32([1.0, 1.05, 1.15])
    img = chroma_ab(img, p["ca"])
    img = glitch(img, p["glitch"], T)
    img *= p["expo"] * (1 - p["vig"] * R2)[..., None]
    if p["flash"] > 0:
        f = min(p["flash"], 1.0)
        img = img * (1 - f) + f * np.float32([0.97, 0.98, 1.0]) + 0.15 * f * img
    g = cv2.resize(GRAIN[int(T * 60) % 6], (W, H), interpolation=cv2.INTER_NEAREST)
    img += (0.018 * g)[..., None]
    return np.clip(img * 255, 0, 255).astype(np.uint8)


# ---- render -----------------------------------------------------------------
def render_shot(args):
    idx, clip, out_dir = args
    shot = SHOTS[idx]
    segs = shot_segments(shot)
    e_in, e_out = bt(shot["a"][0][1]), bt(shot["a"][-1][1])
    if idx == len(SHOTS) - 1:
        e_out = T_END
    n0, n1 = math.ceil(e_in * FPS - 1e-6), math.ceil(e_out * FPS - 1e-6)
    ts = [src_time(segs, n / FPS) for n in range(n0, n1)]
    lo, hi = min(ts) - 0.08, max(ts) + 0.08
    cl = [c for c in CLIPS if c[0] - 0.01 <= shot["a"][0][0] <= c[1]][0]
    lo, hi = max(lo, cl[0]), min(hi, cl[1])
    src = Source(clip, lo, hi)
    clampt = lambda t: min(max(t, lo), hi)

    path = os.path.join(out_dir, f"shot_{idx:02d}.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "11", "-pix_fmt", "yuv420p",
                            "-threads", "2", path], stdin=subprocess.PIPE)
    half = 0.5 / FPS * 0.5          # 180-degree shutter
    for n in range(n0, n1):
        T = n / FPS
        Ma, Mb = homography(fx_params(T - half)), homography(fx_params(T + half))
        geo = corner_motion(Ma, Mb)
        span = abs(src_time(segs, T + half) - src_time(segs, T - half)) * SRC_FPS
        ns = int(min(10, max(1, math.ceil(geo / 7.0), min(6, math.ceil(span / 0.5)))))
        if ns == 1:
            p = fx_params(T)
            frame = cv2.warpPerspective(src.interp(clampt(src_time(segs, T))), homography(p), (W, H),
                                        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        else:
            acc = np.zeros((H, W, 3), np.float32)
            for j in range(ns):
                tj = T - half + 2 * half * (j + 0.5) / ns
                sf = src.interp(clampt(src_time(segs, tj)))
                acc += cv2.warpPerspective(sf, homography(fx_params(tj)), (W, H),
                                           flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            frame = (acc / ns).astype(np.uint8)
            p = fx_params(T)
        enc.stdin.write(grade(frame, p, T).tobytes())
    enc.stdin.close()
    enc.wait()
    return path, n1 - n0


def report():
    for i, sh in enumerate(SHOTS):
        segs = shot_segments(sh)
        e_in, e_out = bt(sh["a"][0][1]), bt(sh["a"][-1][1])
        ts = np.linspace(e_in, e_out, 200)
        ss = [src_time(segs, t) for t in ts]
        v = np.diff(ss) / np.diff(ts)
        print(f"shot {i:2d}  beats {sh['a'][0][1]:>2}-{sh['a'][-1][1]:>2}  src {min(ss):6.2f}-{max(ss):6.2f}"
              f"  speed {v.min():.2f}x .. {v.max():.2f}x")


if __name__ == "__main__":
    clip, music, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    report()
    if len(sys.argv) > 4 and sys.argv[4] == "--plan":
        sys.exit()
    only = [int(x) for x in sys.argv[5].split(",")] if len(sys.argv) > 5 else range(len(SHOTS))
    with Pool(int(os.environ.get("JOBS", "3"))) as pool:
        for path, n in pool.imap_unordered(render_shot, [(i, clip, out_dir) for i in only]):
            print("done", path, n, flush=True)
    json.dump(dict(M0=M0, T_END=T_END, drop=bt(DROP_BEAT), bass_cut=bt(BASS_CUT_BEAT),
                   cuts=[c[0] for c in CUTS], fade=bt(FADE_BEAT)),
              open(os.path.join(out_dir, "timeline.json"), "w"))
