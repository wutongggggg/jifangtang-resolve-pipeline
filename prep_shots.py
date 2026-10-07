#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prep_shots.py —— 把原素材转成 1080x1920 中间片（FCPXML 路径的前提）

为什么必须处理原素材（实测，别改回去）
--------------------------------------
原始 Sony 文件里除了视频和 PCM 音轨，还有一条 **`rtmd` 实时元数据数据轨**。
实测达芬奇的 `ImportTimelineFromFile`（FCPXML）**就是被这条数据轨卡住的**：

| 素材 | FCPXML 导入 |
|---|---|
| 原始 Sony 4K 10bit 4:2:2（含 rtmd 数据轨） | ❌ 失败 |
| 同一文件**只去掉数据轨**、视频 `-c copy` 原样复制 | ✅ **成功** |
| 只去掉音轨、保留数据轨 | ❌ 失败 |
| 重编码成 4K 8bit 4:2:0 | ✅ 成功 |

→ **不是 10bit 4:2:2 的问题，也不是 TCC/路径问题**（`ImportMedia` 读原素材一直正常）。
   用户手动导入能成功，是因为 UI 导入走的是另一条路径。

**默认模式 `remux`：无损重封装** —— 视频/音频 `-c copy` 一字不改，
只丢掉 rtmd 数据轨。画质零损失、仍是真 4K 10bit 4:2:2，符合"V1 用 4K 原素材"的规格。
文件大小几乎不变，耗时几秒。

`--mode transcode` 是后备：万一某台机器 remux 仍失败，
再转成 1080x1920 8bit 4:2:0（会有一次缩放损失）。

两种模式都**保留整条素材**（不预裁），非破坏性裁剪仍由达芬奇在时间线上做；
输出保持**同名文件**放到单独目录，时间轴 JSON 与 FCPXML 生成器都不用改。

用法
    /opt/homebrew/bin/python3 prep_shots.py --chapter 01          # 默认 remux（无损）
    /opt/homebrew/bin/python3 prep_shots.py --chapter 01 --mode transcode
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

DEFAULT_OUT = os.path.expanduser("~/Movies/素材/_remux")
DEFAULT_OUT_X = os.path.expanduser("~/Movies/素材/_1080")


def probe(path):
    r = subprocess.run([E.FFMPEG, "-hide_banner", "-i", path],
                       capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    return r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", required=True)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--mode", choices=["remux", "transcode"], default="remux",
                    help="remux=无损重封装去数据轨（默认）；transcode=转 1080x1920 8bit")
    ap.add_argument("--crf", default="16")
    ap.add_argument("--force", action="store_true", help="已存在也重转")
    a = ap.parse_args()
    if a.mode == "transcode" and a.out == DEFAULT_OUT:
        a.out = DEFAULT_OUT_X

    with open(os.path.join(E.AUTO, "media_map.json"), encoding="utf-8") as f:
        mmap = json.load(f)
    src = mmap.get(str(a.chapter))
    if not src:
        sys.exit(f"media_map.json 里没有第 {a.chapter} 章")
    if not os.path.isdir(src):
        sys.exit(f"原素材目录不可读: {src}")
    code = os.path.basename(src.rstrip("/"))
    dst = os.path.join(os.path.expanduser(a.out), code)
    os.makedirs(dst, exist_ok=True)
    print(f"=== 第 {a.chapter} 章 中间片 ===")
    print(f"  源: {src}")
    print(f"  出: {dst}")

    files = sorted(f for f in os.listdir(src)
                   if f.upper().endswith((".MP4", ".MOV")) and not f.startswith("._"))
    if not files:
        sys.exit("源目录里没有 MP4/MOV")
    n_new = n_skip = 0
    for i, fn in enumerate(files, 1):
        s, d = os.path.join(src, fn), os.path.join(dst, fn)
        if os.path.exists(d) and not a.force and os.path.getsize(d) > 10000:
            print(f"  [{i}/{len(files)}] {fn} 已存在，跳过")
            n_skip += 1
            continue
        if a.mode == "remux":
            # 无损：视频/音频原样复制，只丢掉 rtmd 数据轨
            cmd = [E.FFMPEG, "-y", "-v", "error", "-i", s,
                   "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                   "-movflags", "+faststart", d]
        else:
            cmd = [E.FFMPEG, "-y", "-v", "error", "-i", s,
                   "-vf", ("scale=1080:1920:force_original_aspect_ratio=increase,"
                           "crop=1080:1920,fps=30,setsar=1"),
                   "-an", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", a.crf, "-pix_fmt", "yuv420p", d]
        r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        if r.returncode or not os.path.exists(d):
            print(f"  ❌ {fn}: {r.stderr[-200:]}")
            continue
        sz = os.path.getsize(d) / 1048576
        print(f"  [{i}/{len(files)}] ✅ {fn} -> {sz:.1f} MB")
        n_new += 1

    print(f"\n  新转 {n_new} / 跳过 {n_skip}")
    print(f"  构建时用:  --media-dir {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
