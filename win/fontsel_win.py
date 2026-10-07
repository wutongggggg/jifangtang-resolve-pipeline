#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fontsel.py —— 跨平台版本（macOS 用 CoreText，Windows 用 PIL）

这是 patch_for_windows.py 会覆盖安装到 autoedit 根目录的 `fontsel.py`。
原 macOS 版会被备份成 `fontsel_mac.py`。

对外 API 与原版**完全一致**，所以 editlib.py / check_text_layout.py /
build_resolve5.py / batch_timelines.py 都不用改：
    enumerate_fonts() / resolve(ps, fallback) / measure(family, text, px) /
    wrap_to_width(...) / wrap_balanced(...) / line_height(family, px) / main()

两个平台都做同一件事：
  1) 查"字体族名"（Fusion Text+ 的 Font 输入要的是族名，不是文件路径）；
  2) 用**真实字形宽度**量文字像素宽度（"一次成片"排版预检的基础）。

Windows 说明
-----------
  · 中文：用本目录 `fonts/SourceHanSerifCN-Medium.otf`（随包带上）测宽，
    族名仍是 `Source Han Serif CN`。
  · 英文：macOS 用的是系统自带的 **Didot**；Windows 没有这个字体。
    找不到时不会瞎猜，直接报错并告诉你怎么配（见 main()）。
    配置方式：设环境变量
        JIFANGTANG_FONT_EN        = 你装的英文衬线字体文件全路径
        JIFANGTANG_FONT_EN_FAMILY = 该字体在系统里的**族名**（Fusion 里显示的族名）
    例：装好 Playfair Display 后
        set JIFANGTANG_FONT_EN=C:\\Windows\\Fonts\\PlayfairDisplay-Regular.ttf
        set JIFANGTANG_FONT_EN_FAMILY=Playfair Display
