#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_resolve5.py —— 达芬奇剪辑草稿构建器 v5（寄方堂 · 人参）

相对 build_resolve_final.py 修的 12 项（见 DSH_HANDOFF_ERRATA.md 第五节）：
   1. 字幕真正实现（Fusion Text+，6 条文字轨）
   2. 轨道数补齐：V1 镜头 / V2 logo / V3-V5 档案卡 / V6 中文 / V7 英文 / V8 引文 / V9 黑场
   3. BGM 从**第 0 帧**开始（原来从 2.0s）
   4. BGM 末尾 0.7s 淡出（预渲染 bgm_bed.wav）
   5. 配音从**档案卡结束后**开始（原来从 0）
   6. 档案卡 0-2s 的**人参全景背景**补上（并与首镜无缝衔接）
   7. 大 logo 放在 `总长-3s`（原来顺序排在 2s）
   8. logo 有缩放/位置/渐隐渐现（预渲染全画幅 alpha）
   9. 段间 0.5s 闪黑 + 末段 0.3s 渐隐（API 无转场 -> 乘法叠加层）
  10. 总时长 19.91s ≤ 20s
  11. 镜头时长 ≥ 配音时长（硬校验，不足则退用长素材）
  12. 坐标换算改正（`px_y=963.7-0.281*jy`，原式符号错 + 缺偏移）

达芬奇 API 的硬限制与对策
------------------------
  · 无转场 API      -> 闪黑用「不透明 白->黑 渐变 + 乘法合成」叠加层（见 prep_media.py）
  · 无关键帧 API    -> 所有淡入淡出都预渲染进素材，或交给 Fusion 动画
  · Insert* 不能指定轨 -> 文字用「全透明 PNG 载体 + AppendToTimeline(trackIndex, recordFrame) + AddFusionComp」

用法
    # 离线自检：不启动达芬奇，打印完整落点计划并校验规格
    /opt/homebrew/bin/python3 build_resolve5.py --timeline out/timeline_ch01.json --dry-run

    # 真建（达芬奇已启动、界面停在项目页）
    /opt/homebrew/bin/python3 build_resolve5.py --timeline out/timeline_ch01.json \
        --code "20260626～39" --ver v01 \
        --card-zh "23道芦碗·约26年" --card-en "23 crown-scars ·c. 26yrs" \
        --card-name "一阴一阳" --citation "周易·系辞上"
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

SCRIPT_API = os.environ.get("RESOLVE_SCRIPT_API") or (os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting") if os.name == "nt" else "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"); _RESOLVE_LIB = os.environ.get("RESOLVE_SCRIPT_LIB") or (r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll" if os.name == "nt" else "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")

# Text+ 的**稳定英文输入 ID**（由 resolve_probe4.py 从 GetAttrs()["INPS_ID"] 实测得到）。
# 为什么必须核对：达芬奇的 SetInput 对**不存在的输入名会静默接受、不报错**，
# 写错名字只会导致样式悄悄不生效（"Tracking"/"Opacity" 就是这么踩到的）。
ASSETS_DIR = os.path.join(E.AUTO, "assets")
CARRIER = os.path.join(ASSETS_DIR, "blank_alpha.mov")

TEXT_INPUT_IDS = {
    "StyledText", "Font", "Style", "Size", "Center", "Red1", "Green1", "Blue1", "Alpha1",
    "HorizontalJustificationNew", "Direction", "ReadingDirection", "WordSpacing",
    "LineSpacing",
    "CharacterSpacing", "WordSpacing", "LineSpacing", "VerticalJustificationNew",
    "HorizontalJustificationNew", "Opacity1", "Background", "TextText",
}


