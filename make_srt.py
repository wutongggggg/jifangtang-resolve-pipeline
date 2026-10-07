#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 timeline.json 生成 SRT 字幕（中文轨 / 英文轨），交给达芬奇导入。

为什么走 SRT（而不是 Fusion 脚本）:
    达芬奇支持 Timeline -> Import -> Subtitles 直接读 SRT；
    字幕**内容与时间**由脚本保证精确（与配音逐句对齐），
    而**样式**（字体/字号/颜色/位置）在达芬奇字幕设置里设一次即可全片生效。
    这条路的自动化收益最大、调试成本最低。

用法:
    python3 make_srt.py --timeline out/timeline_ch01.json --outdir out/srt
"""
import argparse
import json
import os
import wave


def srt_time(sec):
    """秒 -> SRT 时间戳 00:00:02,000"""
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wav_dur(p):
    try:
        with wave.open(p, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 3)
    except Exception:
        return 0.0


def write_srt(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for i, (t0, t1, text) in enumerate(entries, 1):
            f.write(f"{i}\n{srt_time(t0)} --> {srt_time(t1)}\n{text}\n\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--outdir", default="out/srt")
    ap.add_argument("--card-zh", default="")
    ap.add_argument("--card-en", default="")
    ap.add_argument("--citation", default="")
    ap.add_argument("--citation-start", type=float, default=None)
    a = ap.parse_args()

    tl = json.load(open(a.timeline, encoding="utf-8"))
    segs = tl["segments"]
    CARD = tl.get("card_sec", 2.0)
    os.makedirs(a.outdir, exist_ok=True)
    ch = tl["chapter"]

    zh, en = [], []
    # 档案卡文字（0 ~ CARD 秒）
    if a.card_zh:
        zh.append((0.0, CARD, a.card_zh))
    if a.card_en:
        en.append((0.0, CARD, a.card_en))

    for s in segs:
        t0 = s["t0"]
        t1 = t0 + s["dur"]
        if s.get("zh"):
            zh.append((t0, t1, s["zh"]))
        if s.get("en"):
            en.append((t0, t1, s["en"]))

    # 引文（对齐最后一句镜头）
    if a.citation:
        last = segs[-1]
        st = a.citation_start if a.citation_start is not None else last["t0"]
        zh.append((st, st + last["dur"], a.citation))

    fz = write_srt(os.path.join(a.outdir, f"ch{ch}_中文.srt"), zh)
    fe = write_srt(os.path.join(a.outdir, f"ch{ch}_英文.srt"), en)
    print(f"中文 SRT: {fz}  ({len(zh)} 条)")
    print(f"英文 SRT: {fe}  ({len(en)} 条)")
    print(f"\n导入方法: 达芬奇 → Timeline → Import → Subtitles → 选 SRT 文件")
    # 打印预览
    print("\n=== 中文轨预览 ===")
    for t0, t1, txt in zh[:4]:
        print(f"  {srt_time(t0)} --> {srt_time(t1)}  {txt[:34]}")
    print("=== 英文轨预览 ===")
    for t0, t1, txt in en[:4]:
        print(f"  {srt_time(t0)} --> {srt_time(t1)}  {txt[:44]}")


if __name__ == "__main__":
    main()
