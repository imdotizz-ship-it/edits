"""Builds the edit's soundtrack: song section + synthesized impact SFX.

usage: python3 audio.py MUSIC_WAV TIMELINE_JSON OUT_WAV
"""
import json
import sys

import numpy as np
import scipy.signal as ss
import soundfile as sf

music_path, tl_path, out_path = sys.argv[1:4]
tl = json.load(open(tl_path))
y, sr = sf.read(music_path, dtype="float32")
if y.ndim == 1:
    y = np.stack([y, y], 1)

n = int(tl["T_END"] * sr)
start = int(tl["M0"] * sr)
mix = y[start:start + n].copy() * 0.80
t = np.arange(n) / sr
rng = np.random.RandomState(7)


def at(sec):
    return int(sec * sr)


def add(sig, sec, gain):
    i = at(sec)
    j = min(n, i + len(sig))
    if j > max(i, 0):
        mix[max(i, 0):j] += (sig[max(0, -i):j - i] * gain)[:, None]


# sub boom on the drop
d = np.arange(int(1.6 * sr)) / sr
freq = 32 + 30 * np.exp(-d / 0.25)
boom = np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-d / 0.5)
click = ss.lfilter(*ss.butter(2, 900 / (sr / 2)), rng.randn(len(d))) * np.exp(-d / 0.02)
add(boom * 0.9 + click * 0.6, tl["drop"], 0.55)

# riser through the bass cut
L = tl["drop"] - tl["bass_cut"]
r = np.arange(int(L * sr)) / sr
noise = rng.randn(len(r))
riser = np.zeros_like(noise)
for k, (f0, f1) in enumerate([(400, 1200), (1200, 3000), (3000, 7000)]):
    b, a = ss.butter(2, [f0 / (sr / 2), f1 / (sr / 2)], "band")
    riser += ss.lfilter(b, a, noise) * (r / L) ** (1.5 + k)
add(riser * (r / L) ** 2, tl["bass_cut"], 0.30)

# whooshes on the cuts
for c in tl["cuts"]:
    if abs(c - tl["drop"]) < 0.5:
        continue
    w = np.arange(int(0.35 * sr)) / sr
    env = np.where(w < 0.22, (w / 0.22) ** 2, np.exp(-(w - 0.22) / 0.04))
    b, a = ss.butter(2, [700 / (sr / 2), 5000 / (sr / 2)], "band")
    add(ss.lfilter(b, a, rng.randn(len(w))) * env, c - 0.22, 0.12)

# fade out
f0 = tl["fade"]
g = np.clip(1 - (t - f0) / (tl["T_END"] - f0), 0, 1)
mix *= g[:, None]
mix[: int(0.005 * sr)] *= np.linspace(0, 1, int(0.005 * sr))[:, None]
sf.write(out_path, mix, sr, subtype="FLOAT")
print("peak", float(np.abs(mix).max()), "dur", n / sr)