# ===================================================================== 计划
def plan_timeline(tl, card_zh, card_en, card_name, citation, flash_mode="pairs"):
    """把时间轴 JSON 变成一份**纯数据**的落点计划（dry-run 与真建共用）。

    返回 (actions, problems)
      actions: [{track, kind, ...}]
      kind: shot / overlay / text / voice / bgm
    """
    segs = tl["segments"]
    fps = E.FPS
    card = tl.get("card_sec", E.CARD_SEC)
    gap = tl.get("gap", E.GAP_SEC)
    total = tl["total"]
    durs = E.load_durations().get("chapters", {}).get(tl.get("chapter"), {})
    _mdir = tl.get("media_dir", "")
    acts, problems = [], []
    n = len(segs)

    # ---- 全部换算到**整数帧**再排 ----
    # 之前按秒累加、再各自 round 成帧，会出现"上一段结束 242 / 下一段开始 241"
    # 这种 1 帧重叠。这里统一用帧边界：每句占 [t0f[i], t0f[i+1])。
    def F(sec):
        return int(round(sec * fps))

    # 优先用时间轴里**按帧算好的** t0f / frames（权威）；
    # 没有（旧文件）才退回按秒推，保证兼容。
    if all("t0f" in s and "frames" in s for s in segs):
        t0f = [int(s["t0f"]) for s in segs]
        vf = [int(s["frames"]) for s in segs]
        endf = t0f[-1] + vf[-1] + int(round(gap * fps)) * 0
        endf = t0f[-1] + vf[-1]
    else:
        t0f = [F(s["t0"]) for s in segs]
        endf = F(total)
        vf = [int(math.ceil(s["dur"] * fps - 1e-6)) for s in segs]
    nxtf = t0f[1:] + [endf]
    for i, s in enumerate(segs):
        slot = nxtf[i] - t0f[i]
        if vf[i] > slot:
            problems.append(f"句{i+1} 配音 {vf[i]}f 超出档位 {slot}f"
                            f"（{vf[i]/fps:.3f}s > {slot/fps:.3f}s）")

    # ---------- V1 镜头 ----------
    # 档案卡背景：用第一句的全景素材，从 0s 起连续，并与第一句无缝衔接
    # 档案卡背景：**必须是整根完整的参**，且与第一句用**不同的镜头**
    # （用户 2026-10-06 要求：不能合并成一个镜头；即使是同一段素材也要用不同时间段，
    #   并做慢放或整段放大）。另外要跳过素材开头的黑帧（用户反馈"开头前几帧全黑"）。
    card_clip = E.card_clip_for(durs) or segs[0]["clip_name"]
    if card_clip in [x["clip_name"] for x in segs]:
        # 正文里仍用到了整根全景那条 —— 说明它不是"最长 A"或已被分配，
        # 这时让第一句错开时间段（上面 i==0 分支已处理），并靠缩放区分。
        pass
    card_ss = E.HEAD_TRIM_SEC
    acts.append(dict(track="V1", kind="shot",
                     clip=os.path.join(_mdir, card_clip), clip_name=card_clip,
                     src_start=round(card_ss, 3), record=0.0,
                     dur=round(F(card) / fps, 4), frames=F(card),
                     zoom=1.0, label="档案卡背景(整根全景)"))
    v1_start = 0
    for i, s in enumerate(segs):
        # 每镜覆盖到下一句起点（含句间停顿），保证 V1 连续且**不重叠**
        d = (nxtf[i] - t0f[i]) / fps
        ss = s.get("src_start") or 0.0
        if i == 0 and s["clip_name"] == card_clip:
            ss = E.HEAD_TRIM_SEC + card          # 与档案卡错开时间段
        av = durs.get(s["clip_name"])
        if av is not None and ss + d > av + 1e-6:
            problems.append(f"句{i+1} V1 越界: {s['clip_name']} src={ss:.2f}+{d:.2f}"
                            f" > {av:.2f}s")
        acts.append(dict(track="V1", kind="shot",
                         clip=os.path.join(_mdir, s["clip_name"]),
                         clip_name=s["clip_name"],
                         src_start=round(ss, 3), record=round(t0f[i] / fps, 4),
                         dur=round(d, 4), frames=nxtf[i] - t0f[i],
                         label=f"句{i+1}"))

    # ---------- V2 logo ----------
    acts.append(dict(track="V2", kind="overlay", asset="logo_small.mov",
                     path=os.path.join(ASSETS_DIR, "logo_small.mov"),
                     record=0.0, dur=E.LOGO_SPEC["small"]["dur"], label="小 logo"))
    # 大 logo：**与最后一个镜头完全重合**（同起点、同时长），淡入随比例
    _v1 = sorted([x for x in acts if x["track"] == "V1"], key=lambda x: x["record"])
    _last = _v1[-1] if _v1 else None
    if _last:
        bframes, bdur, bfade = E.logo_big_geom(_last["frames"])
        bname = E.logo_big_name(bframes)
        acts.append(dict(track="V2", kind="overlay", asset=bname,
                         path=os.path.join(ASSETS_DIR, bname),
                         record=_last["record"], dur=bdur,
                         zoom=E.LOGO_SPEC["big"].get("zoom", 1.0),
                         label=f"大 logo(与末镜等长 {bframes}f, 淡入{bfade:.2f}s)"))

    # ---------- V9 黑场（闪黑 + 末段渐隐）----------
    # 用户要求：**所有镜头与镜头之间**都要加闪黑（原来只在 chapters.json 的 pair
    # 边界加，太少）。这里直接从 V1 的交界推，保证"有交界就有闪黑"，不会再漏。
    if flash_mode != "none":
        v1_acts = sorted([a for a in acts if a["track"] == "V1"],
                         key=lambda a: a["record"])
        for k, a in enumerate(v1_acts):
            if k == 0:
                continue
            if flash_mode == "pairs":
                # 兼容旧行为：只在 pair 变化处
                i = k - v1_start
                pr = segs[i].get("pair") if 0 <= i < len(segs) else None
                pv = segs[i - 1].get("pair") if 0 <= i - 1 < len(segs) else None
                if pr is None or pv is None or pr == pv:
                    continue
            j = a["record"]
            acts.append(dict(track="V9", kind="overlay",
                             asset=E.ramp_name("up", E.FLASH_OUT_FRAMES),
                             path=os.path.join(ASSETS_DIR,
                                               E.ramp_name("up", E.FLASH_OUT_FRAMES)),
                             record=round(j - E.FLASH_OUT_FRAMES / fps, 3),
                             dur=E.FLASH_OUT_FRAMES / fps,
                             multiply=True, label=f"闪黑-出 @{a['label']}"))
            acts.append(dict(track="V9", kind="overlay",
                             asset=E.ramp_name("down", E.FLASH_IN_FRAMES),
                             path=os.path.join(ASSETS_DIR,
                                               E.ramp_name("down", E.FLASH_IN_FRAMES)),
                             record=j, dur=E.FLASH_IN_FRAMES / fps,
                             multiply=True, label=f"闪黑-入 @{a['label']}"))
    acts.append(dict(track="V9", kind="overlay",
                     asset=E.ramp_name("up", E.FADE_VIDEO_TAIL_FRAMES),
                     path=os.path.join(ASSETS_DIR,
                                       E.ramp_name("up", E.FADE_VIDEO_TAIL_FRAMES)),
                     record=round(total - E.FADE_VIDEO_TAIL_SEC, 3),
                     dur=E.FADE_VIDEO_TAIL_SEC, multiply=True, label="末段渐隐"))

    # ---------- V3-V5 档案卡三轨 ----------
    if card_zh:
        acts.append(dict(track="V3", kind="text", record=0.0, dur=card,
                         text=card_zh, spec="card_zh", label="档案卡中文",
                         path=CARRIER))
    if card_en:
        acts.append(dict(track="V4", kind="text", record=0.0, dur=card,
                         text=card_en, spec="card_en", label="档案卡英文",
                         path=CARRIER))
    if card_name:
        acts.append(dict(track="V5", kind="text", record=0.0, dur=card,
                         text=card_name, spec="card_nm", label="档案卡竖排题名",
                         path=CARRIER))

    # ---------- V6/V7 中英字幕（覆盖到下一句，避免 0.12s 停顿处闪断）----------
    for i, s in enumerate(segs):
        d = (nxtf[i] - t0f[i]) / fps
        is_last = (i == n - 1)
        try:
            import fontsel as _fs
        except Exception:
            _fs = None
        sp_zh = E.SPEC["quote" if s.get("quote") else "zh"]
        sp_en = E.SPEC["en"]
        em_zh = E.px_size(sp_zh["size"], sp_zh["scale"])
        em_en = E.px_size(sp_en["size"], sp_en["scale"])

        # 中文**不折行**：超宽的中文句在上游 batch_timelines.split_wide_segments 里
        # 就拆成两段了（配音/字幕/镜头一起拆）。这里只做兜底断言，防止漏拆后静默溢出画面。
        zh_w = 0.0
        if _fs is not None and s.get("zh"):
            try:
                zh_w = _fs.measure(sp_zh["font"], s["zh"], em_zh)
                if zh_w is None:
                    zh_w = len(s["zh"]) * em_zh
            except Exception:
                zh_w = len(s["zh"]) * em_zh
        if zh_w > E.SUB_SAFE_W + 1e-6:
            problems.append(f"中文句{i+1} 宽 {zh_w:.0f}px > 安全宽 {E.SUB_SAFE_W:.0f}px "
                            f"（应在 segments_for 里拆句）: {s['zh'][:20]}")
        acts.append(dict(track="V6", kind="text", record=round(t0f[i] / fps, 4),
                         dur=round(d, 4), frames=nxtf[i] - t0f[i],
                         text=s["zh"], spec=("quote" if s.get("quote") else "zh"),
                         fade_out=(0.3 if is_last else 0.0), label=f"中文句{i+1}",
                         path=CARRIER))
        # 英文按词边界折行，最多 3 行（拆句后仍可能一行放不下）
        en_txt, en_lines = s["en"], 1
        try:
            mx = E.en_wrap_max(zh_w or len(s["zh"] or "") * em_zh)
            en_w = _fs.measure(sp_en["font"], s["en"], em_en)
            if en_w is not None and en_w > mx:
                ls = _fs.wrap_balanced(s["en"], sp_en["font"], em_en, mx,
                                       max_lines=3, hard_max_px=E.SUB_SAFE_W)
                if len(ls) > 1:
                    en_txt = chr(10).join(ls)
                    en_lines = len(ls)
        except Exception as e:
            print(f"    warning: 英文折行失败(句{i+1}): {str(e)[:60]}")
        acts.append(dict(track="V7", kind="text", record=round(t0f[i] / fps, 4),
                         dur=round(d, 4), frames=nxtf[i] - t0f[i],
                         text=en_txt, spec="en", lines=en_lines,
                         fade_out=(0.3 if is_last else 0.0), label=f"英文句{i+1}",
                         path=CARRIER))

    # ---------- V8 引文竖排（时长 = 所在镜头的配音）----------
    if citation:
        qi = next((i for i, s in enumerate(segs) if s.get("quote")), n - 1)
        # 规格 §5.6 要"0.3s 渐现 + 0.3s 渐隐"，但**达芬奇无法脚本化 Fusion 关键帧**
        # （实测：SetInput(...,time) 不产生动画；SetKeyframe/GetSpline 是 None；
        #   AddModifier 返回 True 但拿不到 spline 对象 —— 见 resolve_probe4_report.txt）。
        # 解法：让引文跨度取"档位"（到下一句起点）而不是配音长度——
        # 起点正好落在 pair 边界的闪黑渐现上，末端正好落进闪黑渐隐里，
        # 于是 0.3s 渐现+渐隐由已实测可用的乘法叠加层自然实现。
        slot = nxtf[qi] - t0f[qi]
        acts.append(dict(track="V8", kind="text", record=round(t0f[qi] / fps, 4),
                         dur=round(slot / fps, 4), frames=slot,
                         text=citation, spec="cite", label="引文竖排(靠闪黑渐变实现渐现渐隐)",
                         path=CARRIER))

    # ---------- A1 配音 ----------
    for i, s in enumerate(segs):
        acts.append(dict(track="A1", kind="voice", wav=os.path.basename(s["wav"]),
                         path=os.path.join(E.AUTO, "voice_sent", os.path.basename(s["wav"])),
                         record=round(t0f[i] / fps, 4), dur=round(vf[i] / fps, 4),
                         frames=vf[i], label=f"配音句{i+1}"))

    # ---------- A2 BGM ----------
    acts.append(dict(track="A2", kind="bgm", asset="bgm_bed.wav",
                     path=os.path.join(ASSETS_DIR, "bgm_bed.wav"),
                     record=0.0, dur=total, label="BGM(循环+末尾0.7s淡出)"))
    return acts, problems


