#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""synth_sentences.py —— 用 VoiceStudio 合成「一章、逐句」的配音

为什么要逐句合成（而不是整章一段）
----------------------------------
逐句合成天然给出"字幕换行 + 镜头切换"的锚点，不需要强制对齐。
本脚本把 chapters.json 里该章的每个 pair **按句子拆开**，一句一个 wav，
落到流水线固定的输入位置 `voice_sent/sentence_NN.wav`。

拆分规则复用流水线自己的 `batch_timelines.segments_for()`，
保证"合成的句数"和"时间轴的句数"**永远一致**（对不上会立刻报错）。

用法
    # 1) 先启动 VoiceStudio（见 SKILL.md；首次加载模型要 80~210s）
    open -a /Applications/VoiceStudio.app

    # 2) 合成某一章
    /opt/homebrew/bin/python3 synth_sentences.py --chapter 02
    /opt/homebrew/bin/python3 synth_sentences.py --chapter 02 --voice demo0001

    # 3) 合成完会自动跑 clean_voice.py 去掉开头的 "off" 伪音

注意
    · 串行合成（单 GPU），请求之间留间隔；sidecar 首次加载模型会超 60s，脚本自动重试。
    · 只做一章 —— 和"一次只做一根参"的原则一致。
    · 会先把现有 voice_sent/ 备份到 voice/ch<NN>_prev/，不会丢上一章的东西。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E          # noqa: E402
import synth as S            # noqa: E402
from batch_timelines import segments_for  # noqa: E402

VOICE_SENT = os.path.join(E.AUTO, "voice_sent")
VOICE_ARCH = os.path.join(E.AUTO, "voice")


def numpy_python():
    """能 `import numpy` 的解释器（clean_voice 依赖它）。找不到返回 None。"""
    cands = [
        sys.executable,
        "/opt/homebrew/bin/python3",
        # VoiceStudio 自带 venv（本机实测有 numpy）
        os.path.expanduser("~/Library/Application Support/"
                           "com.debpalash.omnivoice-studio/project/.venv/bin/python"),
    ]
    for py in cands:
        if not py or not os.path.exists(py):
            continue
        try:
            r = subprocess.run([py, "-c", "import numpy"], capture_output=True)
        except Exception:
            continue
        if r.returncode == 0:
            return py
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", required=True)
    ap.add_argument("--voice", default=S.VOICE_ID,
                    help=f"VoiceStudio 音色 ID（默认 {S.VOICE_ID} = 寄方堂2；"
                         f"别用 demo0001，那是内置演示女声）")
    ap.add_argument("--gap", type=float, default=1.5, help="请求间隔秒")
    ap.add_argument("--no-clean", action="store_true", help="跳过 clean_voice")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ch_no = f"{int(a.chapter):02d}"

    chs = json.load(open(os.path.join(E.AUTO, "chapters.json"),
                         encoding="utf-8"))["chapters"]
    ch = next((c for c in chs if str(c.get("no")) == ch_no), None)
    if ch is None:
        sys.exit(f"chapters.json 里没有第 {ch_no} 章")

    segs = segments_for(ch)
    if not segs:
        sys.exit(f"第 {ch_no} 章没有可合成的句子")
    print(f"=== 第 {ch_no} 章 {ch.get('title')} ({ch.get('code')}) ===")
    print(f"  逐句拆分: {len(segs)} 句")
    for s in segs:
        print(f"    {s['i']:>2}. {s['en'][:64]}")
    if a.dry_run:
        print("\n  --dry-run：不实际合成")
        return 0

    # 备份现有 voice_sent
    if os.path.isdir(VOICE_SENT) and os.listdir(VOICE_SENT):
        bak = os.path.join(VOICE_ARCH, f"ch{ch_no}_prev")
        if os.path.isdir(bak):
            shutil.rmtree(bak)
        os.makedirs(VOICE_ARCH, exist_ok=True)
        shutil.move(VOICE_SENT, bak)
        print(f"  已备份原 voice_sent -> {os.path.relpath(bak, E.AUTO)}")
    # 连原始备份一起挪走，好让 clean_voice 重新建立
    raw = os.path.join(E.AUTO, "voice_sent_raw")
    if os.path.isdir(raw):
        bakr = os.path.join(VOICE_ARCH, f"ch{ch_no}_raw_prev")
        if os.path.isdir(bakr):
            shutil.rmtree(bakr)
        shutil.move(raw, bakr)
    os.makedirs(VOICE_SENT, exist_ok=True)

    print(f"\n  开始合成（音色 {a.voice}，串行，句间 {a.gap}s）")
    total = 0.0
    t0 = time.time()
    for s in segs:
        out = os.path.join(VOICE_SENT, f"sentence_{int(s['i']):02d}.wav")
        try:
            S.synth(s["en"], a.voice, out)
        except Exception as e:
            sys.exit(f"  句{s['i']} 合成失败: {e}\n"
                     f"  提示：VoiceStudio 起了吗？`open -a /Applications/VoiceStudio.app`"
                     f"，首次加载模型要 80~210s")
        S.trim_silence(out)
        d = S.probe_duration(out)
        total += d
        print(f"    {s['i']:>2}/{len(segs)}  {d:6.2f}s  {s['en'][:52]}")
        sys.stdout.flush()
        if s is not segs[-1]:
            time.sleep(a.gap)
    print(f"\n  合成 {len(segs)} 句，{total:.2f}s，耗时 {time.time()-t0:.0f}s")
    print(f"  产物: {os.path.relpath(VOICE_SENT, E.AUTO)}/sentence_*.wav")

    if not a.no_clean:
        print("\n-- 去掉每句开头的 'off' 伪音 --")
        # clean_voice 需要 numpy，而 `/opt/homebrew/bin/python3` 上没有装
        # （踩过：这一步静默失败，配音直接带着伪音进片）。挑第一个能 import numpy 的解释器。
        py = numpy_python()
        if py is None:
            print("  ⚠️ 找不到带 numpy 的解释器，clean_voice 跳过 —— 配音可能带伪音")
        else:
            r = subprocess.run([py, os.path.join(E.AUTO, "clean_voice.py")], cwd=E.AUTO)
            if r.returncode != 0:
                print(f"  ⚠️ clean_voice 失败（{py}），配音可能带伪音")

    print("\n  下一步：")
    print(f"    ./一次成片.sh {ch_no}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
