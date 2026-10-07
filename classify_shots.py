#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""镜头分类：全景(full) vs 特写(close)。

用户的先验: **A 开头 = 全景, B 开头 = 不同部位特写**。
本脚本用视觉特征**交叉验证**这个先验，不盲信命名 —— 命名与视觉不符的会被标出。

特征（对每个镜头取中段一帧，缩到 270x480 灰度分析）:
    coverage  主体像素占画面比例   （特写高、全景低）
    bbox_w    主体包围盒宽度占比   （特写宽、全景窄）
    margin    主体四周最小留白占比（全景留白大）
    sharp     拉普拉斯方差（清晰度，参考）

输出: shots.json  {file: {kind, score, features...}}
"""
import json
import os
import subprocess
import sys

FFMPEG = (__import__("shutil").which("ffmpeg") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
W, H = 270, 480


def probe_dur(path):
    import re
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
    return round(int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3)), 3) if m else 0.0


def features(path):
    """取中段一帧做特征提取。"""
    d = probe_dur(path)
    frame = f"/tmp/shot_{os.path.basename(path)}.png"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(max(0.3, d/2)), "-i", path,
                    "-frames:v", "1", "-vf", f"scale={W}:{H}", frame],
                   capture_output=True, text=True)
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None
    if not os.path.exists(frame):
        return None
    a = np.asarray(Image.open(frame).convert("L")).astype(float)
    thr = max(35.0, a.mean() + a.std()*0.8)
    m = a > thr
    if m.sum() < 50:
        return {"coverage": 0.0, "bbox_w": 0.0, "margin": 1.0, "sharp": 0.0}
    ys, xs = np.where(m)
    bw = (xs.max()-xs.min())/W
    bh = (ys.max()-ys.min())/H
    margin = min(xs.min()/W, (W-1-xs.max())/W, ys.min()/H, (H-1-ys.max())/H)
    # 拉普拉斯方差（清晰度）
    lap = np.abs(4*a[1:-1,1:-1] - a[:-2,1:-1] - a[2:,1:-1] - a[1:-1,:-2] - a[1:-1,2:])
    return {"coverage": round(float(m.mean()), 4),
            "bbox_w": round(float(bw), 3),
            "bbox_h": round(float(bh), 3),
            "margin": round(float(margin), 3),
            "sharp": round(float(lap.var()), 1)}


def classify(name, f):
    """命名先验 + 视觉一致性判定。"""
    prior = "full" if name.upper().startswith("A") else "close" if name.upper().startswith("B") else None
    if not f:
        return prior, "no-features", False
    # 视觉打分: coverage 低 + margin 大 -> 全景
    v_full = (1.0 - min(f["coverage"]/0.25, 1.0))*0.6 + min(f["margin"]/0.15, 1.0)*0.4
    v_is_full = v_full >= 0.5
    agree = (prior == "full") == v_is_full if prior else None
    return ("full" if v_is_full else "close"), ("agree" if agree else "MISMATCH"), bool(agree)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("media_dir")
    ap.add_argument("--out", default="shots.json")
    a = ap.parse_args()
    out = {}
    files = sorted(f for f in os.listdir(a.media_dir)
                   if f.upper().endswith(".MP4") and not f.startswith("._"))
    print(f"{'文件':<14}{'命名':<7}{'视觉':<7}{'一致':<10}{'覆盖':>7}{'包围盒宽':>9}{'留白':>7}")
    for fn in files:
        f = features(os.path.join(a.media_dir, fn))
        kind, status, agree = classify(fn, f) if f else (None, "?", False)
        prior = "全景" if fn.upper().startswith("A") else "特写"
        vis = "全景" if kind == "full" else "特写" if kind == "close" else "?"
        mark = "✅" if agree else ("⚠️ 不符" if f else "")
        print(f"{fn:<14}{prior:<7}{vis:<7}{mark:<10}"
              f"{f['coverage'] if f else 0:>7.3f}{f['bbox_w'] if f else 0:>9.2f}"
              f"{f['margin'] if f else 0:>7.3f}")
        out[fn] = {"kind": kind, "prior": prior, "agree": agree, "features": f}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    nm = sum(1 for v in out.values() if v["agree"] is False)
    print(f"\n-> {a.out}   命名与视觉不符: {nm} 个")


if __name__ == "__main__":
    main()