def print_plan(acts, problems, tl):
    fps = E.FPS
    by_track = {}
    for a in acts:
        by_track.setdefault(a["track"], []).append(a)
    order = [t for t, _, _ in E.TRACK_LAYOUT if t in by_track]
    print(f"=== 落点计划（{fps:.0f}fps，总长 {tl['total']:.2f}s）===")
    for t in order:
        items = sorted(by_track[t], key=lambda x: x["record"])
        name = dict((a, c) for a, _, c in E.TRACK_LAYOUT)[t]
        print(f"\n  {t} · {name}  ({len(items)} 段)")
        for a in items:
            r = int(round(a["record"] * fps))
            d = int(a["frames"]) if a.get("frames") else int(round(a["dur"] * fps))
            extra = ""
            if a["kind"] == "shot":
                extra = f"{a['clip']:<12} src={a['src_start']:.2f}s"
            elif a["kind"] == "overlay":
                extra = f"{a['asset']:<20}{'[乘法]' if a.get('multiply') else ''}"
            elif a["kind"] == "text":
                extra = f"{a['spec']:<7} {a['text'][:22]!r}"
            elif a["kind"] == "voice":
                extra = a["wav"]
            elif a["kind"] == "bgm":
                extra = a["asset"]
            print(f"    f{r:>5}-{r+d:<5} ({d:>3}f {a['dur']:5.2f}s)  {extra}")
    if problems:
        print("\n  ❌ 计划问题:")
        for p in problems:
            print(f"     {p}")
    return len(problems)


# ===================================================================== Resolve
def safe_page(r):
    """把界面切回 Edit 页。

    教训（用户 2026-10-06）：上一轮跑完无头渲染/导出后，桌面卡在达芬奇
    "放大预览"页面，只能强制关机。根因是我调用了 StartRendering /
    ExportCurrentFrameAsStill，把界面留在**交付页**。
    **本流水线从此不再调用任何渲染/导出接口**，并且每次结束都把页面切回 edit。
    """
    try:
        r.OpenPage("edit")
    except Exception:
        pass


def connect():
    os.environ["RESOLVE_SCRIPT_API"] = SCRIPT_API
    os.environ["RESOLVE_SCRIPT_LIB"] = _RESOLVE_LIB
    sys.path.insert(0, os.path.join(SCRIPT_API, "Modules"))
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if not r:
        raise SystemExit("无法连接达芬奇（确认已启动、界面停在项目页）")
    return r


def fusion_connect(comp, src, dst, in_name=None):
    """把 Fusion 工具连起来。Fusion 各版本 API 习惯不一，逐个试。"""
    tries = []
    if in_name:
        tries.append(lambda: dst.ConnectInput(in_name, src))
    tries += [
        lambda: dst.ConnectInput("Input", src),
        lambda: dst.ConnectInput("Main", src),
        lambda: dst.FindMainInput(1).ConnectTo(src.Output),
        lambda: src.Output.ConnectTo(dst.FindMainInput(1)),
        lambda: src.Output.ConnectTo(dst.Input),
    ]
    for i, fn in enumerate(tries):
        try:
            fn()
            return i
        except Exception:
            continue
    return None


