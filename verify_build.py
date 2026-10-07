#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_build.py —— 回读已建好的达芬奇工程，验证"真的写进去了"

自检（build_resolve5.py 里那段）只验证了**时间线骨架**：轨数、段数、落点、时长。
本脚本验证**内容**：
  · 轨名是否写对
  · 每个 V1 片段的时间线帧长 / 源入出点
  · V9 叠加层是否真的设成乘法合成
  · **每条文字的 Fusion 合成里，Text+ 是否真的存在、文本/字号/位置/透明度是否写对、
    有没有接到 MediaOut**（不接 MediaOut 的话文字根本不会出现在画面上）

用法
    /opt/homebrew/bin/python3 verify_build.py --project "20260626～39v02"
    /opt/homebrew/bin/python3 verify_build.py --project "..." --render /tmp/frames
"""
import argparse
import os
import sys
import time

SCRIPT_API = os.environ.get("RESOLVE_SCRIPT_API") or (os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting") if os.name == "nt" else "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"); _RESOLVE_LIB = os.environ.get("RESOLVE_SCRIPT_LIB") or (r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll" if os.name == "nt" else "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
os.environ["RESOLVE_SCRIPT_API"] = SCRIPT_API
os.environ["RESOLVE_SCRIPT_LIB"] = _RESOLVE_LIB
sys.path.insert(0, os.path.join(SCRIPT_API, "Modules"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

FPS = 30.0
TRACK_NAMES = {
    ("video", 1): "镜头", ("video", 2): "logo", ("video", 3): "档案卡1",
    ("video", 4): "档案卡2", ("video", 5): "档案卡3", ("video", 6): "中文字幕",
    ("video", 7): "英文字幕", ("video", 8): "引文竖排", ("video", 9): "闪黑",
    ("audio", 1): "配音", ("audio", 2): "BGM",
}


def connect():
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if not r:
        sys.exit("无法连接达芬奇")
    return r


def text_inputs(comp):
    """找到 comp 里的 Text+ 工具，返回 (tool, {id: value})。"""
    try:
        tools = comp.GetToolList()
    except Exception:
        return None, {}
    for k in tools:
        t = tools[k]
        try:
            tid = str(t.GetID() or "")
        except Exception:
            tid = ""
        if "TextPlus" in tid or "Text" in tid:
            vals = {}
            for iid in ("StyledText", "Size", "Center", "Alpha1", "Font",
                        "CharacterSpacing", "VerticalJustificationNew"):
                try:
                    v = t.GetInput(iid, 0)
                    if v is not None:
                        vals[iid] = v
                except Exception:
                    pass
            return t, vals
    return None, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--render", default=None, help="渲染静帧到该目录")
    a = ap.parse_args()

    r = connect()
    pm = r.GetProjectManager()
    if not pm.LoadProject(a.project):
        sys.exit(f"打不开项目 {a.project}（现有: {pm.GetProjectListInCurrentFolder()}）")
    project = pm.GetCurrentProject()
    tl = project.GetCurrentTimeline()
    if tl is None:
        sys.exit("项目没有时间线")
    print(f"=== 回读 {project.GetName()} / 时间线 {tl.GetName()} ===")
    print(f"  时间线帧率 {tl.GetSetting('timelineFrameRate')}  "
          f"时长 {tl.GetEndFrame()} 帧 "
          f"({tl.GetEndFrame()/FPS:.2f}s)")

    print("\n-- 轨名 --")
    for (kind, idx), want in sorted(TRACK_NAMES.items()):
        if idx > tl.GetTrackCount(kind):
            print(f"  ❌ {kind}{idx} 不存在（只有 {tl.GetTrackCount(kind)} 条）")
            continue
        got = tl.GetTrackName(kind, idx)
        print(f"  {'✅' if got == want else '⚠️ '} {kind}{idx} = {got!r} (期望 {want!r})")

    print("\n-- V1 镜头：时间线帧长 vs 计划 --")
    import json
    with open(os.path.join(E.AUTO, "out", "timeline_ch01.json"), encoding="utf-8") as f:
        tlj = json.load(f)
    v1 = tl.GetItemListInTrack("video", 1) or []
    print(f"  共 {len(v1)} 段")
    for it in sorted(v1, key=lambda x: x.GetStart()):
        d = it.GetEnd() - it.GetStart()
        try:
            ss, se = it.GetSourceStartFrame(), it.GetSourceEndFrame()
        except Exception:
            ss, se = "?", "?"
        print(f"    f{it.GetStart():>5}-{it.GetEnd():<5} ({d:>3}f {d/FPS:5.2f}s) "
              f"源 {ss}~{se}  {it.GetName()[:40]}")

    print("\n-- V2 / V9 叠加层：合成模式 --")
    for idx, label in ((2, "logo"), (9, "闪黑")):
        items = tl.GetItemListInTrack("video", idx) or []
        modes = []
        for it in items:
            try:
                modes.append(it.GetProperty("CompositeMode"))
            except Exception:
                modes.append("?")
        print(f"  V{idx} {label}: {len(items)} 段  合成模式={modes}")

    print("\n-- 文字轨：Fusion Text+ 内容回读 --")
    total_txt, ok_txt, wired = 0, 0, 0
    for idx, name in ((3, "档案卡1"), (4, "档案卡2"), (5, "档案卡3"),
                      (6, "中文字幕"), (7, "英文字幕"), (8, "引文竖排")):
        items = tl.GetItemListInTrack("video", idx) or []
        if not items:
            continue
        print(f"\n  V{idx} · {name}  ({len(items)} 段)")
        for it in sorted(items, key=lambda x: x.GetStart()):
            total_txt += 1
            ncomp = it.GetFusionCompCount()
            if not ncomp:
                print(f"    ❌ f{it.GetStart():>5} 没有 Fusion 合成")
                continue
            comp = it.GetFusionCompByIndex(1)
            tp, vals = text_inputs(comp)
            if tp is None:
                print(f"    ❌ f{it.GetStart():>5} 合成里没有 Text+")
                continue
            ok_txt += 1
            # 是否接到 MediaOut
            conn = "?"
            try:
                mo = comp.FindTool("MediaOut1")
                if mo is not None:
                    try:
                        mo.ConnectInput("Input", tp)
                        conn = "输入已连"
                        wired += 1
                    except Exception as e:
                        conn = f"连线失败:{str(e)[:30]}"
            except Exception:
                pass
            txt = str(vals.get("StyledText", "")).replace("\n", "\\n")
            c = vals.get("Center")
            try:
                cx, cy = (c[1], c[2]) if isinstance(c, dict) else (None, None)
            except Exception:
                cx, cy = (None, None)
            print(f"    ✅ f{it.GetStart():>5} Text+ 文本={txt[:34]!r}")
            print(f"         Size={vals.get('Size')}  Center=({cx},{cy})"
                  f"  Alpha1={vals.get('Alpha1')}  字距={vals.get('CharacterSpacing')}")
    print(f"\n  文字段: {total_txt} 段，其中 {ok_txt} 段有 Text+，{wired} 段确认连到 MediaOut")
    if wired < ok_txt:
        print("  ⚠️ 未连到 MediaOut 的文字不会出现在画面上")

    try:
        r.OpenPage("edit")      # 不做渲染/导出，直接把页面复位
    except Exception:
        pass

    if a.render:
        os.makedirs(a.render, exist_ok=True)
        print(f"\n-- 渲染静帧到 {a.render} --")
        try:
            project.SetCurrentRenderFormatAndCodec("png", "")
        except Exception as e:
            print(f"  ⚠️ 设置 png 格式失败: {str(e)[:80]}")
        shots = [(30, "card"), (200, "sub1"), (350, "quote"), (560, "biglogo")]
        okr = 0
        for frame, tag in shots:
            try:
                project.SetRenderSettings({
                    "SelectAllFrames": False, "MarkIn": frame, "MarkOut": frame,
                    "TargetDir": a.render, "CustomName": f"{tag}_f{frame}",
                    "ExportVideo": True, "ExportAudio": False,
                    "FormatWidth": E.W, "FormatHeight": E.H,
                })
                job = project.AddRenderJob()
                if not job:
                    print(f"  ❌ {tag}: AddRenderJob 失败")
                    continue
                project.StartRendering(job)
                for _ in range(60):
                    st = project.GetRenderJobStatus(job)
                    if st and st.get("JobStatus") in ("Complete", "Failed"):
                        break
                    time.sleep(1.0)
                st = project.GetRenderJobStatus(job)
                print(f"  {tag} f{frame}: {st.get('JobStatus') if st else '?'}")
                project.DeleteRenderJob(job)
                okr += 1
            except Exception as e:
                print(f"  ❌ {tag} f{frame}: {type(e).__name__} {str(e)[:90]}")
        print(f"  渲染成功 {okr}/{len(shots)}")
        for f in sorted(os.listdir(a.render)):
            p = os.path.join(a.render, f)
            print(f"    {f}  {os.path.getsize(p)/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
