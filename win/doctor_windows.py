#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor_windows.py —— Windows 10 环境体检（跑 run_chapter 之前先跑这个）

检查 8 项，每项 ✅/❌ + 修法。**不碰达芬奇工程**（只在最后试着连一下 API）。

用法（在 2-人参剪辑 目录下）
    .venv-win\\Scripts\\python.exe win\\doctor_windows.py
    python win\\doctor_windows.py --no-resolve   # 不试连达芬奇
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IS_WIN = os.name == "nt"
results = []


def say(m=""):
    print(m)


def item(name, ok, detail="", fix=""):
    results.append((name, ok))
    say(f"  {'✅' if ok else '❌'} {name}")
    if detail:
        say(f"       {detail}")
    if not ok and fix:
        say(f"       → {fix}")


def check_python():
    v = sys.version_info
    ok = v >= (3, 8)
    item("Python 版本", ok, f"{sys.version.split()[0]}  ({sys.executable})",
         "装 Python 3.10~3.12（64 位）：https://www.python.org/downloads/windows/")


def check_libs():
    miss = []
    for mod, pipname in (("PIL", "pillow"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except Exception:
            miss.append(pipname)
    item("Python 库 pillow / numpy", not miss,
         "缺: " + ", ".join(miss) if miss else "已装",
         "pip install -r requirements.txt")


def check_ffmpeg():
    sys.path.insert(0, ROOT)
    try:
        import editlib as E
        p = E.FFMPEG
        ok = bool(p) and os.path.exists(p)
        item("ffmpeg", ok, p or "没找到",
             "winget install Gyan.FFmpeg   （然后重开一个终端）")
    except Exception as e:
        item("ffmpeg", False, f"import editlib 失败: {e}", "先跑 win\\patch_for_windows.py")


def check_resolve_install():
    if not IS_WIN:
        item("达芬奇安装", True, "非 Windows，跳过")
        return
    cands = [
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
        r"C:\Program Files (x86)\Blackmagic Design\DaVinci Resolve\Resolve.exe",
    ]
    found = next((c for c in cands if os.path.exists(c)), None)
    api = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                       "Blackmagic Design", "DaVinci Resolve", "Support",
                       "Developer", "Scripting")
    lib = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
    ok = bool(found) and os.path.isdir(api) and os.path.exists(lib)
    item("达芬奇 + 脚本 API", ok,
         f"程序: {found or '未找到'} | API: {'有' if os.path.isdir(api) else '无'} | "
         f"fusionscript.dll: {'有' if os.path.exists(lib) else '无'}",
         "装 DaVinci Resolve（免费版即可，含脚本 API），装完重启一次电脑")


def check_resolve_live():
    try:
        sys.path.insert(0, ROOT)
        import editlib  # noqa: F401
        api = os.environ.get("RESOLVE_SCRIPT_API") or os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
            "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting")
        os.environ["RESOLVE_SCRIPT_API"] = api
        os.environ.setdefault("RESOLVE_SCRIPT_LIB",
                              r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
                              if IS_WIN else
                              "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
        sys.path.insert(0, os.path.join(api, "Modules"))
        import DaVinciResolveScript as dvr
        r = dvr.scriptapp("Resolve")
        if not r:
            item("连达芬奇", False, "API 起来了但拿不到 Resolve 对象",
                 "达芬奇要先**手动启动**（别用脚本拉起来，会降级运行）")
            return
        item("连达芬奇", True, f"{r.GetProductName()} {r.GetVersionString()} | 当前页 {r.GetCurrentPage()}")
        try:
            r.OpenPage("edit")     # 复位界面
        except Exception:
            pass
    except Exception as e:
        item("连达芬奇", False, f"{type(e).__name__}: {e}",
             "1) 先手动启动达芬奇  2) Preferences→System→General 里允许脚本（选 Local）")


def check_fonts():
    try:
        sys.path.insert(0, ROOT)
        import fontsel as F
        zh = F.resolve("SourceHanSerifCN-Medium", "Source Han Serif CN")
        zh_b = F.resolve("SourceHanSerifCN-Heavy", "Source Han Serif CN")
        en = F.resolve("Didot", os.environ.get("JIFANGTANG_FONT_EN_FAMILY", "Didot"))
        m_zh = F.measure(zh, "人参", 49.1)
        m_en = F.measure(en, "ginseng", 49.1)
        ok = bool(zh and en and m_zh and m_en)
        item("字体（族名 + 测宽）", ok,
             f"中文族名={zh!r} 英文族名={en!r} 测宽 中={m_zh} 英={m_en}",
             "中文: 装 fonts/SourceHanSerifCN-*.otf；"
             "英文: Windows 没 Didot，设 JIFANGTANG_FONT_EN / _FAMILY（详见 win\\说明-Windows适配.md）")
    except Exception as e:
        item("字体", False, f"{type(e).__name__}: {e}", "先跑 win\\patch_for_windows.py")


def check_layout():
    """真正跑一次排版预检，证明"不开达芬奇也能确认排版"。"""
    tl = os.path.join(ROOT, "out", "timeline_ch01.json")
    if not os.path.exists(tl):
        item("排版预检", False, f"缺 {tl}", "先跑 batch_timelines.py --chapter 01")
        return
    r = subprocess.run([sys.executable, os.path.join(ROOT, "check_text_layout.py"),
                        "--timeline", tl], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    item("排版预检（check_text_layout）", ok,
         (r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[-200:],
         "看上面输出，中文超宽=漏拆句")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-resolve", action="store_true")
    a = ap.parse_args()
    say("=" * 70)
    say("寄方堂人参视频 · Windows 环境体检")
    say("=" * 70)
    check_python()
    check_libs()
    check_ffmpeg()
    check_resolve_install()
    check_fonts()
    check_layout()
    if not a.no_resolve:
        check_resolve_live()
    say("\n" + "=" * 70)
    n_ok = sum(1 for _, v in results if v)
    say(f"结论: {n_ok}/{len(results)} 项通过")
    if n_ok == len(results):
        say("🎉 可以开始建草稿了：")
        say(r"   一次成片.bat 01  D:\素材\_remux\20260626～39")
    say("=" * 70)
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