def make_text_item(tlobj, mp, blank_item, act, log):
    """文字轨：全透明 PNG 载体 + AppendToTimeline(trackIndex, recordFrame) + Fusion Text+。"""
    fps = E.FPS
    tindex = int(act["track"][1:])
    rf = int(round(act["record"] * fps))
    ef = max(1, int(round(act["dur"] * fps)))
    got = mp.AppendToTimeline([{"mediaPoolItem": blank_item, "startFrame": 0,
                                "endFrame": ef, "trackIndex": tindex,
                                "mediaType": 1, "recordFrame": rf}])
    if not got:
        log(f"    ❌ {act['label']}: 载体未能放入 {act['track']}")
        return None
    item = got[0]
    spec = E.SPEC[act["spec"]]
    ok = add_text_fusion(item, act["text"], spec, act, log)
    log(f"    {'✅' if ok else '⚠️ '} {act['label']:<12} {act['track']} "
        f"f{rf}-{rf+ef}  {act['spec']}")
    return item


def add_text_fusion(item, text, spec, act, log):
    """在载体片段上建 Fusion Text+ 并写样式。"""
    try:
        comp = item.AddFusionComp()
        if comp is None:
            return False
        tp = comp.AddTool("TextPlus", -1, -1)
        if tp is None:
            return False
        # 竖排：逐字换行（Fusion 没有可靠的 CJK 竖排输入，换行最稳）
        body = "\n".join(list(text)) if spec["vertical"] else text
        tp.SetInput("StyledText", body, 0)
        # Font 必须是**字体族名**；再单独设 Style。
        # 路径写进 Font 会让 Fusion 把路径当文字渲染并导致渲染失败（曾导致无法导出）。
        if "Font" in TEXT_INPUT_IDS:
            tp.SetInput("Font", spec["font"], 0)
        if "Style" in TEXT_INPUT_IDS:
            tp.SetInput("Style", spec.get("style", "Regular"), 0)
        cx, cy = E.fusion_center(spec["jx"], spec["jy"])
        # 多行时把 Center 下移 (行数-1)/2 x 行高，保证**首行停在原位**、第二行往下排
        _nl = int(act.get("lines") or 1)
        if _nl > 1:
            try:
                import fontsel as _fs2
                _lh = _fs2.line_height(spec["font"],
                                       E.px_size(spec["size"], spec["scale"]))
            except Exception:
                _lh = E.px_size(spec["size"], spec["scale"]) * 1.2
            # 注意 Fusion 归一化 y 是**翻转的**（1 - px_y/H）：
            # 想让画面上的位置**下移**，归一化值要**减**。之前写成加号，
            # 结果首行反而上移、第二行占了原位 —— 与要求正好相反。
            cy = max(0.0, min(1.0, cy - (_nl - 1) / 2.0 * _lh / E.H))
        tp.SetInput("Size", E.fusion_size(spec["size"], spec["scale"]), 0)
        tp.SetInput("Center", {1: cx, 2: cy}, 0)
        r, g, b = E.hex_rgb(spec["color"])
        for k, v in (("Red1", r), ("Green1", g), ("Blue1", b), ("Alpha1", 1.0)):
            try:
                tp.SetInput(k, v, 0)
            except Exception:
                pass
        # 字距：ID 是 CharacterSpacing（不是 "Tracking"），而且它是**倍数**
        # （默认 1.0）。把剪映的"字距像素"直接写进去会把整行拉开。
        if "CharacterSpacing" in TEXT_INPUT_IDS:
            px = E.px_size(spec["size"], spec["scale"])
            cs = round(E.CSPACE_DEFAULT + spec["tr"] / max(1.0, px), 4) \
                if spec.get("tr") else E.CSPACE_DEFAULT
            tp.SetInput("CharacterSpacing", cs, 0)
            tp.SetInput("WordSpacing", 1.0, 0)
            tp.SetInput("LineSpacing", 1.0, 0)
        # 水平对齐：默认 3.0 会**两端对齐**把整行拉开，改成 1.0（居中）
        if "HorizontalJustificationNew" in TEXT_INPUT_IDS:
            tp.SetInput("HorizontalJustificationNew", E.HJUST_CENTER, 0)
        if "VerticalJustificationNew" in TEXT_INPUT_IDS:
            tp.SetInput("VerticalJustificationNew", 0 if spec["vertical"] else 1, 0)
        # 文字方向：显式设成自动（0），避免出现整体反向
        if "Direction" in TEXT_INPUT_IDS:
            tp.SetInput("Direction", 0, 0)
        if "ReadingDirection" in TEXT_INPUT_IDS:
            tp.SetInput("ReadingDirection", 0, 0)
        # 不透明度：**没有 "Opacity"**（同样不存在），实测是 Alpha1 / Opacity1
        if spec["opacity"] < 1.0:
            for key in ("Alpha1", "Opacity1"):
                if key in TEXT_INPUT_IDS:
                    tp.SetInput(key, spec["opacity"], 0)
                    break
        # 注意：淡入淡出**不能**靠 SetInput(name, value, time) —— 实测不产生插值。
        # 所有渐隐/渐现都由 V9 的乘法叠加层实现（见 plan_timeline 注释）。
        # 接到 MediaOut（否则文字不会出现在输出里）
        mo = comp.FindTool("MediaOut1")
        if mo is not None:
            fusion_connect(comp, tp, mo)
        return True
    except Exception as e:
        log(f"    Fusion 失败: {str(e)[:90]}")
        return False



