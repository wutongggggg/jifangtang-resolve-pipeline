#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_calibration.py —— 生成"标定草稿"，导出后用它把字幕参数一次定死

上一版为什么是空的
------------------
第一版用 `MediaPool.AppendToTimeline` 逐段摆文字载体 —— 这正是本项目已知的
坑：**AppendToTimeline 的片段不可靠持久化**（用户打开只看到 1 个片段）。
现在改成走 **FCPXML 导入**（`ImportTimelineFromFile`），这是唯一可靠的路径。

要标定什么
----------
1. **字号基准 K**：参考成片 20260625-23 的中文字距约 50px，
   而我的导出只有约 26px（小了近一半）。说明 Fusion Text+ 的 `Size` 不是以
   画面高 1920 为 1.0。CAL1..CAL5 是**同一个字符串的 5 档字号**，
   量出字距后线性拟合就能把 K 定死。
2. **东/西文字序**：CAL6/CAL7 换 Direction。
3. **档案卡**：CAL8。
4. **大小 logo 与结尾**：CAL9/CAL10。

用法
    /opt/homebrew/bin/python3 make_calibration.py
    # 然后在达芬奇里打开 _DSH_CALIB 导出到桌面，把文件名告诉我
"""
import os
import sys
import time

SCRIPT_API = os.environ.get("RESOLVE_SCRIPT_API") or (os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting") if os.name == "nt" else "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"); _RESOLVE_LIB = os.environ.get("RESOLVE_SCRIPT_LIB") or (r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll" if os.name == "nt" else "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
os.environ["RESOLVE_SCRIPT_API"] = SCRIPT_API
os.environ["RESOLVE_SCRIPT_LIB"] = _RESOLVE_LIB
sys.path.insert(0, os.path.join(SCRIPT_API, "Modules"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402
import fcpxml_gen  # noqa: E402

FPS = 30.0
PROJ = "_DSH_CALIB"
SEG_F = int(2.5 * FPS)
CARRIER = os.path.join(E.AUTO, "assets", "blank_alpha.mov")
LOGO_SMALL = os.path.join(E.AUTO, "assets", "logo_small.mov")

ZH = "看着相对，本是一体。"
ZH20 = "国" * 20
EN = "The quick brown fox jumps over 1234567890"

# (标签, 载体, 文本, 字体, 样式, 覆盖参数)
V = [
    ("CAL1", CARRIER, ZH20, E.FONT_ZH, E.STYLE_ZH, {"Size": 0.0256}),
    ("CAL2", CARRIER, ZH20, E.FONT_ZH, E.STYLE_ZH, {"Size": 0.0350}),
    ("CAL3", CARRIER, ZH20, E.FONT_ZH, E.STYLE_ZH,
     {"Size": E.fusion_size(6, 117)}),                      # 当前公式值
    ("CAL4", CARRIER, ZH20, E.FONT_ZH, E.STYLE_ZH, {"Size": 0.0550}),
    ("CAL5", CARRIER, ZH20, E.FONT_ZH, E.STYLE_ZH, {"Size": 0.0700}),
    ("CAL6", CARRIER, EN, E.FONT_EN, E.STYLE_EN, {}),        # Direction 默认
    ("CAL7", CARRIER, EN, E.FONT_EN, E.STYLE_EN, {"Direction": 1}),
    ("CAL8", CARRIER, ZH, E.FONT_ZH, E.STYLE_ZH, {}),        # 中文字幕样例
    ("CAL9", LOGO_SMALL, "", None, None, {"__logo__": "small"}),
    ("CAL10", LOGO_SMALL, "", None, None, {"__logo__": "big"}),
]


def main():
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if not r:
        sys.exit("连不上达芬奇")
    pm = r.GetProjectManager()
    have = pm.GetProjectListInCurrentFolder() or []
    if PROJ in have:
        tmp = "_DSH_SWITCH"
        if tmp not in have:
            pm.CreateProject(tmp)
        else:
            pm.LoadProject(tmp)
        time.sleep(1.0)
        pm.DeleteProject(PROJ)
        time.sleep(0.8)
        try:
            pm.DeleteProject("_DSH_SWITCH")
        except Exception:
            pass
    if not pm.CreateProject(PROJ):
        sys.exit("建标定工程失败")
    p = pm.GetCurrentProject()
    p.SetSetting("timelineFrameRate", str(int(FPS)))
    p.SetSetting("timelineResolutionWidth", str(E.W))
    p.SetSetting("timelineResolutionHeight", str(E.H))

    total = SEG_F * len(V)
    # ---- 用 FCPXML 摆轨（AppendToTimeline 不可靠，见文件头注释）----
    # V1 必须是 kind="shot"（FCPXML 的主 spine），否则没有主轨、导入会得到空时间线
    acts = [dict(track="V1", kind="shot", clip=CARRIER,
                 clip_name="blank_alpha.mov", src_start=0.0, record=0.0,
                 dur=total / FPS, frames=total, label="黑底")]
    for i, (label, carrier, *_rest) in enumerate(V):
        acts.append(dict(track="V2", kind="overlay", path=carrier,
                         clip_name=os.path.basename(carrier), src_start=0.0,
                         record=i * SEG_F / FPS, dur=SEG_F / FPS,
                         frames=SEG_F, label=label))
    xml = os.path.join(E.AUTO, "out", "_calib.fcpxml")
    os.makedirs(os.path.dirname(xml), exist_ok=True)
    fcpxml_gen.build_fcpxml(acts, xml, total, FPS, PROJ)
    got = p.GetMediaPool().ImportTimelineFromFile(xml, {"timelineName": PROJ})
    time.sleep(2.0)
    tl = p.GetCurrentTimeline()
    if not tl:
        sys.exit("FCPXML 导入失败（没有时间线）")
    v2 = sorted(tl.GetItemListInTrack("video", 2) or [], key=lambda x: x.GetStart())
    print(f"=== 标定草稿 {PROJ} ===")
    print(f"  总长 {total/FPS:.1f}s，V2 段数 {len(v2)}（期望 {len(V)}）")
    if len(v2) != len(V):
        print(f"  ⚠️ 段数不符！实际 {len(v2)}")

    ok = 0
    for i, (label, carrier, text, font, style, over) in enumerate(V):
        if i >= len(v2):
            break
        it = v2[i]
        if over.get("__logo__"):
            it.SetProperty("CompositeMode", 4)      # 乘法，和正式工程一致
            print(f"  {label}  f{it.GetStart():>5}  {over['__logo__']} logo（已挂，乘法合成）")
            ok += 1
            continue
        tp = it.AddFusionComp().AddTool("TextPlus", -1, -1)
        tp.SetInput("StyledText", f"{label}  {text}", 0)
        tp.SetInput("Font", font, 0)
        tp.SetInput("Style", style, 0)
        tp.SetInput("Size", E.fusion_size(6, 117), 0)
        cx, cy = E.fusion_center(0, -1029)
        tp.SetInput("Center", {1: cx, 2: cy}, 0)
        r_, g_, b_ = E.hex_rgb(E.C_ECDABD)
        for k, val in (("Red1", r_), ("Green1", g_), ("Blue1", b_), ("Alpha1", 1.0)):
            tp.SetInput(k, val, 0)
        tp.SetInput("CharacterSpacing", E.CSPACE_DEFAULT, 0)
        tp.SetInput("WordSpacing", 1.0, 0)
        tp.SetInput("LineSpacing", 1.0, 0)
        tp.SetInput("HorizontalJustificationNew", E.HJUST_CENTER, 0)
        tp.SetInput("VerticalJustificationNew", 1, 0)
        tp.SetInput("Direction", 0, 0)
        tp.SetInput("ReadingDirection", 0, 0)
        for k, val in over.items():
            tp.SetInput(k, val, 0)
        mo = None
        try:
            mo = it.GetFusionCompByIndex(1).FindTool("MediaOut1")
        except Exception:
            pass
        if mo is not None:
            try:
                mo.ConnectInput("Input", tp)
            except Exception:
                pass
        show = over.get("Size", E.fusion_size(6, 117))
        print(f"  {label}  f{it.GetStart():>5}  Size={show}  {text[:20]!r}")
        ok += 1
    print(f"\n  处理 {ok}/{len(V)}")
    print(f"  保存: {pm.SaveProject()}")
    try:
        r.OpenPage("edit")
    except Exception:
        pass
    print(f"\n  请打开工程 {PROJ} 导出到桌面（黑底纯文字，{total/FPS:.1f}s）,")
    print("  把文件名告诉我，我量出字距后线性拟合把字号基准 K 定死。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
