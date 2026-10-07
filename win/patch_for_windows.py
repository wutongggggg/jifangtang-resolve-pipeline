#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_for_windows.py —— 把这份（从 macOS 拷来的）autoedit 脚本改成 win10 可跑

做了什么（全部幂等，跑第二遍不会改坏）
------------------------------------
 1. `editlib.py`
      AUTO   : 写死的 `/Users/geophyto/Movies/JianyingPro/autoedit`
               -> 脚本自己所在目录（换到任何盘/任何用户名都对）
      FFMPEG : `AUTO/bin/ffmpeg`（Mac 二进制，Windows 用不了）
               -> 先找 PATH 上的 ffmpeg/ffmpeg.exe，再退回 bin/
 2. 所有脚本里的达芬奇脚本 API 路径
      mac: /Library/Application Support/...  + fusionscript.so
      win: %PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting
           + C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\fusionscript.dll
      （写成运行时判断 os.name，所以同一份代码在 mac 上也照样能跑）
 3. 其它脚本里写死的 `.../autoedit/bin/ffmpeg` -> 同上按平台找
 4. `fontsel.py` 换成跨平台版（mac 用 CoreText，Windows 用 PIL），
      原文件备份为 `fontsel_mac.py`
 5. `run_chapter.py` 里 subprocess 输出加 UTF-8 解码，避免中文路径在
      cp936 控制台下 UnicodeDecodeError

用法（在 2-人参剪辑 目录下）
    python patch_for_windows.py            # 打补丁
    python patch_for_windows.py --dry-run  # 只看会改什么