"""
import ctypes
import ctypes.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

WANTED = {
    "SourceHanSerifCN-Medium": ("思源中宋", "Medium"),
    "SourceHanSerifCN-Heavy": ("思源粗宋", "Heavy"),
    "Didot": ("英文 Didot", "Regular"),
}

# 中文字体：优先用随包带的 .otf
_ZH_FILES = {
    "Source Han Serif CN": os.path.join(_HERE, "fonts", "SourceHanSerifCN-Medium.otf"),
}
_ZH_FILES_HEAVY = os.path.join(_HERE, "fonts", "SourceHanSerifCN-Heavy.otf")

# 英文衬线字体候选（Windows 上找得到的）
_EN_CANDIDATES = [
    "Didot.ttf", "Didot.ttc", "Didot.otf",
    "PlayfairDisplay-Regular.ttf", "PlayfairDisplay.ttf",
    "GFSDidot-Regular.ttf", "Bodoni*.ttf",
    "times.ttf", "georgia.ttf", "constan.ttf",  # 最后才退到这些
]

_IS_MAC = sys.platform == "darwin"
_IS_WIN = os.name == "nt"


# ============================================================ macOS: CoreText
_CT = None
_CF = None
_CT_TRIED = False


def _load():
    """加载 CoreText / CoreFoundation（仅 macOS）。失败抛异常。"""
    ct = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                             ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_long, ctypes.c_uint32]
    ct.CTFontCollectionCreateFromAvailableFonts.restype = ctypes.c_void_p
    ct.CTFontCollectionCreateFromAvailableFonts.argtypes = [ctypes.c_void_p]
    ct.CTFontCollectionCreateMatchingFontDescriptors.restype = ctypes.c_void_p
    ct.CTFontCollectionCreateMatchingFontDescriptors.argtypes = [ctypes.c_void_p]
    ct.CTFontDescriptorCopyAttribute.restype = ctypes.c_void_p
    ct.CTFontDescriptorCopyAttribute.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return ct, cf


def _coretext():
    global _CT, _CF, _CT_TRIED
    if not _CT_TRIED:
        _CT_TRIED = True
        if _IS_MAC:
            try:
                _CT, _CF = _load()
            except Exception:
                _CT = _CF = None
    return _CT, _CF


_ENC = 0x08000100  # kCFStringEncodingUTF8


def _mkstr(cf, s):
    return cf.CFStringCreateWithCString(None, s.encode("utf-8"), _ENC)


def _tostr(cf, p):
    if not p:
        return None
    buf = ctypes.create_string_buffer(1024)
    if cf.CFStringGetCString(p, buf, 1024, _ENC):
        return buf.value.decode("utf-8", "replace")
    return None


# ============================================================ Windows: PIL
_PIL = None


def _pil():
    global _PIL
    if _PIL is None:
        try:
            from PIL import ImageFont
            _PIL = ImageFont
        except Exception:
            _PIL = False
    return _PIL or None


_WIN_FAMILY_CACHE = None


def _font_dirs():
    dirs = []
    env = os.environ.get("JIFANGTANG_FONT_DIR")
    if env:
        dirs.append(env)
    dirs.append(os.path.join(_HERE, "fonts"))
    if _IS_WIN:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif _IS_MAC:
        dirs.append(os.path.expanduser("~/Library/Fonts"))
        dirs.append("/Library/Fonts")
        dirs.append("/System/Library/Fonts")
        dirs.append("/System/Library/Fonts/Supplemental")
    return [d for d in dirs if d and os.path.isdir(d)]


def _scan_fonts():
    """{族名小写: 文件路径}。用 PIL 读字体 name 表拿真实族名。"""
    global _WIN_FAMILY_CACHE
    if _WIN_FAMILY_CACHE is not None:
        return _WIN_FAMILY_CACHE
    IF = _pil()
    out = {}
    if IF is None:
        _WIN_FAMILY_CACHE = out
        return out
    for d in _font_dirs():
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for fn in names:
            if not fn.lower().endswith((".ttf", ".ttc", ".otf", ".otc")):
                continue
            p = os.path.join(d, fn)
            for idx in (0, 1, 2):
                try:
                    f = IF.truetype(p, 12, index=idx)
                    fam, _style = f.getname()
                except Exception:
                    break
                if fam:
                    out.setdefault(fam.strip().lower(), p)
                if not fn.lower().endswith((".ttc", ".otc")):
                    break
    _WIN_FAMILY_CACHE = out
    return out


_EN_FAMILIES = ["didot", "didot lt std", "playfair display", "gfs didot",
                "times new roman", "georgia"]


def _en_file():
    """英文衬线字体的文件路径（Windows 测宽用）。找不到返回 None。"""
    env = os.environ.get("JIFANGTANG_FONT_EN")
    if env and os.path.exists(env):
        return env
    # 1) 按族名反查（跨平台；用户装了 Didot 就能直接命中）
    fonts = _scan_fonts()
    for fam in _EN_FAMILIES:
        if fonts.get(fam):
            return fonts[fam]
    # 2) 按候选文件名在字体目录里找
    import glob as _g
    for d in _font_dirs():
        for cand in _EN_CANDIDATES:
            if "*" in cand:
                hits = sorted(_g.glob(os.path.join(d, cand)))
                if hits:
                    return hits[0]
                continue
            p = os.path.join(d, cand)
            if os.path.exists(p):
                return p
    return None


# ============================================================ 族名解析
def enumerate_fonts():
    """返回 {PostScript名: 族名}（macOS）；Windows 返回 {族名小写: 族名}。"""
    ct, cf = _coretext()
    if ct is not None:
        coll = ct.CTFontCollectionCreateFromAvailableFonts(None)
        descs = ct.CTFontCollectionCreateMatchingFontDescriptors(coll)
        n = cf.CFArrayGetCount(descs)
        key_fam = _mkstr(cf, "NSFontFamilyAttribute")
        key_nm = _mkstr(cf, "NSFontNameAttribute")
        out = {}
        for i in range(n):
            d = cf.CFArrayGetValueAtIndex(descs, i)
            fam = _tostr(cf, ct.CTFontDescriptorCopyAttribute(d, key_fam))
            nm = _tostr(cf, ct.CTFontDescriptorCopyAttribute(d, key_nm))
            if nm:
                out[nm] = fam
        return out
    # Windows：族名 -> 族名
    return {k: k for k in _scan_fonts()}


def resolve(ps_name, fallback=None):
    """按字体族名/PostScript 名查 Fusion 能用的族名。找不到返回 fallback。"""
    ct, cf = _coretext()
    if ct is not None:
        f = enumerate_fonts()
        if ps_name in f:
            return f[ps_name]
        low = ps_name.lower().replace("-", "").replace(" ", "")
        for nm, fam in f.items():
            if nm.lower().replace("-", "").replace(" ", "") == low:
                return fam
        return fallback
    # Windows
    if ps_name.startswith("SourceHanSerif") or (fallback or "").startswith("Source Han Serif"):
        return fallback or "Source Han Serif CN"
    if ps_name == "Didot":
        return os.environ.get("JIFANGTANG_FONT_EN_FAMILY") or fallback or "Didot"
    return fallback


# ============================================================ 测宽
_CT_EXTRA = None


def _ct_extra():
    global _CT_EXTRA
    if _CT_EXTRA is not None:
        return _CT_EXTRA
    ct, cf = _coretext()
    if ct is None:
        return None, None
    ct.CTFontCreateWithName.restype = ctypes.c_void_p
    ct.CTFontCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_double,
                                        ctypes.c_void_p]
    ct.CTFontGetGlyphsForCharacters.restype = ctypes.c_bool
    ct.CTFontGetGlyphsForCharacters.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.c_void_p, ctypes.c_long]
    ct.CTFontGetAdvancesForGlyphs.restype = ctypes.c_double
    ct.CTFontGetAdvancesForGlyphs.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.c_long]
    _CT_EXTRA = (ct, cf)
    return _CT_EXTRA


_CT_FONT_CACHE = {}
_WIN_FONT_CACHE = {}


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


def _family_file(family):
    """族名 -> 字体文件（Windows 测宽用）。"""
    if not family:
        return None
    low = family.strip().lower()
    if low.startswith("source han serif"):
        return _ZH_FILES.get("Source Han Serif CN")
    if low in ("didot", "didot lt std"):
        return _en_file()
    env_fam = (os.environ.get("JIFANGTANG_FONT_EN_FAMILY") or "").strip().lower()
    if env_fam and low == env_fam:
        return _en_file()
    return _scan_fonts().get(low)


def _win_measure(family, text, px_size):
    IF = _pil()
    if IF is None:
        return None
    path = _family_file(family)
    if not path:
        return None
    key = (path, round(px_size, 4))
    f = _WIN_FONT_CACHE.get(key)
    if f is None:
        try:
            f = IF.truetype(path, max(1, int(round(px_size))))
        except Exception:
            return None
        _WIN_FONT_CACHE[key] = f
    try:
        return float(f.getlength(text))
    except Exception:
        try:
            return float(f.getsize(text)[0])
        except Exception:
            return None


def measure(ps_name, text, px_size):
    """用真实字体度量算字符串宽度（像素）。测不了返回 None。"""
    ct, cf = _ct_extra()
    if ct is not None:
        try:
            key = (ps_name, round(px_size, 4))
            f = _CT_FONT_CACHE.get(key)
            if f is None:
                f = ct.CTFontCreateWithName(_mkstr(cf, ps_name),
                                            ctypes.c_double(px_size), None)
                _CT_FONT_CACHE[key] = f
            if not f:
                return None
            total = 0.0
            for ch in text:
                u = ch.encode("utf-16-le")
                n = len(u) // 2
                chars = (ctypes.c_uint16 * n)(*[int.from_bytes(u[i:i + 2], "little")
                                                for i in range(0, len(u), 2)])
                glyphs = (ctypes.c_uint16 * n)()
                if not ct.CTFontGetGlyphsForCharacters(f, chars, glyphs, n):
                    total += px_size
                    continue
                adv = (CGSize * n)()
                ct.CTFontGetAdvancesForGlyphs(f, 0, glyphs, adv, n)
                total += sum(adv[i].width for i in range(n))
            return total
        except Exception:
            return None
    return _win_measure(ps_name, text, px_size)


def line_height(ps_name, px_size):
    """字体的默认行高（ascent+descent+leading）。"""
    ct, cf = _ct_extra()
    if ct is not None:
        try:
            ct.CTFontGetAscent.restype = ctypes.c_double
            ct.CTFontGetAscent.argtypes = [ctypes.c_void_p]
            ct.CTFontGetDescent.restype = ctypes.c_double
            ct.CTFontGetDescent.argtypes = [ctypes.c_void_p]
            ct.CTFontGetLeading.restype = ctypes.c_double
            ct.CTFontGetLeading.argtypes = [ctypes.c_void_p]
            key = (ps_name, round(px_size, 4))
            f = _CT_FONT_CACHE.get(key)
            if f is None:
                f = ct.CTFontCreateWithName(_mkstr(cf, ps_name),
                                            ctypes.c_double(px_size), None)
                _CT_FONT_CACHE[key] = f
            return (ct.CTFontGetAscent(f) + ct.CTFontGetDescent(f)
                    + ct.CTFontGetLeading(f))
        except Exception:
            return px_size * 1.2
    IF = _pil()
    path = _family_file(ps_name)
    if IF is not None and path:
        try:
            f = IF.truetype(path, max(1, int(round(px_size))))
            asc, desc = f.getmetrics()
            return float(asc + desc)
        except Exception:
            pass
    return px_size * 1.2


def wrap_to_width(text, ps_name, px_size, max_px, max_lines=3):
    """按词边界折行，使每行宽度 <= max_px（英文用）。"""
    words = text.split()
    if not words:
        return [text]
    lines, cur = [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        wd = measure(ps_name, cand, px_size)
        if wd is None:
            wd = len(cand) * px_size * 0.5
        if cur and wd > max_px:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1 and len(words) > 1:
                rest = words[words.index(w):]
                cur = " ".join(rest)
                break
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def wrap_balanced(text, ps_name, px_size, max_px, max_lines=2, hard_max_px=None):
    """折行并让最长行尽量短（最小化最大行宽）。选法分三档，详见 SKILL.md。"""
    words = text.split()
    if len(words) <= 1:
        return [text]
    n = len(words)

    def w_of(i, j):
        return measure(ps_name, " ".join(words[i:j + 1]), px_size)

    hard = hard_max_px if hard_max_px else max_px
    best = None
    for L in range(1, min(max_lines, n) + 1):
        INF = float("inf")
        dp = [[INF] * (L + 1) for _ in range(n + 1)]
        cut = [[-1] * (L + 1) for _ in range(n + 1)]
        dp[0][0] = 0.0
        for k in range(1, L + 1):
            for j in range(1, n + 1):
                for i in range(k - 1, j):
                    if dp[i][k - 1] == INF:
                        continue
                    w = w_of(i, j - 1)
                    if w is None:
                        w = len(" ".join(words[i:j])) * px_size * 0.5
                    cand = max(dp[i][k - 1], w)
                    if cand < dp[j][k]:
                        dp[j][k] = cand
                        cut[j][k] = i
        if dp[n][L] < INF:
            mm = dp[n][L]
            if mm <= max_px + 1e-6:
                key = (0, L, mm)
            elif mm <= hard + 1e-6:
                key = (1, L, mm)
            else:
                key = (2, mm, L)
            if best is None or key < best[0]:
                best = (key, L, cut)
    if not best:
        return wrap_to_width(text, ps_name, px_size, max_px, max_lines)
    _key, L, cut = best
    out, j, k = [], n, L
    while k > 0:
        i = cut[j][k]
        out.append(" ".join(words[i:j]))
        j, k = i, k - 1
    return list(reversed(out))


def main():
    """字体自检：缺字体直接报错（否则 Fusion 渲染会失败、无法导出）。"""
    print(f"平台: {'macOS(CoreText)' if _coretext()[0] is not None else 'Windows(PIL)'}")
    print(f"字体目录: {_font_dirs()}")
    ok = True
    for ps, (label, style) in WANTED.items():
        fam = resolve(ps)
        if _coretext()[0] is not None:
            mark = "✅" if fam else "❌"
            if not fam:
                ok = False
            print(f"  {mark} {label:10} PostScript={ps!r}")
            print(f"      -> 族名(Fusion 的 Font) = {fam!r}   样式(Fusion 的 Style) = {style!r}")
        else:
            f = _family_file(fam)
            mark = "✅" if f else "❌"
            if not f:
                ok = False
            print(f"  {mark} {label:10} 族名={fam!r} 样式={style!r}")
            print(f"      -> 测宽字体文件 = {f!r}")
    if not ok:
        print("\n缺少字体！")
        if _IS_WIN:
            print("  · 中文：把 fonts/SourceHanSerifCN-*.otf 双击安装（或保留在本目录）")
            print("  · 英文：Windows 没有 macOS 的 Didot。请")
            print("      1) 装一个 Didone 衬线体（如 Playfair Display / GFS Didot），然后")
            print("      2) set JIFANGTANG_FONT_EN=C:\\Windows\\Fonts\\<文件>.ttf")
            print("         set JIFANGTANG_FONT_EN_FAMILY=<Fusion 里显示的族名>")
            print("      3) 重启达芬奇后重新自检")
        else:
            print("  把 .otf 拷到 ~/Library/Fonts/ 后重启达芬奇。")
    print("字体自检:", "通过 ✓" if ok else "失败 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
