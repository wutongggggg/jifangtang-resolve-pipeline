#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor.py —— 30 秒体检：判断达芬奇是不是"降级运行"

背景
----
这轮实测发现一组症状，怀疑与本机达芬奇**是被 DSH 的 node 进程拉起来的**有关
（而不是你从访达/程序坞正常启动）：

  · `AppendToTimeline` 放进去的片段**不持久化**（SaveProject 返回 True，切换项目就空）
  · 渲染与 `ExportCurrentFrameAsStill` **全是纯黑**（连本地已知良好的素材也一样）
  · 一批 README 里写明的方法返回 `None`（GetName / GetRenderSettings /
    GetCurrentStillAlbum / SetKeyframe / GetSpline / CloseProject→False）

但你**之前**建的那几个工程（`20260626～39v01` 等，14:56–15:42 建的）
片段是**持久化**的 —— 所以正常启动的达芬奇应该是好的。

用法（达芬奇已启动）
    /opt/homebrew/bin/python3 doctor.py

它会做 3 个决定性检查并给出结论。**不会改动你的任何工程**（只在临时工程里做）。
"""
import os
import sys
import time

SCRIPT_API = os.environ.get("RESOLVE_SCRIPT_API") or (os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting") if os.name == "nt" else "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"); _RESOLVE_LIB = os.environ.get("RESOLVE_SCRIPT_LIB") or (r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll" if os.name == "nt" else "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
os.environ["RESOLVE_SCRIPT_API"] = SCRIPT_API
os.environ["RESOLVE_SCRIPT_LIB"] = _RESOLVE_LIB
sys.path.insert(0, os.path.join(SCRIPT_API, "Modules"))

PRE = "_DSH_DOCTOR"
LOCAL = os.environ.get("JIFANGTANG_DOCTOR_CLIP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "blank_alpha.mov")
VOL_DIR = "/Volumes/备份盘/20260626/20260626～39"
TMP = "/tmp/dsh_doctor"
results = {}


def say(m=""):
    print(m)


def cleanup(pm):
    runner = f"{PRE}_runner"
    if runner not in (pm.GetProjectListInCurrentFolder() or []):
        pm.CreateProject(runner)
    else:
        pm.LoadProject(runner)
    time.sleep(0.8)
    for n in [x for x in (pm.GetProjectListInCurrentFolder() or [])
              if x.startswith(PRE) and x != runner]:
        for _ in range(3):
            if pm.DeleteProject(n):
                break
            time.sleep(0.6)


def main():
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if not r:
        sys.exit("连不上达芬奇（先启动它）")
    pm = r.GetProjectManager()
    say(f"达芬奇: {r.GetProductName()} {r.GetVersionString()}")
    say(f"当前页面: {r.GetCurrentPage()}")
    say(f"你的工程: {pm.GetProjectListInCurrentFolder()}")

    os.makedirs(TMP, exist_ok=True)
    for f in os.listdir(TMP):
        os.remove(os.path.join(TMP, f))
    cleanup(pm)

    # ---------- 检查 1：片段是否持久化（用本地素材，排除 TCC）----------
    say("\n[1/3] 片段持久化（本地素材）")
    nm = f"{PRE}_p_{int(time.time())}"
    pm.CreateProject(nm)
    p = pm.GetCurrentProject()
    try:
        p.SetSetting("timelineFrameRate", "30")
    except Exception:
        pass
    mp = p.GetMediaPool()
    mp.SetCurrentFolder(mp.AddSubFolder(mp.GetRootFolder(), "d"))
    clips = mp.ImportMedia([LOCAL])
    tl = mp.CreateEmptyTimeline("t")
    p.SetCurrentTimeline(tl)
    mp.AppendToTimeline([{"mediaPoolItem": clips[0], "startFrame": 0, "endFrame": 90,
                          "trackIndex": 1, "mediaType": 1, "recordFrame": 0}])
    n_before = len(tl.GetItemListInTrack("video", 1) or [])
    end_before = tl.GetEndFrame()
    pm.SaveProject()
    time.sleep(2.5)
    pm.LoadProject(f"{PRE}_runner")
    time.sleep(1.5)
    pm.LoadProject(nm)
    time.sleep(1.5)
    p2 = pm.GetCurrentProject()
    t2 = p2.GetCurrentTimeline()
    n_after = len(t2.GetItemListInTrack("video", 1) or []) if t2 else 0
    ok1 = (n_before == 1 and n_after == 1)
    results["持久化"] = ok1
    say(f"   内存 {n_before} 段 (GetEndFrame={end_before}) -> 切走切回 {n_after} 段  "
        f"{'✅ 正常' if ok1 else '❌ 丢失'}")

    # ---------- 检查 2：能否导出非黑画面 ----------
    say("\n[2/3] 画面输出（本地已知良好素材，应为非黑）")
    out = os.path.join(TMP, "frame.png")
    ok2 = False
    try:
        p2.SetCurrentTimeline(t2)
        for tc in ("00:00:00:00", "00:00:01:00", "00:00:02:00"):
            try:
                t2.SetCurrentTimecode(tc)
            except Exception:
                pass
            time.sleep(0.6)
            if p2.ExportCurrentFrameAsStill(out) and os.path.exists(out):
                break
        if os.path.exists(out) and os.path.getsize(out) > 0:
            # 用自带 ffmpeg 缩到 1x1 灰度取平均亮度 —— 不依赖 PIL，
            # 也比"看文件大小"可靠（第一版用大小判断，把纯黑误判成正常）。
            FF = (__import__("shutil").which("ffmpeg") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
            import subprocess
            rr = subprocess.run([FF, "-v", "error", "-i", out, "-vf", "scale=1:1",
                                 "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                capture_output=True)
            lum = rr.stdout[0] if rr.stdout else -1
            ok2 = lum > 8
            say(f"   {os.path.getsize(out)/1024:.0f} KB  平均亮度={lum}/255  "
                f"{'✅ 有画面' if ok2 else '❌ 纯黑'}")
        else:
            say("   ❌ 没导出文件")
    except Exception as e:
        say(f"   ❌ {type(e).__name__}: {str(e)[:80]}")
    results["画面输出"] = ok2

    # ---------- 检查 3：FCPXML 能否用外置盘素材 ----------
    say("\n[3/3] FCPXML 导入 + 外置盘素材")
    ok3 = False
    try:
        from urllib.parse import quote
        if os.path.exists(VOL_DIR):
            vol = os.path.join(VOL_DIR, "A0776.MP4")
        else:
            vol = None
        if vol is None:
            # 我们的进程读不到外置盘目录也没关系，直接拼路径
            vol = VOL_DIR + "/A0776.MP4"
        xml = os.path.join(TMP, "t.fcpxml")
        with open(xml, "w", encoding="utf-8") as f:
            f.write(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9"><resources>
<format id="r1" name="FFVideoFormat1080x1920p30" frameDuration="1/30s" width="1080" height="1920"/>
<asset id="r2" name="a" start="0s" duration="600/30s" hasVideo="1" format="r1">
<media-rep kind="original-media" src="file://{quote(vol)}"/></asset></resources>
<library><event name="dsh"><project name="d">
<sequence format="r1" duration="90/30s" tcStart="0s" tcFormat="NDF">
<spine><asset-clip ref="r2" name="c1" offset="0s" duration="90/30s" start="0s"/></spine>
</sequence></project></event></library></fcpxml>
''')
        nm3 = f"{PRE}_x_{int(time.time())}"
        pm.CreateProject(nm3)
        p3 = pm.GetCurrentProject()
        p3.SetSetting("timelineFrameRate", "30")
        got = p3.GetMediaPool().ImportTimelineFromFile(xml, {"timelineName": "t"})
        time.sleep(2)
        ok3 = bool(got) and p3.GetTimelineCount() > 0
        say(f"   外置盘素材 FCPXML -> {'✅ 导入成功' if ok3 else '❌ 导入失败'}")
    except Exception as e:
        say(f"   ❌ {type(e).__name__}: {str(e)[:80]}")
    results["外置盘FCPXML"] = ok3

    cleanup(pm)
    say(f"\n清理后工程: {pm.GetProjectListInCurrentFolder()}")

    # ---------- 结论 ----------
    say("\n" + "=" * 66)
    say("体检结论")
    say("=" * 66)
    for k, v in results.items():
        say(f"  {'✅' if v else '❌'} {k}")
    n_ok = sum(1 for v in results.values() if v)
    if n_ok == 3:
        say("\n  🎉 全部正常 —— 可以构建真实草稿了：")
        say("     python3 run_chapter.py --chapter 01 \\")
        say("         --media-dir /Volumes/备份盘/20260626/20260626～39")
    elif n_ok == 0:
        say("\n  ❌ 三项全废 —— 达芬奇处于**降级运行**状态。")
        say("     请**完全退出达芬奇**，然后**你亲自从「访达/程序坞」双击启动**")
        say("     （不要用脚本/命令行启动），再重跑本体检。")
        say("     本机达芬奇之前正常时建的工程（20260626～39v01 等）片段是持久的，")
        say("     说明正常启动的达芬奇没问题。")
    else:
        say(f"\n  ⚠️ 通过 {n_ok}/3。把上面完整输出发我，我据此调整方案。")
    return 0 if n_ok == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