"""
import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 2-人参剪辑/

MAC_AUTO = '"/Users/geophyto/Movies/JianyingPro/autoedit"'
MAC_FFMPEG = '"/Users/geophyto/Movies/JianyingPro/autoedit/bin/ffmpeg"'
MAC_SCRIPT_API = '"/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"'

# --- 跨平台常量定义（写进用到 SCRIPT_API 的脚本里） -------------------------
SCRIPT_API_DEF = (
    'SCRIPT_API = os.environ.get("RESOLVE_SCRIPT_API") or ('
    'os.path.join(os.environ.get("PROGRAMDATA", r"C:\\ProgramData"), '
    '"Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting") '
    'if os.name == "nt" else '
    '"/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting")'
    '; _RESOLVE_LIB = os.environ.get("RESOLVE_SCRIPT_LIB") or ('
    'r"C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\fusionscript.dll" '
    'if os.name == "nt" else '
    '"/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")'
)

FFMPEG_EXPR = (
    '(__import__("shutil").which("ffmpeg") or '
    'os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", '
    '"ffmpeg.exe" if os.name == "nt" else "ffmpeg"))'
)

EDITLIB_FFMPEG_BLOCK = '''def _find_bin(name):
    """找 ffmpeg/ffprobe：先看 bin/（本地随包），再看 PATH。"""
    import shutil as _sh
    exe = name + (".exe" if os.name == "nt" else "")
    local = os.path.join(AUTO, "bin", exe)
    if os.path.exists(local):
        return local
    return _sh.which(name) or _sh.which(exe) or local


FFMPEG = _find_bin("ffmpeg")'''

report = []


def note(f, what):
    report.append((os.path.relpath(f, ROOT), what))


def read(p):
    with open(p, encoding="utf-8") as fp:
        return fp.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(s)


def sub_once(text, old, new, path, what):
    if old in text:
        return text.replace(old, new), True
    return text, False


def patch_editlib(dry):
    p = os.path.join(ROOT, "editlib.py")
    if not os.path.exists(p):
        return
    s = read(p)
    orig = s
    s, hit = sub_once(s, "AUTO = " + MAC_AUTO,
                      "AUTO = os.path.dirname(os.path.abspath(__file__))", p,
                      "AUTO 改为脚本自身目录")
    if hit:
        note(p, "AUTO 改为脚本自身目录")
    if 'FFMPEG = os.path.join(AUTO, "bin", "ffmpeg")' in s:
        s = s.replace('FFMPEG = os.path.join(AUTO, "bin", "ffmpeg")',
                      EDITLIB_FFMPEG_BLOCK)
        note(p, "FFMPEG 改为按平台查找（PATH -> bin/）")
    if s != orig and not dry:
        write(p, s)


def patch_resolve_env(dry):
    """所有脚本：SCRIPT_API / RESOLVE_SCRIPT_LIB 改成跨平台。"""
    lib_re = re.compile(
        r'(?P<ind>[ \t]*)os\.environ\["RESOLVE_SCRIPT_LIB"\]\s*=\s*'
        r'\(\s*"/Applications/DaVinci Resolve/DaVinci Resolve\.app"\s*'
        r'"/Contents/Libraries/Fusion/fusionscript\.so"\)')
    for fn in sorted(os.listdir(ROOT)):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(ROOT, fn)
        if os.path.abspath(p) == os.path.abspath(__file__):
            continue
        s = read(p)
        orig = s
        changed = []
        if "SCRIPT_API = " + MAC_SCRIPT_API in s:
            s = s.replace("SCRIPT_API = " + MAC_SCRIPT_API, SCRIPT_API_DEF)
            changed.append("SCRIPT_API/RESOLVE_SCRIPT_LIB 跨平台")
        if lib_re.search(s):
            s = lib_re.sub(r'\g<ind>os.environ["RESOLVE_SCRIPT_LIB"] = _RESOLVE_LIB', s)
            if "SCRIPT_API/RESOLVE_SCRIPT_LIB 跨平台" not in changed:
                changed.append("RESOLVE_SCRIPT_LIB 跨平台")
        if s != orig:
            note(p, "；".join(changed))
            if not dry:
                write(p, s)


def patch_ffmpeg_literals(dry):
    for fn in sorted(os.listdir(ROOT)):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(ROOT, fn)
        if os.path.abspath(p) == os.path.abspath(__file__):
            continue
        s = read(p)
        if MAC_FFMPEG in s:
            s = s.replace(MAC_FFMPEG, FFMPEG_EXPR)
            note(p, "写死的 ffmpeg 路径 -> 按平台查找")
            if not dry:
                write(p, s)


def patch_fontsel(dry):
    src = os.path.join(HERE, "fontsel_win.py")
    p = os.path.join(ROOT, "fontsel.py")
    if not os.path.exists(src):
        note(src, "找不到 fontsel_win.py，跳过")
        return
    if not dry:
        if os.path.exists(p) and not os.path.exists(os.path.join(ROOT, "fontsel_mac.py")):
            shutil.copy2(p, os.path.join(ROOT, "fontsel_mac.py"))
        shutil.copy2(src, p)
    note(p, "换成跨平台版（CoreText / PIL 双实现），原版备份为 fontsel_mac.py")


def patch_utf8_subprocess(dry):
    p = os.path.join(ROOT, "run_chapter.py")
    if not os.path.exists(p):
        return
    s = read(p)
    old = "r = subprocess.run(cmd, text=True)"
    if old in s:
        s = s.replace(old, 'r = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")')
        note(p, "subprocess 输出强制 UTF-8（中文路径不再解码报错）")
        if not dry:
            write(p, s)


def patch_didot_paths(dry):
    """把 macOS 系统字体路径改成可用环境变量覆盖（jy_layout / build_resolve_final）。"""
    for fn in ("jy_layout.py", "build_resolve_final.py"):
        p = os.path.join(ROOT, fn)
        if not os.path.exists(p):
            continue
        s = read(p)
        if '"/System/Library/Fonts/Supplemental/Didot.ttc"' in s:
            s = s.replace(
                '"/System/Library/Fonts/Supplemental/Didot.ttc"',
                '(os.environ.get("JIFANGTANG_FONT_EN") '
                'or "/System/Library/Fonts/Supplemental/Didot.ttc")')
            note(p, "Didot 路径改为可用 JIFANGTANG_FONT_EN 覆盖")
            if not dry:
                write(p, s)


def patch_home_paths(dry):
    """把 `/Users/geophyto/Public` 之类的外部目录改成"当前用户主目录"。"""
    for fn in sorted(os.listdir(ROOT)):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(ROOT, fn)
        if os.path.abspath(p) == os.path.abspath(__file__):
            continue
        s = read(p)
        orig = s
        # 构建日志 / 探针报告写到用户主目录
        s = s.replace('os.path.join("/Users/geophyto/Public", ',
                      'os.path.join(os.path.expanduser("~"), ')
        s = re.sub(r'"\/Users\/geophyto\/Public\/([A-Za-z0-9_.]+)"',
                   r'os.path.join(os.path.expanduser("~"), "\1")', s)
        # 探针脚本自己写死的 AUTO / FFMPEG
        s = s.replace('AUTO = "/Users/geophyto/Movies/JianyingPro/autoedit"',
                      'AUTO = os.path.dirname(os.path.abspath(__file__))')
        s = s.replace('FFMPEG = os.path.join(AUTO, "bin", "ffmpeg")',
                      'FFMPEG = (__import__("shutil").which("ffmpeg") or '
                      'os.path.join(AUTO, "bin", "ffmpeg.exe" if os.name == "nt" else "ffmpeg"))')
        if s != orig:
            note(p, "外部目录/写死路径 -> 跨平台")
            if not dry:
                write(p, s)


MISC_MAC_PATHS = [
    # (文件, 原字符串, 新字符串, 说明)
    ("build_draft.py",
     'DRAFT_DIR = "/Users/geophyto/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"',
     'DRAFT_DIR = os.environ.get("JIANYING_DRAFT_DIR") or ('
     'os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), '
     '"JianyingPro", "User Data", "Projects", "com.lveditor.draft") '
     'if os.name == "nt" else '
     'os.path.expanduser("~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"))',
     "剪映草稿目录按平台解析（旧方案脚本，流水线不用）"),
    ("doctor.py",
     'LOCAL = "/Users/geophyto/Movies/ch01一阴一阳.mov"',
     'LOCAL = os.environ.get("JIFANGTANG_DOCTOR_CLIP") or os.path.join('
     'os.path.dirname(os.path.abspath(__file__)), "assets", "blank_alpha.mov")',
     "体检用的本地样片改成随包素材（Windows 上路径不存在）"),
    ("jy_layout.py",
     'FONT_DIR = "/Users/geophyto/Movies/JianyingPro/autoedit/fonts"',
     'FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")',
     "字体目录跟随脚本所在目录"),
    ("build_resolve_final.py",
     'font_zh = "/Users/geophyto/Movies/JianyingPro/autoedit/fonts/SourceHanSerifCN-Medium.otf"',
     'font_zh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", '
     '"SourceHanSerifCN-Medium.otf")',
     "字体路径跟随脚本所在目录"),
]


def patch_misc_mac_paths(dry):
    for fn, old, new, why in MISC_MAC_PATHS:
        p = os.path.join(ROOT, fn)
        if not os.path.exists(p):
            continue
        s = read(p)
        if old in s:
            s = s.replace(old, new)
            note(p, why)
            if not dry:
                write(p, s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dry = a.dry_run
    print(f"=== 目标目录: {ROOT} ===")
    print("（--dry-run，只报告不改）" if dry else "（就地打补丁）")
    patch_editlib(dry)
    patch_resolve_env(dry)
    patch_ffmpeg_literals(dry)
    patch_fontsel(dry)
    patch_utf8_subprocess(dry)
    patch_didot_paths(dry)
    patch_home_paths(dry)
    patch_misc_mac_paths(dry)
    if report:
        print("\n改动清单:")
        for f, w in report:
            print(f"  · {f:28} {w}")
    else:
        print("\n没有需要改的（可能已经打过补丁）")
    print("\n下一步: python doctor_windows.py   # 环境体检")
    return 0


if __name__ == "__main__":
    sys.exit(main())