# ===================================================================== FCPXML 路径
def build_via_fcpxml(r, pm, tl, acts, args, proj):
    """正确架构：**先生成 FCPXML 导入建时间线，再往导入得到的片段上挂 Fusion Text+**。

    为什么不能直接 AppendToTimeline：见 ERRATA §3.6 —— 那样放进去的片段
    完全不会持久化（内存可见、SaveProject 返回 True、一切换项目就全空）。
    FCPXML 导入建出来的时间线完全持久，且在其片段上挂的 Fusion 合成同样持久。
    """
    import fcpxml_gen as G
    xml = os.path.join(E.AUTO, "out", f"ch{tl['chapter']}.fcpxml")
    fps = E.FPS
    endf = int(round(tl["total"] * fps))
    _, st = G.build_fcpxml(acts, xml, endf, 50.0, proj)
    print(f"\n-- 生成 FCPXML: {xml}")
    print(f"   素材 {st['assets']} 条 / V1 段 {st['v1']} / 附加轨段 {st['lanes']}")

    if proj in (pm.GetProjectListInCurrentFolder() or []):
        if args.replace:
            print(f"   --replace: 删除已存在的 {proj}")
            # 关键：**当前项目删不掉**。以前硬编码切到 "ch01_v3"，
            # 那个工程被用户删掉后就失效了 -> 删除失败 -> 建项目失败。
            # 改成先建/切到一个临时项目再删，跟工程名无关。
            tmp = "_DSH_SWITCH"
            if tmp not in (pm.GetProjectListInCurrentFolder() or []):
                pm.CreateProject(tmp)
            else:
                pm.LoadProject(tmp)
            time.sleep(1.2)
            for _ in range(4):
                if pm.DeleteProject(proj):
                    print(f"   ✅ 已删除旧 {proj}")
                    break
                time.sleep(0.9)
            else:
                print(f"   ⚠️ 删不掉旧 {proj}，将另取名字")
            time.sleep(1.0)
        else:
            for n in range(2, 40):
                c = f"{args.code or tl.get('code', '')}v{n:02d}"
                if c not in pm.GetProjectListInCurrentFolder():
                    proj = c
                    break
            print(f"   已存在，改用 {proj}")
        G.build_fcpxml(acts, xml, endf, 50.0, proj)
    if not pm.CreateProject(proj):
        raise SystemExit("建项目失败")
    project = pm.GetCurrentProject()
    # 清掉切换用的临时项目（它此刻不是当前项目了，可以删）
    if "_DSH_SWITCH" in (pm.GetProjectListInCurrentFolder() or []):
        try:
            pm.DeleteProject("_DSH_SWITCH")
        except Exception:
            pass
    try:
        project.SetSetting("timelineFrameRate", str(fps))
        project.SetSetting("timelineResolutionWidth", str(E.W))
        project.SetSetting("timelineResolutionHeight", str(E.H))
    except Exception:
        pass

    mp = project.GetMediaPool()
    tlobj = mp.ImportTimelineFromFile(xml, {"timelineName": proj})
    if not tlobj:
        raise SystemExit("FCPXML 导入失败")
    project.SetCurrentTimeline(tlobj)
    time.sleep(1.0)
    print(f"   导入完成: video={tlobj.GetTrackCount('video')} "
          f"audio={tlobj.GetTrackCount('audio')}")

    for t, kind, name in E.TRACK_LAYOUT:
        idx = int(t[1:])
        try:
            tlobj.SetTrackName(kind, idx, name)
        except Exception:
            pass

    # V9 黑场叠加层必须设成**乘法合成**。
    # FCPXML 里没有"合成模式"这个属性，导入后必须用 API 补上；
    # 不设的话白→黑渐变会当成不透明图层盖住整个画面（全屏变白/变黑）。
    # 实测：导入后再 SetProperty 也**会持久化**（与 Fusion 合成同理）。
    print("\n-- V9 黑场：设乘法合成 --")
    n_mul = 0
    for it in (tlobj.GetItemListInTrack("video", 9) or []):
        try:
            it.SetProperty("CompositeMode", getattr(r, "COMPOSITE_MULTIPLY", 4))
            n_mul += 1
        except Exception as e:
            print(f"    ❌ 乘法合成设置失败: {str(e)[:60]}")
    print(f"    V9 乘法合成 {n_mul}/{len(tlobj.GetItemListInTrack('video', 9) or [])}")

    # 叠加层缩放（用户 2026-10-06：结尾大 logo 的"缩放"设成 0.65，不占满全屏）
    nz = 0
    for i in range(1, tlobj.GetTrackCount("video") + 1):
        for it in (tlobj.GetItemListInTrack("video", i) or []):
            z = None
            key = (i, it.GetStart())
            for a in acts:
                tr = int(a["track"][1:]) if a["track"].startswith("V") else 0
                if tr == i and abs(round(a["record"] * fps) - it.GetStart()) < 2:
                    z = a.get("zoom")
                    break
            if z and abs(float(z) - 1.0) > 1e-6:
                try:
                    it.SetProperty("ZoomX", float(z))
                    it.SetProperty("ZoomY", float(z))
                    nz += 1
                except Exception as e:
                    print(f"    ❌ 缩放设置失败: {str(e)[:60]}")
    if nz:
        print(f"\n-- 叠加层缩放：{nz} 段已设 --")

    # 往导入得到的文字载体片段上挂 Fusion Text+
    print("\n-- 挂 Fusion Text+ --")
    by_track = {}
    for i in range(1, tlobj.GetTrackCount("video") + 1):
        by_track[i] = {it.GetStart(): it for it in (tlobj.GetItemListInTrack("video", i) or [])}
    ok = 0
    miss = []
    for a in [x for x in acts if x["kind"] == "text"]:
        idx = int(a["track"][1:])
        rf = int(round(a["record"] * fps))
        it = by_track.get(idx, {}).get(rf)
        if it is None:
            miss.append(f"{a['label']}@{a['track']}f{rf}")
            continue
        if add_text_fusion(it, a["text"], E.SPEC[a["spec"]], a, print):
            ok += 1
    n_txt = len([x for x in acts if x["kind"] == "text"])
    print(f"\n  文字 {ok}/{n_txt} 挂好" + (f"   未找到: {miss}" if miss else ""))

    print(f"\n  保存: {pm.SaveProject()}")
    try:
        with open(os.path.join(E.AUTO, "out", "last_project.txt"), "w",
                  encoding="utf-8") as _f:
            _f.write(proj)
    except Exception:
        pass
    time.sleep(2.0)

    # ---- 交付前自检（含"切走再切回"持久化验证）----
    print("\n=== 交付前自检 ===")
    chk = []
    chk.append(("视频轨数", tlobj.GetTrackCount("video") >= 9,
                tlobj.GetTrackCount("video")))
    chk.append(("音频轨数", tlobj.GetTrackCount("audio") >= 2,
                tlobj.GetTrackCount("audio")))
    v1 = tlobj.GetItemListInTrack("video", 1) or []
    nseg = len(tl["segments"])
    # V1 段数由计划决定：档案卡背景与首句同素材时会**合并成一个镜头**，
    # 所以不能写死 nseg+1（这里踩过：合并后 7 段被当成缺一段）。
    want_v1 = len([x for x in acts if x["track"] == "V1"])
    chk.append(("V1 段数", len(v1) == want_v1, f"{len(v1)}/{want_v1}"))
    a1 = tlobj.GetItemListInTrack("audio", 1) or []
    a2 = tlobj.GetItemListInTrack("audio", 2) or []
    chk.append(("配音段数", len(a1) == nseg, f"{len(a1)}/{nseg}"))
    chk.append(("BGM 段数", len(a2) >= 1, len(a2)))
    bad = 0
    for i in range(1, 10):
        for it in (tlobj.GetItemListInTrack("video", i) or []):
            pass
    for tk, idx, want in (("V3", 3, 1), ("V4", 4, 1), ("V5", 5, 1),
                          ("V6", 6, nseg), ("V7", 7, nseg), ("V8", 8, 1)):
        g = tlobj.GetItemListInTrack("video", idx) or []
        chk.append((f"{tk} 段数", len(g) == want, f"{len(g)}/{want}"))
    chk.append(("V9 段数", len(tlobj.GetItemListInTrack("video", 9) or []) ==
                len([x for x in acts if x["track"] == "V9"]),
                len(tlobj.GetItemListInTrack("video", 9) or [])))
    # Fusion 合成数
    ncomp = 0
    for idx in (3, 4, 5, 6, 7, 8):
        for it in (tlobj.GetItemListInTrack("video", idx) or []):
            if it.GetFusionCompCount():
                ncomp += 1
    chk.append(("Fusion 合成数", ncomp == n_txt, f"{ncomp}/{n_txt}"))
    v9 = tlobj.GetItemListInTrack("video", 9) or []
    nmul = 0
    for it in v9:
        try:
            if str(it.GetProperty("CompositeMode")) == "4":
                nmul += 1
        except Exception:
            pass
    chk.append(("V9 乘法合成数", nmul == len(v9) and len(v9) > 0, f"{nmul}/{len(v9)}"))
    # 大 logo 缩放
    want_z = E.LOGO_SPEC["big"].get("zoom")
    if want_z:
        v2 = sorted(tlobj.GetItemListInTrack("video", 2) or [],
                    key=lambda x: x.GetStart())
        got_z = None
        if v2:
            try:
                got_z = round(float(v2[-1].GetProperty("ZoomX")), 4)
            except Exception:
                got_z = None
        chk.append((f"大 logo 缩放={want_z}", got_z is not None
                    and abs(got_z - want_z) < 0.01, str(got_z)))
    name = project.GetName()
    for nm, good, val in chk:
        print(f"  {'✅' if good else '❌'} {nm}: {val}")

    # ---- 持久化验证：切走再切回 ----
    print("\n=== 持久化验证（这是本环境最容易翻车的一步）===")
    before = sum(len(tlobj.GetItemListInTrack("video", i) or []) for i in range(1, 10))
    pm.LoadProject("ch01_v3")
    time.sleep(2.0)
    pm.LoadProject(name)
    time.sleep(2.0)
    p2 = pm.GetCurrentProject()
    t2 = p2.GetCurrentTimeline()
    after = sum(len(t2.GetItemListInTrack("video", i) or []) for i in range(1, 10))
    c2 = 0
    for idx in (3, 4, 5, 6, 7, 8):
        for it in (t2.GetItemListInTrack("video", idx) or []):
            if it.GetFusionCompCount():
                c2 += 1
    print(f"  视频段数 {before} -> {after}  {'✅ 持久' if after == before and before else '❌ 丢失'}")
    print(f"  Fusion 合成 {ncomp} -> {c2}  {'✅ 持久' if c2 == ncomp and ncomp else '❌ 丢失'}")
    ok_all = all(g for _, g, _ in chk) and after == before and after > 0
    print(f"\n  {'✅ 全部通过' if ok_all else '❌ 有未通过项'}")
    safe_page(r)
    return 0 if ok_all else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--code", default="")
    ap.add_argument("--ver", default="v01")
    ap.add_argument("--card-zh", default="")
    ap.add_argument("--card-en", default="")
    ap.add_argument("--card-name", default="")
    ap.add_argument("--citation", default="")
    ap.add_argument("--assets", default=os.path.join(E.AUTO, "assets"))
    ap.add_argument("--flash", choices=["pairs", "all", "none"], default="all",
                    help="闪黑位置：all=所有镜头交界（默认，用户要求）；pairs=只在 pair 边界")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划 + 自检，不碰达芬奇")
    ap.add_argument("--media-dir", default=None,
                    help="覆盖原素材目录（把外置盘素材拷到本地盘后用这个指过去）")
    ap.add_argument("--standin", default=None,
                    help="把 V1 镜头全部换成这个本地文件（用于外置盘不可用时端到端验证管线）")
    ap.add_argument("--replace", action="store_true",
                    help="目标项目名已存在时，删掉它并沿用该名字（规格要求 v01 时用）")
    ap.add_argument("--via-fcpxml", action="store_true",
                    help="用 FCPXML 导入建时间线（**唯一能持久化的路径**，见 ERRATA §3.6）")
    a = ap.parse_args()

    with open(a.timeline, encoding="utf-8") as f:
        tl = json.load(f)
    if a.media_dir:
        print(f"  --media-dir: 素材目录 -> {a.media_dir}")
        tl["media_dir"] = a.media_dir

    # ---- 规格自检 ----
    durs = E.load_durations().get("chapters", {}).get(tl.get("chapter"), {})
    errs, warns = E.validate(tl, durs or None)
    print(f"=== 规格自检 ===")
    for e in errs:
        print(f"  ❌ {e}")
    for w in warns:
        print(f"  ⚠️  {w}")
    if not errs and not warns:
        print("  ✅ 通过")

    acts, problems = plan_timeline(tl, a.card_zh, a.card_en, a.card_name,
                                   a.citation, a.flash)
    if a.standin:
        for x in acts:
            if x["kind"] == "shot":
                x["clip"] = a.standin
        print(f"\n  ⚠️ --standin: V1 镜头全部替换为 {a.standin}（仅用于管线验证）")
    print()
    print_plan(acts, problems, tl)

    # ---- 素材存在性 ----
    print("\n=== 素材检查 ===")
    need = ["blank_alpha.mov", "logo_small.mov", "logo_big.mov", "bgm_bed.wav",
            E.ramp_name("up", E.FLASH_OUT_FRAMES),
            E.ramp_name("down", E.FLASH_IN_FRAMES),
            E.ramp_name("up", E.FADE_VIDEO_TAIL_FRAMES)]
    miss = [n for n in need if not os.path.exists(os.path.join(a.assets, n))]
    print(f"  叠加层: {len(need)-len(miss)}/{len(need)} 就绪"
          + (f"  缺: {miss}" if miss else ""))
    src_dir = tl.get("media_dir", "")
    # 注意：不能用 os.path.isdir —— 外置盘被 TCC 挡住时，挂载点的 stat 仍然成功，
    # isdir 会返回 True（假阳性）。必须真的列一次目录。
    src_ok = False
    if src_dir:
        try:
            names = [x for x in os.listdir(src_dir) if not x.startswith("._")]
            src_ok = any(x.upper().endswith((".MP4", ".MOV")) for x in names)
            if src_ok:
                print(f"  ✅ 原素材目录可读: {src_dir}  ({len(names)} 项)")
        except Exception as e:
            print(f"  ❌ 原素材目录不可读: {src_dir!r}")
            print(f"     {type(e).__name__}: {e}")
    if not src_ok:
        print("     ⚠️ 本进程读不了（TCC），但**达芬奇自己能读**（已实测），不构成阻塞。")
        print("        构建时原素材走 strict=False，由 Resolve 导入。")

    if a.dry_run:
        print("\n(--dry-run：未连接达芬奇)")
        return 1 if (errs or problems or miss) else 0

    # ================= 真建 =================
    r = connect()
    pm = r.GetProjectManager()
    if a.via_fcpxml:
        base0 = a.code or (tl.get("code", "") or tl["chapter"])
        return build_via_fcpxml(r, pm, tl, acts, a, f"{base0}{a.ver}")
    base = a.code or (tl.get("code", "") or tl["chapter"])
    proj = f"{base}{a.ver}"
    print(f"\n=== 构建 {proj} ===")
    print(f"  {r.GetProductName()} {r.GetVersionString()}")
    if proj in (pm.GetProjectListInCurrentFolder() or []):
        for n in range(2, 40):
            c = f"{base}v{n:02d}"
            if c not in pm.GetProjectListInCurrentFolder():
                proj = c
                break
        print(f"  已存在，改用 {proj}")
    if not pm.CreateProject(proj):
        raise SystemExit("建项目失败")
    project = pm.GetCurrentProject()
    try:
        project.SetSetting("timelineFrameRate", str(E.FPS))
        project.SetSetting("timelineResolutionWidth", str(E.W))
        project.SetSetting("timelineResolutionHeight", str(E.H))
    except Exception as e:
        print(f"  (项目设置: {e})")

    # ---- 导入素材 ----
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    def folder(name, files, strict=True):
        """导入到子文件夹。strict=False 时**不要求本地可 stat**。

        外置盘被 TCC 挡住时，本进程的 os.path.exists 对盘上文件返回 False，
        但**达芬奇自己能读**（已实测：A0776/A0777/B1055 时长与交接文档完全一致）。
        所以原素材走 strict=False，让 Resolve 去读。
        """
        sub = next((f for f in (root.GetSubFolderList() or []) if f.GetName() == name), None)
        if sub is None:
            sub = mp.AddSubFolder(root, name)
        mp.SetCurrentFolder(sub)
        have = {i.GetClipProperty("Clip Name") for i in (sub.GetClipList() or [])}
        todo = [f for f in files
                if os.path.basename(f) not in have and (os.path.exists(f) or not strict)]
        if todo:
            mp.ImportMedia(todo)
        return {i.GetClipProperty("Clip Name"): i for i in (sub.GetClipList() or [])}

    shots = sorted({x["clip"] for x in acts if x["kind"] == "shot"})
    shot_clips = folder(f"素材{base}", [os.path.join(src_dir, os.path.basename(p))
                                        for p in shots]
                        if src_dir else [], strict=False)
    ov = folder("叠加层", [os.path.join(a.assets, x) for x in need])
    tx = folder("文字载体", [os.path.join(a.assets, "blank_alpha.mov")])
    vo = folder("配音", [os.path.join(E.AUTO, "voice_sent", x["wav"])
                         for x in acts if x["kind"] == "voice"])

    def dur_of(m, name):
        v = m.get(name)
        if not v:
            return 0.0
        try:
            fps_v = float(v.GetClipProperty("FPS") or E.FPS)
        except Exception:
            fps_v = E.FPS
        return E.parse_timecode(v.GetClipProperty("Duration"), fps_v)

    print(f"  素材: 镜头 {len(shot_clips)}  叠加层 {len(ov)}  "
          f"文字载体 {len(tx)}  配音 {len(vo)}")
    for k in sorted(shot_clips):
        print(f"    {k:<12} {dur_of(shot_clips, k):6.2f}s")

    # ---- 建轨 ----
    tlobj = mp.CreateEmptyTimeline(proj)
    project.SetCurrentTimeline(tlobj)
    nv = len([t for t, k, _ in E.TRACK_LAYOUT if k == "video"])
    na = len([t for t, k, _ in E.TRACK_LAYOUT if k == "audio"])
    for _ in range(nv):
        tlobj.AddTrack("video")
    for _ in range(na):
        tlobj.AddTrack("audio")
    for t, kind, name in E.TRACK_LAYOUT:
        idx = int(t[1:])
        try:
            tlobj.SetTrackName(kind, idx, name)
        except Exception:
            pass
    print(f"  轨道: video={tlobj.GetTrackCount('video')} "
          f"audio={tlobj.GetTrackCount('audio')}")

    log_lines = []

    def log(m):
        print(m)
        log_lines.append(m)

    blank_item = next(iter(tx.values()), None)

    # ---- V1 镜头 ----
    print("\n-- V1 镜头 --")
    v1 = []
    for x in [y for y in acts if y["track"] == "V1"]:
        it = shot_clips.get(os.path.basename(x["clip"]))
        if not it:
            log(f"    ❌ 缺素材 {x['clip']}")
            continue
        # 实测（/tmp/fps_test.py）：AppendToTimeline 的 startFrame/endFrame 是
        # **源素材帧**单位。本项目源是 50p、时间线 30fps：
        # 请求 endFrame=60 -> 时间线 GetDuration=36 帧 = 1.200s = 60/50。
        # 若按时间线 30fps 算，每一镜都会裁错 50/30 倍。
        try:
            src_fps = float(it.GetClipProperty("FPS") or E.FPS)
        except Exception:
            src_fps = E.FPS
        ss = int(round(x["src_start"] * src_fps))
        ee = ss + int(round(x["dur"] * src_fps))
        rf = int(round(x["record"] * E.FPS))
        got = mp.AppendToTimeline([{"mediaPoolItem": it, "startFrame": ss,
                                    "endFrame": ee, "trackIndex": 1,
                                    "mediaType": 1, "recordFrame": rf}])
        if got:
            v1.append(got[0])
            tl_frames = int(round((ee - ss) / src_fps * E.FPS))
            log(f"    ✅ {x['label']:<16} 时间线 f{rf:>5}-{rf+tl_frames:<5} "
                f"({tl_frames}f) {x['clip']} (源 {src_fps:.0f}fps {ss}~{ee})")
        else:
            log(f"    ❌ {x['label']} 放置失败")

    # ---- V2 / V9 叠加层 ----
    for tk, title in (("V2", "logo"), ("V9", "黑场(乘法)")):
        print(f"\n-- {tk} {title} --")
        for x in [y for y in acts if y["track"] == tk]:
            it = ov.get(x["asset"])
            if not it:
                log(f"    ❌ 缺叠加素材 {x['asset']}")
                continue
            rf = int(round(x["record"] * E.FPS))
            ef = max(1, int(round(x["dur"] * E.FPS)))
            got = mp.AppendToTimeline([{"mediaPoolItem": it, "startFrame": 0,
                                        "endFrame": ef,
                                        "trackIndex": int(tk[1:]),
                                        "mediaType": 1, "recordFrame": rf}])
            if got:
                if x.get("multiply"):
                    try:
                        cm = getattr(r, "COMPOSITE_MULTIPLY", 4)
                        got[0].SetProperty("CompositeMode", cm)
                    except Exception as e:
                        log(f"    ⚠️ 乘法合成设置失败: {str(e)[:60]}")
                log(f"    ✅ {x['label']:<16} f{rf:>5}-{rf+ef:<5} {x['asset']}")
            else:
                log(f"    ❌ {x['label']} 放置失败")

    # ---- V3..V8 文字 ----
    for tk in ("V3", "V4", "V5", "V6", "V7", "V8"):
        items = [y for y in acts if y["track"] == tk]
        if not items:
            continue
        print(f"\n-- {tk} 文字 --")
        if blank_item is None:
            log("    ❌ 无文字载体 blank_alpha.mov")
            continue
        for x in items:
            make_text_item(tlobj, mp, blank_item, x, log)

    # ---- A1 配音 ----
    print("\n-- A1 配音 --")
    for x in [y for y in acts if y["track"] == "A1"]:
        it = vo.get(x["wav"])
        if not it:
            log(f"    ❌ 缺配音 {x['wav']}")
            continue
        rf = int(round(x["record"] * E.FPS))
        ef = int(round(x["dur"] * E.FPS))
        got = mp.AppendToTimeline([{"mediaPoolItem": it, "startFrame": 0,
                                    "endFrame": ef, "trackIndex": 1,
                                    "mediaType": 2, "recordFrame": rf}])
        log(f"    {'✅' if got else '❌'} {x['label']:<12} f{rf:>5}-{rf+ef}")

    # ---- A2 BGM ----
    print("\n-- A2 BGM --")
    for x in [y for y in acts if y["track"] == "A2"]:
        it = ov.get(x["asset"])
        if not it:
            log(f"    ❌ 缺 BGM {x['asset']}")
            continue
        ef = int(round(x["dur"] * E.FPS))
        got = mp.AppendToTimeline([{"mediaPoolItem": it, "startFrame": 0,
                                    "endFrame": ef, "trackIndex": 2,
                                    "mediaType": 2, "recordFrame": 0}])
        log(f"    {'✅' if got else '❌'} {x['label']} f0-{ef}")

    pm.SaveProject()
    print(f"\n  ✅ 已保存 {proj}")
    try:
        with open(os.path.join(E.AUTO, "out", "last_project.txt"), "w",
                  encoding="utf-8") as _f:
            _f.write(proj)
    except Exception:
        pass

    # ---- 交付前自检 ----
    print("\n=== 交付前自检 ===")
    chk = []
    vcnt, acnt = tlobj.GetTrackCount("video"), tlobj.GetTrackCount("audio")
    chk.append(("视频轨数", vcnt >= nv, f"{vcnt}/{nv}"))
    chk.append(("音频轨数", acnt >= na, f"{acnt}/{na}"))
    a1 = tlobj.GetItemListInTrack("audio", 1) or []
    a2 = tlobj.GetItemListInTrack("audio", 2) or []
    nseg = len(tl["segments"])
    chk.append(("配音段数", len(a1) == nseg, f"{len(a1)}/{nseg}"))
    chk.append(("BGM 段数", len(a2) >= 1, len(a2)))
    chk.append(("BGM 起于第0帧", bool(a2) and a2[0].GetStart() == 0,
                a2[0].GetStart() if a2 else "-"))
    if a1:
        exp = int(round(tl["segments"][0]["t0"] * E.FPS))
        chk.append(("配音起于档案卡后", abs(a1[0].GetStart() - exp) <= 1,
                    f"{a1[0].GetStart()} vs {exp}"))
    # 镜头时长 >= 配音时长（逐句对齐，不只是数段数）
    v1i = tlobj.GetItemListInTrack("video", 1) or []
    chk.append(("V1 段数", len(v1i) >= nseg, f"{len(v1i)}/{nseg}"))
    short = 0
    for x in [y for y in acts if y["track"] == "V1" and y["kind"] == "shot"]:
        rf = int(round(x["record"] * E.FPS))
        hit = next((it for it in v1i if it.GetStart() == rf), None)
        if hit is None:
            short += 1
        elif (hit.GetEnd() - hit.GetStart()) < x["frames"]:
            short += 1
    chk.append(("镜头落点与时长", short == 0, f"异常 {short} 段"))
    # 每句配音必须有对应档位，且不被裁短
    a1_by_start = {it.GetStart(): it for it in a1}
    badv = 0
    for x in [y for y in acts if y["track"] == "A1"]:
        rf = int(round(x["record"] * E.FPS))
        it = a1_by_start.get(rf)
        if it is None or (it.GetEnd() - it.GetStart()) < x["frames"]:
            badv += 1
    chk.append(("配音完整未被裁短", badv == 0, f"异常 {badv} 段"))
    # 段间不应有重叠（V1 连续且不重叠）
    ov = 0
    v1s = sorted(v1i, key=lambda it: it.GetStart())
    for p, q in zip(v1s, v1s[1:]):
        if q.GetStart() < p.GetEnd():
            ov += 1
    chk.append(("V1 无重叠", ov == 0, f"重叠 {ov} 处"))
    # 文字轨
    for tk, idx in (("V3", 3), ("V4", 4), ("V5", 5), ("V6", 6), ("V7", 7), ("V8", 8)):
        want = len([y for y in acts if y["track"] == tk])
        if want:
            g = tlobj.GetItemListInTrack("video", idx) or []
            chk.append((f"{tk} 文字段数", len(g) == want, f"{len(g)}/{want}"))
    # 叠加层
    v2 = tlobj.GetItemListInTrack("video", 2) or []
    v9 = tlobj.GetItemListInTrack("video", 9) or []
    chk.append(("logo 段数", len(v2) == 2, len(v2)))
    want9 = len([y for y in acts if y["track"] == "V9"])
    chk.append(("黑场段数", len(v9) == want9, f"{len(v9)}/{want9}"))
    for name, good, val in chk:
        print(f"  {'✅' if good else '❌'} {name}: {val}")

    rep = os.path.join(os.path.expanduser("~"), f"build_{proj}.log")
    with open(rep, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    print(f"\n构建日志: {rep}")
    return 0 if all(g for _, g, _ in chk) else 2


if __name__ == "__main__":
    sys.exit(main())
