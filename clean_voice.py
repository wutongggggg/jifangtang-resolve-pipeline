#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_voice.py —— 去掉每段配音开头那声类似 "off" 的杂音

问题（用户 2026-10-06 反馈）
---------------------------
每小段配音开头都有一个像 "off" 的声音。分析 `voice_sent/*.wav` 发现：

  · 开头 ~105ms 是静音；
  · 紧接着（~105ms 起）有一小段**低电平、高频、噪声型**爆发：
        质心 900~4900 Hz，频谱平坦度 0.005~0.26，ZCR 0.08~0.34，RMS 仅 0.02~0.05
  · 之后才是真正的浊音语音：
        质心 336~873 Hz，平坦度 0.001~0.037，ZCR 0.04~0.16，RMS 0.15~0.25

7 个文件的第一响段**都从 105~110ms 开始**，这种固定偏移不是自然语音，
是合成器的固定伪音。做法：把主语音之前的这一段连同前导静音一起剪掉，
只留 40ms 预卷，并加 10ms 淡入避免切口声。

目录约定
--------
  读 `voice_sent_raw/`（原始）-> 写 `voice_sent/`（清理后）
  若 `voice_sent_raw/` 不存在，会先把 `voice_sent/` 改名过去，保证可重复运行。

用法
    /opt/homebrew/bin/python3 clean_voice.py
    /opt/homebrew/bin/python3 clean_voice.py --preroll 0.04 --thr 0.02
"""
import argparse
import glob
import os
import shutil
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

RAW = os.path.join(E.AUTO, "voice_sent_raw")
CLEAN = os.path.join(E.AUTO, "voice_sent")


SPEECH_THR = 0.12      # 主语音 RMS 下限（实测：伪音峰值 ≤0.08，浊音语音 ≥0.15）
HOLD_WINDOWS = 3       # 连续 3×5ms 超过阈值才算语音起点，防单个噪声尖峰误触


def find_speech_onset(s, fr, thr=SPEECH_THR, hold=HOLD_WINDOWS, win_ms=5.0):
    """返回 (主语音起点秒, 5ms RMS 包络, 前导低电平段 span 或 None)。

    为什么不再用"取第二个响段"（2026-10-06 的老做法）
    --------------------------------------------------
    老规则假设"第一个响段＝伪音、第二个响段＝语音"。做第 02 章时实测该假设会**剪掉真话**：
    这里伪音与语音之间没有 ≥30ms 的静音，两段被 merge 成一段，
    于是"第二个响段"其实是**下一个短语**——47 字符的句子只剩 0.85s（≈55 字符/秒，不可能）。

    新规则按**幅度**判定，不依赖段间静音：
      · 伪音是低电平噪声（实测峰值 RMS ≤0.08，ch01_seg1 为 0.10~0.38s）；
      · 主语音是浊音（RMS ≥0.12）。
    取第一个"连续 hold 个窗都 ≥thr"的位置作为语音起点，把它之前的低电平段连同静音剪掉。
    实测对照：
      `voice/ch01_seg1.wav`（确有伪音）→ 语音 0.58s 起，剪 0.54s ✅
      `voice_sent_raw/*`（ch02，无伪音，0.05~0.17s 即语音）→ 几乎不剪 ✅
      ch01 已清理过的文件（语音 0.04~0.08s 起）→ 幂等，不会再剪 ✅
    """
    step = max(1, int(win_ms / 1000.0 * fr))
    rms = np.array([np.sqrt((s[i:i + step] ** 2).mean())
                    for i in range(0, max(1, len(s) - step), step)])
    onset = 0.0
    for i in range(max(1, len(rms) - hold + 1)):
        if np.all(rms[i:i + hold] >= thr):
            onset = i * step / fr
            break
    else:
        # 整段没有达到语音电平：整段都是低电平/静音，不剪（安全兜底）
        return 0.0, rms, None
    lead = rms[:int(round(onset * fr / step))]
    span = None
    if len(lead):
        nz = np.nonzero(lead > 0.005)[0]
        if len(nz):
            # 报"前导低电平段"的**起点**（= 伪音开始处），不是最响的那一窗，
            # 否则日志里会出现"前导低电平 605~610ms"这种看不出伪音在哪的行。
            span = (float(nz[0] * step / fr), float(onset))
    return onset, rms, span


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preroll", type=float, default=0.04, help="语音前保留的预卷秒数")
    ap.add_argument("--thr", type=float, default=SPEECH_THR,
                    help="主语音 RMS 阈值（默认 0.12；伪音峰值 ≤0.08，浊音语音 ≥0.15）")
    ap.add_argument("--fade", type=float, default=0.010, help="淡入秒数")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(RAW) or a.force:
        if os.path.isdir(RAW):
            shutil.rmtree(RAW)
        shutil.move(CLEAN, RAW)
        print(f"  已把 {os.path.basename(CLEAN)} 备份为 {os.path.basename(RAW)}")
    os.makedirs(CLEAN, exist_ok=True)

    files = sorted(glob.glob(os.path.join(RAW, "*.wav")))
    if not files:
        sys.exit(f"{RAW} 里没有 wav")
    print(f"=== 清理配音开头伪音（{len(files)} 个）===")
    print(f"  预卷 {a.preroll*1000:.0f}ms  语音阈值 {a.thr}  淡入 {a.fade*1000:.0f}ms")
    n_trim = 0
    for p in files:
        w = wave.open(p, 'rb')
        fr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        s = np.frombuffer(w.readframes(n), dtype='<i2').astype(np.float64) / 32768.0
        w.close()
        onset, rms, span = find_speech_onset(s, fr, a.thr)
        cut = max(0.0, onset - a.preroll)
        cut_n = int(round(cut * fr))
        out = s[cut_n:].copy()
        if a.fade > 0:
            fn = min(int(a.fade * fr), len(out) // 4)
            if fn > 0:
                out[:fn] *= np.linspace(0.0, 1.0, fn)
        dst = os.path.join(CLEAN, os.path.basename(p))
        with wave.open(dst, 'wb') as o:
            o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(fr)
            o.writeframes((np.clip(out, -1, 1) * 32767).astype('<i2').tobytes())
        trimmed = (len(s) - len(out)) / fr
        flag = "✅" if trimmed > 0.02 else "－"
        if span:
            note = f"前导低电平 {span[0]*1000:.0f}~{span[1]*1000:.0f}ms"
        else:
            note = "无前导伪音"
        print(f"  {flag} {os.path.basename(p):18} {len(s)/fr:5.2f}s -> {len(out)/fr:5.2f}s"
              f"  (剪掉开头 {trimmed*1000:5.0f}ms, {note})")
        if trimmed > 0.02:
            n_trim += 1
    print(f"\n  清理 {n_trim}/{len(files)} 个")
    print(f"  产物: {CLEAN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
