#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""editlib.py —— 寄方堂人参视频：共享常量 / 规格 / 坐标换算 / 时间轴校验

坐标公式说明（重要）
--------------------
交接文档 §6.1 与附录 A 把 y 公式印成了 `px_y = 963.4 + 0.25018*jy`，
代入文档自己的 OCR 实测表即可证伪。正确公式是：

    px_x = 540 + jx / 1920 * 540          # 文档写成 *270，错
    px_y = 963.7 - 0.281 * jy             # 文档符号写反
    px_size = size * scale% / 100 * 7.0

四条 OCR 实测锚点（§6.1 表）在正确公式下误差 ≤0.2px：
    档案卡中文 jy=-2222 -> 1588.1 (实测 1585)
    档案卡英文 jy=-2456 -> 1653.8 (实测 1655)
    中文字幕   jy=-1029 -> 1252.8 (实测 1253)
    英文字幕   jy=-1247 -> 1314.1 (实测 1316)
物理含义：+jy 向上。附录 B 里"小 logo""引文竖排"两行是用错误公式算的，
不可采信（详见 DSH_HANDOFF_ERRATA.md 第二节）。
"""
import json
import os
import re

# ----------------------------------------------------------------- 画布
W, H, FPS = 1080, 1920, 30.0        # 竖屏 1080x1920 @ 30fps
CARD_SEC = 2.0                      # 档案卡 2.0s，无配音
# 用户 2026-10-06 明确要求：**配音之间不留空隙**，且
# 「以配音为时长基准，配音 / 中英字幕 / 镜头 三者严格等长」。
# 所以句间停顿改为 0（原规格 0.12~0.15s 已被用户推翻）。
GAP_SEC = 0.0
TOTAL_MAX = 20.0                    # 总时长上限
# 用户 2026-10-06 决定：**第 02 章文案不压缩、语速不加快，允许超过 20s**。
# 所以这一章不按 TOTAL_MAX 硬拦（只提示）；其余章仍按 20s 硬约束。
# 以后某一章再获用户批准超时，把章号加进来即可（**不要**把检查整体关掉）。
ALLOW_OVER = {"02"}
FADE_BGM_OUT = 0.7                  # BGM 末尾淡出
GAP_JOIN_SEC = 0.5                  # 段间"闪黑"总时长（API 无转场，用叠加层实现）
FLASH_FRAMES = int(round(GAP_JOIN_SEC * FPS))          # 15 帧
FLASH_OUT_FRAMES = (FLASH_FRAMES + 1) // 2             # 8 帧淡出到黑
FLASH_IN_FRAMES = FLASH_FRAMES - FLASH_OUT_FRAMES      # 7 帧从黑淡入
FADE_VIDEO_TAIL_SEC = 0.3                              # 视频末段渐隐
FADE_VIDEO_TAIL_FRAMES = int(round(FADE_VIDEO_TAIL_SEC * FPS))   # 9 帧
FADE_LOGO_SMALL_SEC = 0.5                              # 小 logo 渐隐
FADE_LOGO_BIG_SEC = 0.3                                # 大 logo 渐现（上限，见下）
LOGO_BIG_FADE_RATIO = 0.35                             # 淡入占镜头时长的比例
LOGO_BIG_FADE_MAX = 0.6                                # 淡入最长秒数
STILL_SEC_DEFAULT = 5.0                                # 静帧缺省时长

AUTO = os.path.dirname(os.path.abspath(__file__))
def _find_bin(name):
    """找 ffmpeg/ffprobe：先看 bin/（本地随包），再看 PATH。"""
    import shutil as _sh
    exe = name + (".exe" if os.name == "nt" else "")
    local = os.path.join(AUTO, "bin", exe)
    if os.path.exists(local):
        return local
    return _sh.which(name) or _sh.which(exe) or local


FFMPEG = _find_bin("ffmpeg")
# 字体：Fusion Text+ 的 "Font" 输入要的是**字体族名**，不是文件路径！
# 写成路径时 Fusion 解析不了，会把那串路径**当成文字渲染出来**
# （用户截图里底部显示的 "/Fonts/Supplemental/Didot.ttc Bold" 就是这个），
# 并且导致 Fusion 渲染失败、整条时间线无法导出。
# 族名由 OTF/TTC 的 name 表读出，见 ~/Public/DSH_HANDOFF_ERRATA.md。
# .otf 已安装到 ~/Library/Fonts/，否则 Fusion 找不到。
# 族名/样式名**用 CoreText 实际查询**，不要手写 —— 见 fontsel.py。
# 教训：思源宋体的 name 表里有三套名字（"Source Han Serif CN Medium" /
# "Source Han Serif CN" / "SourceHanSerifCN-Medium"），macOS 报的是中间那个；
# 中文系统下 system_profiler 还会本地化成"思源宋体 CN"。写错 Fusion 就找不到字体。
try:
    import fontsel as _fs
    FONT_ZH = _fs.resolve("SourceHanSerifCN-Medium", "Source Han Serif CN")
    FONT_ZH_B = _fs.resolve("SourceHanSerifCN-Heavy", "Source Han Serif CN")
    FONT_EN = _fs.resolve("Didot", "Didot")
except Exception:                       # 非 macOS / 查询失败时退回硬编码
    FONT_ZH = "Source Han Serif CN"
    FONT_ZH_B = "Source Han Serif CN"
    FONT_EN = "Didot"
# Fusion Text+ 的 CharacterSpacing **默认 1.0，是"倍数"不是像素**！
# 我一开始把剪映的"字距"(0~4) 直接写进去，等于把字距放大到 3 倍，
# 结果字幕被拉满整行（用户截图里的 "看 着 相 对" 就是这个）。
# 现在按"每字增加 tr 像素"换算成倍数：1 + tr/px_size。
CSPACE_DEFAULT = 1.0
# 水平对齐：Text+ 默认 3.0，会**两端对齐把整行拉开**。改用 1.0（居中）。
HJUST_CENTER = 1.0
# 首镜要跳过素材开头的黑帧/空白（用户反馈"开头前几帧全黑"）
HEAD_TRIM_SEC = 0.30

STYLE_ZH = "Medium"       # 思源中宋
STYLE_ZH_B = "Heavy"      # 思源粗宋
STYLE_EN = "Regular"      # Didot
STYLE_NORMAL = STYLE_EN
LOGO = os.path.join(AUTO, "assets", "logo.png")
BGM = os.path.join(AUTO, "assets", "bgm.mp3")
BLANK = os.path.join(AUTO, "assets", "blank.png")   # 1080x1920 全透明，文字轨载体

# ----------------------------------------------------------------- 颜色
C_ECDABD = "ecdabd"   # 档案卡 / 中文
C_BFB8AC = "bfb8ac"   # 英文 / 引文
C_A53A28 = "a53a28"   # 古文强调（加粗）

# ----------------------------------------------------------------- 规格
# 剪映原始坐标；scale 为剪映"缩放%"，tr 为字距
SPEC = {
    "card_zh": dict(jx=0,     jy=-2222, size=15, scale=51,  tr=4, color=C_ECDABD,
                    font=FONT_ZH,   style=STYLE_ZH,
                    vertical=False, bold=False, opacity=1.0),
    "card_en": dict(jx=0,     jy=-2456, size=11, scale=62,  tr=0, color=C_ECDABD,
                    font=FONT_EN,   style=STYLE_EN,
                    vertical=False, bold=False, opacity=1.0),
    "card_nm": dict(jx=1445,  jy=1207,  size=25, scale=78,  tr=0, color=C_ECDABD,
                    font=FONT_ZH_B, style=STYLE_ZH_B,
                    vertical=True,  bold=True,  opacity=1.0),
    "zh":      dict(jx=0,     jy=-1029, size=6,  scale=117, tr=3, color=C_ECDABD,
                    font=FONT_ZH,   style=STYLE_ZH,
                    vertical=False, bold=False, opacity=1.0),
    "en":      dict(jx=0,     jy=-1247, size=6,  scale=117, tr=0, color=C_BFB8AC,
                    font=FONT_EN,   style=STYLE_EN,
                    vertical=False, bold=False, opacity=1.0),
    "quote":   dict(jx=0,     jy=-1029, size=8,  scale=117, tr=3, color=C_A53A28,
                    font=FONT_ZH,   style=STYLE_ZH,
                    vertical=False, bold=True,  opacity=1.0),
    "cite":    dict(jx=-1515, jy=2069,  size=8,  scale=117, tr=0, color=C_BFB8AC,
                    font=FONT_ZH,   style=STYLE_ZH,
                    vertical=True,  bold=False, opacity=0.5),
}
# logo（规格 §5.4；doc 自带"待确认"，数值来源存疑，见 ERRATA §2.3）
LOGO_W, LOGO_H = 1253, 585
LOGO_SPEC = {
    "small": dict(scale_pct=19, jx=-1445, jy=2156, t0=0.0, dur=CARD_SEC, fade_out=0.5),
    # dur=None -> 动态取值：**与最后一个镜头等长**（用户 2026-10-06 要求，
    # 不要让大 logo 横跨几个镜头）。淡入也随之按比例缩短。
    # zoom=0.65：用户在达芬奇界面里把结尾大 logo 的"缩放"定成 0.65
    # （不占满全屏）。已做进脚本，重建时不会丢。
    "big":   dict(scale_pct=88, jx=0,     jy=0,    t0=None, dur=None,    fade_in=None,
                  zoom=0.65),
}

# 视频轨布局（自下而上）。文字轨用全透明 PNG 作载体 + Fusion Text+
TRACK_LAYOUT = [
    ("V1", "video", "镜头"),
    ("V2", "video", "logo"),
    ("V3", "video", "档案卡1"),
    ("V4", "video", "档案卡2"),
    ("V5", "video", "档案卡3"),
    ("V6", "video", "中文字幕"),
    ("V7", "video", "英文字幕"),
    ("V8", "video", "引文竖排"),
    ("V9", "video", "闪黑"),
    ("A1", "audio", "配音"),
    ("A2", "audio", "BGM"),
]


# ----------------------------------------------------------------- 坐标换算
def px_x(jx):
    """剪映 x -> 画布像素 x（0..1080）。"""
    return W / 2.0 + jx / 1920.0 * (W / 2.0)


def px_y(jy):
    """剪映 y -> 画布像素 y（0..1920，向下）。+jy 向上。"""
    return 963.7 - 0.281 * jy


def px_size(size, scale_pct):
    """剪映 size x 缩放% -> 最终像素字号。"""
    return size * scale_pct / 100.0 * 7.0


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def fusion_center(jx, jy):
    """-> Fusion Text+ 的 Center 归一化坐标（0..1，y 向上）。"""
    return px_x(jx) / W, 1.0 - px_y(jy) / H


# Fusion Text+ 的 Size 基准高度（**不是画面高度 1920**）。
# 实测：Size=0.02559 渲出来 CJK 字距只有约 26px，
# 而参考成片 20260625-23 的字距是约 50px（= 规格算出的 49.14px）。
# 说明 Resolve 给竖屏时间线建的 Fusion 合成是按 **1920x1080（横）** 来的，
# Size 以合成高度 1080 为 1.0。用 1920 会让字小掉一半。
# 由 `_DSH_CALIB` 的 CAL1..CAL5（同一串 20 个「国」、5 档字号）**线性拟合**得到：
#     Size  0.0256 / 0.0350 / 0.0455 / 0.0550 / 0.0700
#     em px 17.82  / 24.36  / 31.66  / 38.30  / 48.72
#     K     695.9  / 696.0  / 695.7  / 696.3  / 696.0      <- 一致性 0.05%
# 所以 K = 696.0。参考成片 20260625-23 的中文字距约 50.1px，
# 规格 px_size=49.14 -> Size=49.14/696=0.0706 -> em 约 49.1px ✅ 对上。
FUSION_SIZE_K = 696.0


def fusion_size(size, scale_pct):
    """-> Fusion Text+ 的 Size（基准高度见 FUSION_SIZE_K）。"""
    return px_size(size, scale_pct) / FUSION_SIZE_K


def logo_scale_from_pct(pct):
    """剪映 img 段 scale_x/y：以画布宽为基准的归一化缩放。"""
    return (LOGO_W / float(W)) * (pct / 100.0)


def ramp_name(direction, frames):
    """黑场渐变素材文件名（**按帧**命名，避免 0.25s 被量化成 8 帧 = 0.267s）。

    up   = 白→黑（乘法合成 = 渐隐到黑）
    down = 黑→白（乘法合成 = 从黑渐现）
    """
    return f"ramp_{direction}_{int(frames):02d}f.mov"


DASHES = "\u2014\u2013-\u2010\u2011\u2012"


def parse_timecode(tc, fps=30.0):
    """达芬奇 GetClipProperty("Duration") 返回的是**时间码字符串**
    （如 '00:00:20:08'），不是浮点秒。直接 float() 会抛异常 ->
    之前的 dur_of 把全部素材时长报成 0.00s。"""
    if tc is None:
        return 0.0
    try:
        return float(tc)
    except (TypeError, ValueError):
        pass
    parts = str(tc).strip().split(":")
    try:
        if len(parts) == 4:
            h, m, sec, f = parts
            return int(h) * 3600 + int(m) * 60 + int(sec) + int(f) / float(fps)
        if len(parts) == 3:
            h, m, sec = parts
            return int(h) * 3600 + int(m) * 60 + float(sec)
    except ValueError:
        return 0.0
    return 0.0


def clean_subtitle(s):
    """去掉切句残留的悬空破折号。

    中英是各自独立切的，`...形状——比如这支参。` 会被切成
    `...形状—` / `—比如这支参。`；字幕不该以破折号开头或结尾。
    batch_timelines 与 timeline_tool 都必须调这里，否则修好的 ch01 会被重新做坏。
    """
    if not s:
        return s
    t = s.strip()
    t = re.sub(r"^[%s]+\s*" % re.escape(DASHES), "", t)
    t = re.sub(r"\s*[%s]+$" % re.escape(DASHES), "", t)
    return t.strip()


# ----------------------------------------------------------------- 时长账
def voice_total(segs):
    return round(sum(s["dur"] for s in segs), 3)


def cspace_for(spec_name):
    """字距：剪映 tr(px) -> Fusion CharacterSpacing 倍数。"""
    sp = SPEC[spec_name]
    if not sp.get("tr"):
        return CSPACE_DEFAULT
    px = px_size(sp["size"], sp["scale"])
    return round(CSPACE_DEFAULT + sp["tr"] / max(1.0, px), 4)


# 英文字幕换行（用户 2026-10-06：以中文字幕长度为基准，按词换行，
# 首行位置不动、第二行下移）
SUB_SAFE_W = 960          # 字幕安全宽度（1080 - 两侧各 60）
SUB_WRAP_FLOOR = 0.65     # 换行上限的下限，避免中文很短时把英文切得太碎


def en_wrap_max(zh_width):
    """给定中文行宽，返回英文换行的宽度上限。"""
    return max(SUB_SAFE_W * SUB_WRAP_FLOOR, min(zh_width, SUB_SAFE_W))


def logo_big_name(frames):
    """大 logo 的素材名（按帧数区分，保证"与最后镜头等长"）。"""
    return f"logo_big_{int(frames)}f.mov"


def logo_big_geom(last_frames):
    """返回（帧数, 时长秒, 淡入秒）。大 logo 与最后一个镜头完全重合。"""
    d = round(int(last_frames) / FPS, 4)
    fi = min(LOGO_BIG_FADE_MAX, d * LOGO_BIG_FADE_RATIO)
    return int(last_frames), d, round(fi, 4)


def timeline_total(segs, card_sec=CARD_SEC, gap=GAP_SEC):
    """总时长（秒）。有 frames（按帧排过）时按帧算，与 lay_out 一致；
    否则退回按秒近似。"""
    if segs and all("frames" in s for s in segs):
        g = int(round(gap * FPS))
        f = (int(round(card_sec * FPS)) + sum(int(s["frames"]) for s in segs)
             + g * max(0, len(segs) - 1))
        return round(f / FPS, 4)
    if not segs:
        return card_sec
    return round(card_sec + sum(s["dur"] for s in segs)
                 + gap * max(0, len(segs) - 1), 3)


def frames_of(dur):
    """配音秒数 -> 帧数，**向上取整**，保证 WAV 不被裁短。"""
    import math
    return int(math.ceil(float(dur) * FPS - 1e-6))


def lay_out(segs, card_sec=CARD_SEC, gap=GAP_SEC):
    """按**整数帧**排布：第 i 句占 [t0f, t0f+frames)。

    为什么要按帧：早先按秒累加再各自 round 成帧，会出现"槽位比配音短 1 帧"
    （2.68s 的配音落在 80 帧 = 2.667s 的槽里），配音被裁。
    现在槽位 = ceil(配音帧数)，**配音 / 中英字幕 / 镜头三者严格等长**（用户要求）。

    写入：t0f（帧，权威）、frames、t0（秒，仅供阅读）
    """
    g = int(round(gap * FPS))
    t = int(round(card_sec * FPS))
    for i, s in enumerate(segs):
        s["t0f"] = t
        s["frames"] = frames_of(s["dur"])
        s["t0"] = round(t / FPS, 4)
        t += s["frames"] + (g if i < len(segs) - 1 else 0)
    return round(t / FPS, 4)


# ----------------------------------------------------------------- 选片
def load_excluded(chapter=None):
    """读取 shots_excluded.json，返回该章不可用素材名集合。"""
    p = os.path.join(AUTO, "shots_excluded.json")
    if not os.path.exists(p):
        return set()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return set()
    out = set()
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if chapter is not None and str(k) != str(chapter):
            continue
        if isinstance(v, dict):
            out |= set(v.get("excluded") or [])
        elif isinstance(v, list):
            out |= set(v)
    return out


def card_clip_for(durations):
    """挑"档案卡背景"用的素材：A 类里**最长**的那条（一般就是"全参悬吊"整根全景）。

    用户 2026-10-06：档案卡背景必须是**整根完整的参**，而且要与第一句配音的镜头
    **不是同一个镜头**。所以这条素材要被"预订"给档案卡，正文不再使用它。
    """
    A = [k for k in durations if k.upper().startswith("A")]
    return max(A, key=lambda k: durations[k]) if A else None


def assign_shots(segs, durations, margin=0.3, excluded=None, reserve_card=True):
    """选片：**镜头时长必须 >= 配音时长**（硬约束，规格 §5.7）。

    A = 全景（首尾句，规格 §7「开场全景 / 落点句拉回全景」）
    B = 部位特写（中间句）
    在同一条素材上按 (使用次数, 是否与上一镜重复, -时长) 排序，
    把素材摊开用、避免相邻两句同一个镜头；用同一素材时靠 src_start 错开画面。

    durations: {clip文件名: 秒}
    返回 (plan, notes, src_starts)
    """
    excluded = set(excluded or ())
    if reserve_card:
        cc = card_clip_for(durations)
        if cc:
            excluded.add(cc)          # 把"整根全景"留给档案卡
    keys = [k for k in durations if k not in excluded]
    if not keys:
        keys = list(durations)
    A = [k for k in keys if k.upper().startswith("A")]
    B = [k for k in keys if k.upper().startswith("B")]
    pool = {"A": A or keys, "B": B or keys}
    plan, notes, starts = [], [], []
    used, taken = {}, {}
    prev = None
    for i, s in enumerate(segs):
        need = round(s["dur"] + margin, 3)
        want = "A" if (i == 0 or i == len(segs) - 1) else "B"
        cands = [k for k in pool[want] if durations[k] >= need]
        if not cands:                                  # 退到另一类
            other = "B" if want == "A" else "A"
            cands = [k for k in pool[other] if durations[k] >= need]
            if cands:
                notes.append(f"句{i+1}: {want} 类无 ≥{need:.2f}s 素材，"
                             f"退用 {other} 类 {cands[0]}")
        if not cands:                                  # 全都不够长
            pick = max(keys, key=lambda k: durations[k])
            notes.append(f"句{i+1}: ⚠️ 无足够长素材，退用最长 {pick} "
                         f"({durations[pick]:.2f}s < 需要 {need:.2f}s)")
        else:
            pick = min(cands, key=lambda k: (used.get(k, 0),
                                             1 if k == prev else 0,
                                             -durations[k]))
            if durations[pick] - need < 0.2:
                notes.append(f"句{i+1}: {pick} 余量仅 "
                             f"{durations[pick]-s['dur']:.2f}s")
        ss = src_start_for(durations[pick], s["dur"], taken.get(pick, 0))
        taken[pick] = taken.get(pick, 0) + 1
        used[pick] = used.get(pick, 0) + 1
        plan.append(pick)
        starts.append(ss)
        prev = pick
    return plan, notes, starts


def src_start_for(clip_dur, need, taken):
    """在素材里挑一个尽量居中、且不与已用区间重叠的入点。"""
    slack = clip_dur - need
    if slack <= 0.05:
        return 0.0
    # 在可用区间内均匀错开
    n = max(1, int(slack // (need + 0.5)) + 1)
    idx = taken % n
    return round(min(slack, (slack / max(1, n)) * idx + 0.05), 3)


# ----------------------------------------------------------------- 校验
def total_max_for(chapter=None):
    """该章的总时长上限（秒）；在 ALLOW_OVER 里的章返回 None = 不设上限（用户已批准超时）。"""
    return None if str(chapter) in {str(x) for x in ALLOW_OVER} else TOTAL_MAX


def validate(tl, durations=None):
    """离线自检。返回 (errors, warnings)。"""
    errs, warns = [], []
    segs = tl.get("segments") or []
    if not segs:
        errs.append("segments 为空")
        return errs, warns

    total = tl.get("total")
    calc = timeline_total(segs, tl.get("card_sec", CARD_SEC), tl.get("gap", GAP_SEC))
    if total is not None and abs(total - calc) > 0.02:
        errs.append(f"total 登记 {total} 与实际 {calc} 不符")
    limit = total_max_for(tl.get("chapter"))
    if limit is None:
        if calc > TOTAL_MAX + 1e-9:
            warns.append(f"总时长 {calc}s 超过 {TOTAL_MAX}s 规格上限"
                         f"（第 {tl.get('chapter')} 章已获用户批准允许超时，不拦）")
    elif calc > limit + 1e-9:
        errs.append(f"总时长 {calc}s 超过 {limit}s 上限")

    gap = tl.get("gap", GAP_SEC)
    if not (-1e-9 <= gap <= 0.15 + 1e-9):
        errs.append(f"句间停顿 {gap}s 非法（用户要求 0，不再允许 0.12~0.15）")

    if tl.get("card_sec", CARD_SEC) != CARD_SEC:
        warns.append(f"档案卡 {tl.get('card_sec')}s ≠ 规格 {CARD_SEC}s")

    # 句间间隔：能用帧就用帧比（按秒比会被四舍五入坑到，见下）
    use_frames = all("t0f" in x and "frames" in x for x in segs)
    gframes = int(round(gap * FPS))
    prev_end = None
    prev_end_f = None
    for i, s in enumerate(segs, 1):
        tag = f"句{i}"
        for k in ("en", "zh", "wav", "dur", "t0", "clip", "clip_name"):
            if k not in s:
                errs.append(f"{tag} 缺字段 {k}")
        if i == 1 and abs(s.get("t0", -1) - tl.get("card_sec", CARD_SEC)) > 0.02:
            errs.append(f"{tag} 起点 {s.get('t0')} 应等于档案卡结束 "
                        f"{tl.get('card_sec', CARD_SEC)}")
        if use_frames:
            if prev_end_f is not None and s["t0f"] != prev_end_f + gframes:
                errs.append(f"{tag} 帧起点 {s['t0f']} != 上一句结束 {prev_end_f}"
                            f" + 间隔 {gframes}")
            prev_end_f = s["t0f"] + s["frames"]
        else:
            if prev_end is not None and abs(s.get("t0", 0) - prev_end - gap) > 0.02:
                errs.append(f"{tag} 起点 {s.get('t0')} 与上一句结束 {prev_end} "
                            f"间隔不为 {gap}")
            prev_end = round(s.get("t0", 0) + s.get("dur", 0), 3)

        if s.get("src_start") is None:
            warns.append(f"{tag} 无 src_start")
        # 镜头时长 >= 配音时长
        if durations and s.get("clip_name") in durations:
            avail = durations[s["clip_name"]]
            ss = s.get("src_start") or 0.0
            need = s["dur"]
            if ss + need > avail + 0.02:
                errs.append(f"{tag} 镜头不足: {s['clip_name']} {avail:.2f}s, "
                            f"需 src_start({ss})+{need:.2f}={ss+need:.2f}s")
            elif avail - need < 0.2:
                warns.append(f"{tag} {s['clip_name']} 余量仅 {avail-need:.2f}s")

        # 字幕不该以破折号开头/结尾（切句残留）
        for lang in ("zh", "en"):
            t = (s.get(lang) or "").strip()
            if t.startswith(("—", "-", "–")):
                warns.append(f"{tag} {lang} 以破折号开头: {t[:12]!r}")
            if t.endswith(("—", "-", "–")) and not t.endswith("——"):
                warns.append(f"{tag} {lang} 以破折号结尾: ...{t[-12:]!r}")

    # 用到了"不可用素材"（便签纸等）要报错
    ex = load_excluded(tl.get("chapter"))
    for i, s in enumerate(segs, 1):
        if s.get("clip_name") in ex:
            errs.append(f"句{i} 用了被排除的素材 {s['clip_name']}（便签纸镜头）")

    # 末段应为全景
    last = segs[-1].get("clip_name", "")
    if last and not last.upper().startswith("A"):
        warns.append(f"末句用 {last}（非 A 全景）——规格要求落点句拉回全景")
    first = segs[0].get("clip_name", "")
    if first and not first.upper().startswith("A"):
        warns.append(f"首句用 {first}（非 A 全景）——规格要求开场全景")
    return errs, warns


def load_durations(path=None):
    """读取素材时长缓存 {文件名: 秒}（外置盘不可读时离线用）。"""
    path = path or os.path.join(AUTO, "media_durations.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}
