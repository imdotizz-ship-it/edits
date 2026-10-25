"""Jugg edit v2 - modelled on the Kaiser reference.

16:9 gameplay band centred on a 9:16 canvas over a blurred copy of itself,
fast velocity pacing (short brakes on kills), cold blue grade with white bloom,
clean flash / whip cuts and a 3D freeze-frame on the drop. 1080x1920 @ 60fps.

usage: python3 render_v2.py CLIP MUSIC_WAV OUT_DIR [--plan | --only i,j]
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
BW, BH = 1080, 608                  # 16:9 band
BAND_Y = (H - BH) // 2
FPS = 60
SRC_FPS = 30.0
CROP_Y0, CROP_Y1 = 520, 1400         # source rows kept in memory (band +- margin)
SRC_CX, SRC_CY = 540.0, 960.0 - CROP_Y0
BASE_SCALE = 1.05
FOCAL = 1100.0

PERIOD = 0.41788                    # 143.58 bpm
DROP_MUSIC = 39.47
M0 = DROP_MUSIC - 32 * PERIOD
DROP_BEAT, BASS_CUT_BEAT, END_BEAT, FADE_BEAT = 32, 29, 66, 64.5


def bt(b):
    return b * PERIOD


T_END = bt(END_BEAT)
CLIPS = [(0.0, 3.26), (3.27, 9.72), (9.74, 27.86), (27.87, 33.36), (33.37, 37.99), (38.0, 87.3)]

# kills: (source_time, beat). in/out: beats where the shot starts/ends.
SHOTS = [
    dict(k=[(0.94, 1)], b=(0, 3)),
    dict(k=[(6.20, 4)], b=(3, 6), tr="whip"),
    dict(k=[(12.80, 7)], b=(6, 9), tr="flash"),
    dict(k=[(22.70, 10), (23.97, 12)], b=(9, 13), tr="whip"),
    dict(k=[(32.80, 14)], b=(13, 16), tr="flash"),
    dict(k=[(34.67, 17)], b=(16, 19), tr="whip"),
    dict(k=[(39.30, 20)], b=(19, 22), tr="flash"),
    dict(k=[(43.30, 23), (44.34, 25), (45.20, 26)], b=(22, 28), tr="whip"),
    dict(k=[(72.17, 29), (74.70, 32)], b=(28, 35), tr="flash", freeze=(32, 33.5)),
    dict(k=[(48.37, 36), (49.00, 37), (50.04, 39)], b=(35, 40), tr="whip"),
    dict(k=[(53.07, 41), (54.04, 43), (55.54, 45)], b=(40, 46), tr="flash"),
    dict(k=[(59.50, 47)], b=(46, 49), tr="whip"),
    dict(k=[(63.14, 50), (64.54, 52)], b=(49, 53), tr="flash"),
    dict(k=[(69.00, 54)], b=(53, 56), tr="whip"),
    dict(k=[(82.04, 57)], b=(56, 59), tr="flash"),
    dict(k=[(86.60, 61)], b=(59, 66), tr="whip", end=True),
]
PRE_SPEED, POST_SPEED = 1.7, 1.4     # average speed into / out of the kills
KILL_SPEED = 0.35                    # brake speed on the kill frame
TR_OUT, TR_IN = 0.10, 0.16


def clip_of(t):
    return [c for c in CLIPS if c[0] - 0.01 <= t <= c[1] + 0.01][0]


def anchors(shot):
    """[(src, beat, kind)] incl. computed in/out points and the drop freeze."""
    ks = shot["k"]
    c0, c1 = clip_of(ks[0][0])
    b_in, b_out = shot["b"]
    s_in = max(c0 + 0.05, ks[0][0] - PRE_SPEED * bt(ks[0][1] - b_in))
    a = [(s_in, b_in, "cut")]
    for s, b in ks:
        if shot.get("freeze") and b == shot["freeze"][0]:
            a += [(s, b, "drop"), (s, shot["freeze"][1], "freeze")]
        else:
            a.append((s, b, "kill"))
    last_s, last_b = a[-1][0], a[-1][1]
    post = 0.3 if shot.get("end") else POST_SPEED
    s_out = min(c1 - 0.05, last_s + post * bt(b_out - last_b))
    a.append((s_out, b_out, "end" if shot.get("end") else "cut"))
    return a


def segments(shot):
    segs = []
    a = anchors(shot)
    for (s0, b0, k0), (s1, b1, k1) in zip(a, a[1:]):
        e0, e1 = bt(b0), bt(b1)
        v = (s1 - s0) / (e1 - e0)
        if v < 1e-6:                   # freeze frame
            segs.append((e0, e1, s0, s1, 0.0, 0.0))
            continue

        def slope(kind):
            return {"cut": 1.5, "end": 0.2, "kill": KILL_SPEED / v,
                    "drop": 0.08 / v, "freeze": 0.12 / v}[kind]
        m0, m1 = slope(k0), slope(k1)
        n = math.hypot(m0, m1)
        if n > 2.9:
            m0, m1 = m0 * 2.9 / n, m1 * 2.9 / n
        segs.append((e0, e1, s0, s1, m0, m1))
    return segs


def src_time(segs, T):
    T = min(max(T, segs[0][0]), segs[-1][1])
    for e0, e1, s0, s1, m0, m1 in segs:
        if T <= e1:
            u = (T - e0) / (e1 - e0)
            f = (u**3 - 2 * u**2 + u) * m0 + (-2 * u**3 + 3 * u**2) + (u**3 - u**2) * m1
            return s0 + (s1 - s0) * f
    return segs[-1][3]


KILLS = [(bt(b), "drop" if sh.get("freeze") and b == sh["freeze"][0] else "kill")
         for sh in SHOTS for _, b in sh["k"]]
CUTS = [(bt(sh["b"][0]), sh["tr"]) for sh in SHOTS[1:]]
FREEZE = [(bt(sh["freeze"][0]), bt(sh["freeze"][1])) for sh in SHOTS if sh.get("freeze")][0]


def smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def noise(t, seed, freq):
    r = np.random.RandomState(seed)
    fr = freq * (0.7 + 0.6 * r.rand(3))
    ph = r.rand(3) * 2 * math.pi
    return float(np.sum(np.sin(2 * math.pi * fr * t + ph)) / 1.8)


def fx(T):
    p = dict(z=1.0, dx=0.0, dy=0.0, rot=0.0, yaw=0.0, pitch=0.0, flash=0.0,
             bloom=0.35, ca=1.0, expo=1.0, blue=1.0)

    if bt(BASS_CUT_BEAT) <= T < bt(DROP_BEAT):          # build-up: darker, push in
        u = (T - bt(BASS_CUT_BEAT)) / (bt(DROP_BEAT) - bt(BASS_CUT_BEAT))
        p["expo"] *= 1 - 0.25 * u
        p["z"] += 0.06 * u * u

    f0, f1 = FREEZE                                    # 3D camera move on the freeze
    if f0 <= T < f1 + 0.4:
        u = smooth((T - f0) / (f1 - f0))
        out = smooth((T - f1) / 0.4) if T > f1 else 0.0
        p["z"] += (0.16 * u) * (1 - out)
        p["yaw"] += -9 * u * (1 - out)
        p["pitch"] += 4 * u * (1 - out)
        p["rot"] += -2.5 * u * (1 - out)
        p["bloom"] += 0.35 * (1 - out)

    for tk, kind in KILLS:
        d = T - tk
        if d < 0 or d > 1.2:
            continue
        if kind == "drop":
            p["flash"] += 1.0 * math.exp(-d / 0.14)
            p["ca"] += 10 * math.exp(-d / 0.25)
            p["bloom"] += 0.6 * math.exp(-d / 0.4)
            e = 22 * math.exp(-d / 0.18)
            p["dx"] += e * noise(T, 5, 14)
            p["dy"] += e * noise(T, 6, 14)
        else:
            p["flash"] += 0.14 * math.exp(-d / 0.05)
            p["z"] += 0.04 * math.exp(-d / 0.12)
            p["bloom"] += 0.25 * math.exp(-d / 0.15)
            e = 7 * math.exp(-d / 0.1)
            p["dx"] += e * noise(T, int(tk * 7), 18)
            p["dy"] += e * noise(T, int(tk * 7) + 1, 18)

    for tc, kind in CUTS:
        d = T - tc
        if -TR_OUT <= d < 0:
            u = smooth((d + TR_OUT) / TR_OUT) ** 2
            if kind == "flash":
                p["z"] *= 1 + 0.35 * u
                p["flash"] += 0.5 * u
            else:
                p["dx"] -= 0.9 * BW * u
        elif 0 <= d < TR_IN:
            u = 1 - smooth(d / TR_IN)
            if kind == "flash":
                p["z"] *= 1 + 0.25 * u
                p["flash"] += 0.85 * math.exp(-d / 0.05)
            else:
                p["dx"] += 0.9 * BW * u
            p["ca"] += 4 * u

    p["flash"] += 1.0 * math.exp(-T / 0.1)
    if T > bt(FADE_BEAT):
        p["expo"] *= max(0.0, 1 - (T - bt(FADE_BEAT)) / (T_END - bt(FADE_BEAT)))
    return p


def homography(p):
    z = BASE_SCALE * p["z"]
    r = math.radians(p["rot"])
    c, s = math.cos(r) * z, math.sin(r) * z
    A = np.array([[c, -s, BW / 2 + p["dx"] - c * SRC_CX + s * SRC_CY],
                  [s, c, BH / 2 + p["dy"] - s * SRC_CX - c * SRC_CY],
                  [0, 0, 1]])
    if p["yaw"] == 0 and p["pitch"] == 0:
        return A
    yw, pt = math.radians(p["yaw"]), math.radians(p["pitch"])
    Ry = np.array([[math.cos(yw), 0, math.sin(yw)], [0, 1, 0], [-math.sin(yw), 0, math.cos(yw)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(pt), -math.sin(pt)], [0, math.sin(pt), math.cos(pt)]])
    K = np.array([[FOCAL, 0, BW / 2], [0, FOCAL, BH / 2], [0, 0, 1]])
    return K @ Ry @ Rx @ np.linalg.inv(K) @ A


def motion_px(Ma, Mb):
    pts = np.array([[0, 0, 1], [BW, 0, 1], [0, BH, 1], [BW, BH, 1], [BW / 2, BH / 2, 1]], float).T
    a, b = np.linalg.inv(Ma) @ pts, np.linalg.inv(Mb) @ pts
    return float(np.max(np.hypot(*(a[:2] / a[2] - b[:2] / b[2]))))


class Source:
    def __init__(self, path, t0, t1):
        self.i0 = max(0, int(math.floor(t0 * SRC_FPS)) - 2)
        n = int(math.ceil((t1 - self.i0 / SRC_FPS) * SRC_FPS)) + 3
        h = CROP_Y1 - CROP_Y0
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{self.i0 / SRC_FPS:.4f}", "-i", path, "-frames:v", str(n),
               "-vf", f"crop=1080:{h}:0:{CROP_Y0}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
        self.frames = np.frombuffer(raw, np.uint8).reshape(-1, h, 1080, 3)
        self.h = h
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.cache = {}
        gy, gx = np.mgrid[0:h, 0:1080].astype(np.float32)
        self.gx, self.gy = gx, gy

    def _flows(self, k):
        if k not in self.cache:
            if len(self.cache) > 8:
                self.cache.pop(next(iter(self.cache)))
            sz = (540, self.h // 2)
            a = cv2.cvtColor(cv2.resize(self.frames[k], sz), cv2.COLOR_BGR2GRAY)
            b = cv2.cvtColor(cv2.resize(self.frames[k + 1], sz), cv2.COLOR_BGR2GRAY)
            self.cache[k] = (cv2.resize(self.dis.calc(a, b, None), (1080, self.h)) * 2,
                             cv2.resize(self.dis.calc(b, a, None), (1080, self.h)) * 2)
        return self.cache[k]

    def get(self, t):
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
        w0 = cv2.remap(self.frames[k], self.gx + ft0[..., 0], self.gy + ft0[..., 1], cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REPLICATE)
        w1 = cv2.remap(self.frames[k + 1], self.gx + ft1[..., 0], self.gy + ft1[..., 1], cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REPLICATE)
        return cv2.addWeighted(w0, 1 - a, w1, a, 0)


# ---- cold grade -------------------------------------------------------------
def gradient_lut():
    # BGR colour per luminance: deep navy -> steel blue -> icy white
    stops = [(0.0, (0.10, 0.03, 0.01)), (0.25, (0.38, 0.18, 0.06)), (0.55, (0.86, 0.62, 0.38)),
             (0.8, (1.0, 0.90, 0.78)), (1.0, (1.0, 1.0, 1.0))]
    x = np.linspace(0, 1, 256)
    lut = np.zeros((256, 3), np.float32)
    for ch in range(3):
        lut[:, ch] = np.interp(x, [s[0] for s in stops], [s[1][ch] for s in stops])
    return lut


GMAP = gradient_lut()
_x = np.arange(256) / 255.0
TONE = (np.clip(0.5 + np.tanh((_x - 0.47) * 2.6) / np.tanh(1.3) * 0.5, 0, 1) * 255).astype(np.uint8)
_yy, _xx = np.mgrid[0:BH, 0:BW].astype(np.float32)
VIG = (1 - 0.28 * (((_xx - BW / 2) / (BW / 2)) ** 2 * 0.7 + ((_yy - BH / 2) / (BH / 2)) ** 2 * 0.5))[..., None]


WB = [None, None]


def grade(band, p):
    lab = cv2.cvtColor(cv2.LUT(band, TONE), cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0] / 255.0
    # pull the source's orange cast back and push the white balance cold
    # gray-world white balance (smoothed over time) so every shot starts neutral
    ma = max(0.0, float(lab[..., 1].mean()) - 128)      # only remove warm casts
    mb = max(0.0, float(lab[..., 2].mean()) - 128)
    WB[0] = ma if WB[0] is None else 0.85 * WB[0] + 0.15 * ma
    WB[1] = mb if WB[1] is None else 0.85 * WB[1] + 0.15 * mb
    a = (lab[..., 1] - 128 - 0.9 * WB[0]) * 0.9 - 1.5
    b = (lab[..., 2] - 128 - 0.9 * WB[1]) * 0.75 - 11 * p["blue"]
    b -= 8 * (1 - L) * p["blue"]                       # teal/blue shadows
    lab[..., 1], lab[..., 2] = a + 128, b + 128
    img = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32) / 255.0
    Lm = img.mean(2, keepdims=True)
    img = Lm + 1.15 * (img - Lm)
    img = np.maximum(img - 0.045, 0) * 1.06            # deeper blacks
    # crisp it up a little (source is soft)
    img = img + 0.6 * (img - cv2.GaussianBlur(img, (0, 0), 1.6))
    # white-blue bloom
    hi = np.maximum(img - 0.6, 0)
    sm = cv2.resize(hi, (BW // 4, BH // 4), interpolation=cv2.INTER_AREA)
    glow = cv2.resize(cv2.GaussianBlur(sm, (0, 0), 5), (BW, BH)) + \
        cv2.resize(cv2.GaussianBlur(cv2.resize(sm, (BW // 16, BH // 16), interpolation=cv2.INTER_AREA), (0, 0), 4), (BW, BH)) * 1.6
    img = img + glow * p["bloom"] * np.float32([1.15, 1.05, 0.95])
    if p["ca"] > 0.5:
        k = p["ca"] / 700.0
        for ch, sg in ((2, 1), (0, -1)):
            sc = 1 + sg * k
            M = np.float32([[sc, 0, BW / 2 * (1 - sc)], [0, sc, BH / 2 * (1 - sc)]])
            img[..., ch] = cv2.warpAffine(img[..., ch], M, (BW, BH), borderMode=cv2.BORDER_REFLECT)
    img = img * VIG * 1.04
    if p["flash"] > 0:
        f = min(p["flash"], 1.0)
        img = img * (1 - f) + f * np.float32([1.0, 0.98, 0.95])
    return np.clip(img * 255, 0, 255).astype(np.uint8)


def compose(band, expo):
    small = cv2.resize(band, (270, 152), interpolation=cv2.INTER_AREA)
    fill_h = 480
    fill_w = int(270 * fill_h / 152)
    big = cv2.resize(small, (fill_w, fill_h))
    x0 = (fill_w - 270) // 2
    bg = cv2.GaussianBlur(big[:, x0:x0 + 270], (0, 0), 9)
    bg = cv2.resize(bg, (W, H), interpolation=cv2.INTER_LINEAR)
    bg = (bg.astype(np.float32) * 0.82).astype(np.uint8)
    bg[BAND_Y:BAND_Y + BH] = band
    if expo < 0.999:
        bg = (bg.astype(np.float32) * expo).astype(np.uint8)
    return bg


def render_shot(args):
    idx, clip, out_dir = args
    shot = SHOTS[idx]
    segs = segments(shot)
    e_in, e_out = bt(shot["b"][0]), bt(shot["b"][1])
    n0, n1 = math.ceil(e_in * FPS - 1e-6), math.ceil(e_out * FPS - 1e-6)
    ts = [src_time(segs, n / FPS) for n in range(n0, n1)]
    lo, hi = min(ts) - 0.05, max(ts) + 0.05
    src = Source(clip, lo, hi)
    path = os.path.join(out_dir, f"shot_{idx:02d}.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
                            "-pix_fmt", "yuv420p", "-threads", "2", path], stdin=subprocess.PIPE)
    half = 0.25 / FPS                   # 180-degree shutter
    for n in range(n0, n1):
        T = n / FPS
        geo = motion_px(homography(fx(T - half)), homography(fx(T + half)))
        span = abs(src_time(segs, T + half) - src_time(segs, T - half)) * SRC_FPS
        ns = int(min(10, max(1, math.ceil(geo / 6.0), min(6, math.ceil(span / 0.45)))))
        acc = np.zeros((BH, BW, 3), np.float32)
        for j in range(ns):
            tj = T if ns == 1 else T - half + 2 * half * (j + 0.5) / ns
            acc += cv2.warpPerspective(src.get(src_time(segs, tj)), homography(fx(tj)), (BW, BH),
                                       flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        p = fx(T)
        band = grade((acc / ns).astype(np.uint8), p)
        enc.stdin.write(compose(band, p["expo"]).tobytes())
    enc.stdin.close()
    enc.wait()
    return path, n1 - n0


def report():
    for i, sh in enumerate(SHOTS):
        segs = segments(sh)
        ts = np.linspace(segs[0][0], segs[-1][1], 400)
        ss = np.array([src_time(segs, t) for t in ts])
        v = np.diff(ss) / np.diff(ts)
        print(f"shot {i:2d} beats {sh['b'][0]:>2}-{sh['b'][1]:>2} src {ss.min():6.2f}-{ss.max():6.2f} "
              f"speed {v.min():.2f}x..{v.max():.2f}x  mean {(ss.max() - ss.min()) / (ts[-1] - ts[0]):.2f}x")


if __name__ == "__main__":
    clip, music, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    report()
    if "--plan" in sys.argv:
        sys.exit()
    only = [int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")] if "--only" in sys.argv \
        else range(len(SHOTS))
    with Pool(int(os.environ.get("JOBS", "4"))) as pool:
        for path, n in pool.imap_unordered(render_shot, [(i, clip, out_dir) for i in only]):
            print("done", path, n, flush=True)
    json.dump(dict(M0=M0, T_END=T_END, drop=bt(DROP_BEAT), bass_cut=bt(BASS_CUT_BEAT),
                   cuts=[c[0] for c in CUTS], fade=bt(FADE_BEAT)), open(os.path.join(out_dir, "timeline.json"), "w"))
