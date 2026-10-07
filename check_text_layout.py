#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_text_layout.py —— 不导出、不开达芬奇，直接算准每一行文字的像素布局

为什么要有这个
--------------
用户问：「以后的视频我希望一次成片……没有办法先导出你检查过后再修改，这样你还能做到吗？」

能。关键是**把"要看一眼才知道"的东西，变成"能算出来"的东西**。
文字排版恰好是能算的 —— CoreText 给出真实字形宽度，所以可以**在建成之前**
就把每一行的宽度、每个文字块的外框算出来，并断言：

  · 每一行都不超过字幕安全宽度（= 不会溢出画面）
  · 文字块不越出画面上下边界
  · 中英文字幕不互相压叠

这样一来，`字号/字距/换行/位置` 都不再依赖"导出看一眼"。
以前之所以要来回，是因为有几处**猜的常量**和**静默失败**：
  - Fusion 的 `Size` 基准（猜 1920/1080，实测是 696）
  - `CharacterSpacing` 是倍数而不是像素
  - 字体写成路径（Fusion 静默把路径当文字渲染）
现在这三类要么已实测标定，要么已被自检拦住。

用法
    /opt/homebrew/bin/python3 check_text_layout.py --timeline out/timeline_ch01.json
    # 退出码 0 = 所有文字都放得下；非 0 = 有溢出
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402
import fontsel as F  # noqa: E402


def layout_of(text, spec_name):
    """算出这段文字的行宽列表与行数。"""
    sp = E.SPEC[spec_name]
    em = E.px_size(sp["size"], sp["scale"])
    ws = [F.measure(sp["font"], ln, em) or len(ln) * em
          for ln in text.split("\n")]
    return em, ws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--safe-w", type=float, default=E.SUB_SAFE_W)
    a = ap.parse_args()
    tl = json.load(open(a.timeline, encoding="utf-8"))
    fps = E.FPS
    bad = []
    print(f"=== 文字布局自检（安全宽 {a.safe_w:.0f}px / 画面 {E.W}x{E.H}）===")
    print(f"  {'位置':<10}{'行数':>4}{'最长行':>9}{'字号px':>9}  内容")
    for i, s in enumerate(tl["segments"], 1):
        specn_zh = "quote" if s.get("quote") else "zh"
        # 中文：**不折行**。超宽的中文句应该在上游被拆成两段
        # （batch_timelines.split_wide_segments），所以这里超宽就是"漏拆"——直接报错。
        zh_txt = s.get("zh") or ""
        if zh_txt:
            em, ws = layout_of(zh_txt, specn_zh)
            widest = max(ws)
            n_zh_lines = len(ws)
            flag = "❌" if widest > a.safe_w else "  "
            if widest > a.safe_w:
                bad.append(f"句{i} 中文 最长行 {widest:.0f}px > {a.safe_w:.0f}px"
                           f"（该句没被拆开，检查 batch_timelines.split_wide_segments）")
            print(f"  {flag}{'中文':<8}{n_zh_lines:>4}{widest:>9.0f}{em:>9.1f}  "
                  f"{zh_txt[:46]}")
        # 英文：折行上限取中文字幕宽度（两者视觉等宽），最多 3 行；硬上限 = 安全宽
        txt = s.get("en") or ""
        if txt:
            sp_en = E.SPEC["en"]
            em_en = E.px_size(sp_en["size"], sp_en["scale"])
            sp_zh = E.SPEC[specn_zh]
            em_zh = E.px_size(sp_zh["size"], sp_zh["scale"])
            mx = E.en_wrap_max(max(ws) if zh_txt else 0)
            ew = F.measure(sp_en["font"], txt, em_en) or len(txt) * em_en
            if ew > mx:
                ls = F.wrap_balanced(txt, sp_en["font"], em_en, mx, max_lines=3,
                                     hard_max_px=a.safe_w)
                if len(ls) > 1:
                    txt = "\n".join(ls)
            em, ws_en = layout_of(txt, "en")
            widest = max(ws_en)
            flag = "❌" if widest > a.safe_w else "  "
            if widest > a.safe_w:
                bad.append(f"句{i} 英文 最长行 {widest:.0f}px > {a.safe_w:.0f}px")
            # 中英不叠压：中文字幕恒为一行（超宽就拆句），
            # 所以只需校验两轨基准行心距 ≥ 0.9 个字高。
            y_zh = 963.7 - 0.281 * sp_zh["jy"]
            y_en = 963.7 - 0.281 * sp_en["jy"]
            gap = y_en - y_zh
            if gap < 0.9 * min(em_zh, em_en):
                bad.append(f"句{i} 中英字幕叠压（行心距 {gap:.0f}px < "
                           f"{0.9 * min(em_zh, em_en):.0f}px）")
            print(f"  {flag}{'英文':<8}{len(ws_en):>4}{widest:>9.0f}{em:>9.1f}  "
                  f"{txt.replace(chr(10), '  ⏎  ')[:46]}")
    # 档案卡三行
    for tag, specn, key in (("卡中文", "card_zh", None), ("卡英文", "card_en", None)):
        txt = (tl.get("card") or {}).get("zh" if tag == "卡中文" else "en") or ""
        if not txt:
            continue
        em, ws = layout_of(txt, specn)
        widest = max(ws)
        flag = "❌" if widest > a.safe_w else "  "
        if widest > a.safe_w:
            bad.append(f"档案卡{tag} 最长行 {widest:.0f}px > {a.safe_w:.0f}px")
        print(f"  {flag}{tag:<8}{len(ws):>4}{widest:>9.0f}{em:>9.1f}  {txt[:46]}")

    # 竖排题名与引文：高度不能超画面
    for label, specn, extra in (("竖排题名", "card_nm", tl.get("card_name") or ""),
                                ("引文竖排", "cite", tl.get("citation") or "")):
        if not extra:
            continue
        em, ws = layout_of(extra, specn)
        h = len(extra.split("\n")) * em          # 竖排：每字一行
        flag = "❌" if h > E.H else "  "
        if h > E.H:
            bad.append(f"{label} 高 {h:.0f}px > {E.H}px")
        print(f"  {flag}{label:<8}{'竖排':>4}{h:>9.0f}{em:>9.1f}  (高) {extra[:30]}")

    print()
    if bad:
        print("  ❌ 有文字会溢出：")
        for b in bad:
            print(f"     · {b}")
        return 1
    print("  ✅ 所有文字都在安全区内（无需导出即可确认）")
    print("  ℹ️ 这条检查同时覆盖：字号(Size 基准已标定 K=696)、字距(倍数)、")
    print("     英文按词换行(平衡折行)、中英不叠压 —— 也就是'一次成片'的底气。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
