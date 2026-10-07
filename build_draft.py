#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 timeline.json 生成剪映草稿（不再输出 mp4，交给你在剪映里审阅/导出）。

用法:
    python3 build_draft.py --timeline out/timeline_ch01.json \
        --name "ch01一阴一阳" --card-zh "23道芦碗·约26年" \
        --card-en "23 crown-scars ·c. 26yrs" --card-name "一阴一阳" \
        --citation "周易·系辞上"

产出: ~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<name>/
      draft_content.json（明文）+ draft_info.json（Mac 入口，含设备指纹）

坐标: 直接使用用户给出的剪映坐标（jy），按已实测标定的映射写入 transform:
    transform_x = jx / 1920
    transform_y = (963.4 + 0.25018*jy - 960) / 960
"""
import argparse
import json
import os
import sys

import pyJianYingDraft as draft
from pyJianYingDraft import ClipSettings, TextStyle, trange



SEC = 1_000_000          # 剪映内部时间单位是微秒


def us(seconds):
    """秒 -> 微秒（剪映内部单位）。

    踩坑: pyJianYingDraft 的 trange(2.5, 3.0) 会被当成 2.5 微秒,
    导致片段只有一帧。必须显式传微秒整数。
    """
    return int(round(float(seconds) * SEC))

W, H, FPS = 1080, 1920, 30
DRAFT_DIR = os.environ.get("JIANYING_DRAFT_DIR") or (os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JianyingPro", "User Data", "Projects", "com.lveditor.draft") if os.name == "nt" else os.path.expanduser("~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"))
AUTO = os.path.dirname(os.path.abspath(__file__))

# ---- 用户规格（剪映原始坐标 -> transform 归一化值）----
def ty(jy):      # 剪映 y -> transform_y (±1 = 半屏)
    return (963.4 + 0.25018 * jy - 960.0) / 960.0


def tx(jx):
    return jx / 1920.0


def rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i+2], 16)/255.0 for i in (0, 2, 4))


# 色板（用户指定）
C_CARD = rgb("ecdabd")
C_ZH = rgb("ecdabd")
C_EN = rgb("bfb8ac")
C_QUOTE = rgb("a53a28")

# 档案卡三轨 + 字幕 + 引文 的剪映参数
# 字体: 剪映字体库里没有 Didot（系统字体），用最接近的古典衬线占位。
# 审阅时可在剪映里把英文轨/档案卡英文轨一键换成 Didot。
FONT_EN = draft.FontType.Cormorant_Garamond_Medium
FONT_ZH = draft.FontType.思源中宋
FONT_ZH_B = draft.FontType.思源粗宋

SPEC = {
    "card_zh":  dict(size=15, scale=51, tr=4,  x=0,     y=-2222, color=C_CARD, bold=False, v=False),
    "card_en":  dict(size=11, scale=62, tr=0,  x=0,     y=-2456, color=C_CARD, bold=False, v=False),
    "card_nm":  dict(size=25, scale=78, tr=0,  x=1445,  y=1207,  color=C_CARD, bold=True,  v=True),
    "zh":       dict(size=6,  scale=117, tr=3, x=0,     y=-1029, color=C_ZH,   bold=False, v=False),
    "en":       dict(size=6,  scale=117, tr=0, x=0,     y=-1247, color=C_EN,   bold=False, v=False),
    "cite":     dict(size=8,  scale=117, tr=0, x=-1515, y=2069,  color=C_EN,   bold=False, v=True),
}


def seg(text, spec, start, dur, font, opacity=1.0):
    """按规格建一个文本片段。剪映的 size 已含缩放? 这里 size 直接给字号，
    缩放用 clip_settings.scale_x/y 表达。"""
    sc = spec["scale"] / 100.0
    return draft.TextSegment(
        text, trange(us(start), us(dur)), font=font,
        style=TextStyle(size=spec["size"], bold=spec["bold"], color=spec["color"],
                        alpha=opacity, align=1 if not spec["v"] else 0,
                        vertical=spec["v"], letter_spacing=spec["tr"]),
        clip_settings=ClipSettings(scale_x=sc, scale_y=sc,
                                   transform_x=tx(spec["x"]), transform_y=ty(spec["y"])))




def use_prepped(tl, workdir):
    """把 timeline 里的 4K 源路径换成 1080x1920 中间片（剪映可访问）。

    优先复用已抽好的 out/prep/segNN.mp4（外置盘权限可能失效，避免重抽）。
    """
    os.makedirs(workdir, exist_ok=True)
    reuse = os.path.join(AUTO, "out", "prep")
    for i, s in enumerate(tl["segments"]):
        dst = os.path.join(workdir, f"seg{i:02d}.mp4")
        if os.path.exists(dst) and os.path.getsize(dst) > 10000:
            s["clip"] = dst; continue
        src_reuse = os.path.join(reuse, f"seg{i:02d}.mp4")
        if os.path.exists(src_reuse) and os.path.getsize(src_reuse) > 10000:
            import shutil; shutil.copy2(src_reuse, dst)
            s["clip"] = dst; continue
        # 兜底：从原始 4K 现抽
        import subprocess
        cmd = [os.path.join(AUTO, "bin", "ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
               "-ss", str(s["src_start"]), "-t", str(s["dur"]), "-i", s["clip"],
               "-vf", "transpose=1,scale=1080:1920:force_original_aspect_ratio=increase,"
                      "crop=1080:1920,fps=30,setsar=1",
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", dst]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"预抽失败(seg{i}): {r.stderr[-300:]}")
        s["clip"] = dst
    return tl




def add_logos(script, segs, logo_path, card_sec=2.0):
    """加入两个 logo（同一张 png）：
       小 logo：档案卡期间（0~2s），缩放 19%，位置 (-1445, 2156)，0.5s 渐隐
       大 logo：结尾 3s 居中，缩放 88%，0.3s 渐现
    PNG 实际像素宽 1658，缩放到 W=1080 画布时，
    scale_x = (1658/1080) * 缩放比例
    """
    from PIL import Image as _Im
    if not os.path.exists(logo_path):
        print("  ⚠️ logo 文件不存在, 跳过"); return
    with _Im.open(logo_path) as im:
        lw, lh = im.size
    Wc, Hc = 1080.0, 1920.0
    total = segs[-1]["t0"] + segs[-1]["dur"]

    def mk(scale_pct, x, y, start, dur):
        sc = scale_pct / 100.0
        vs = draft.VideoSegment(
            logo_path, trange(us(start), us(dur)),
            clip_settings=ClipSettings(scale_x=(lw/Wc)*sc, scale_y=(lw/Wc)*sc,
                                       transform_x=tx(x), transform_y=ty(y)))
        return vs

    # 小 logo（档案卡期间）
    small = mk(19, -1445, 2156, 0.0, card_sec)
    try:
        small.add_animation(draft.OutroType.渐隐, duration="0.5s")
    except Exception:
        pass
    script.add_segment(small, "logo")
    # 大 logo（结尾 3s，居中）
    big = mk(88, 0, 0, max(0.0, total - 3.0), min(3.0, total))
    try:
        big.add_animation(draft.IntroType.渐显, duration="0.3s")
    except Exception:
        pass
    script.add_segment(big, "logo")
    print(f"  logo: 小(档案卡期 19%) + 大(结尾3s 88%)  源图 {lw}x{lh}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--card-zh", default="")
    ap.add_argument("--card-en", default="")
    ap.add_argument("--card-name", default="")
    ap.add_argument("--citation", default="")
    ap.add_argument("--bgm", default=os.path.join(AUTO, "assets", "bgm.mp3"))
    a = ap.parse_args()

    CARD = 2.0
    tl = json.load(open(a.timeline, encoding="utf-8"))
    # 先把 4K 源换成预抽好的 1080x1920 中间片（剪映可访问 + 时长精确匹配）
    prep_dir = os.path.join(AUTO, "draft_prep", f"ch{tl['chapter']}")
    tl = use_prepped(tl, prep_dir)
    segs = tl["segments"]
    doc = json.load(open(os.path.join(AUTO, "chapters.json"), encoding="utf-8"))
    ch = next((c for c in doc["chapters"] if c["no"] == tl["chapter"]), {})

    os.makedirs(DRAFT_DIR, exist_ok=True)
    folder = draft.DraftFolder(DRAFT_DIR)
    script = folder.create_draft(a.name, W, H, fps=FPS, allow_replace=True)

    # ---- 轨道（后建的在上层）----
    script.append_track(draft.TrackSpec(draft.TrackType.video, name="镜头"))
    script.append_track(draft.TrackSpec(draft.TrackType.audio, name="配音"))
    script.append_track(draft.TrackSpec(draft.TrackType.audio, name="BGM"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="档案卡1"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="档案卡2"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="档案卡3"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="中文字幕"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="英文字幕"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="引文竖排"))
    script.append_track(draft.TrackSpec(draft.TrackType.video, name="logo"))

    # ---- 镜头轨：档案卡 2s 用第一段全景，然后逐个镜头 ----
    # 档案卡背景（0~2s）：用最长的一条预抽片
    bg = max(segs, key=lambda x: x["dur"])
    script.add_segment(draft.VideoSegment(bg["clip"], trange(us(0), us(CARD))), "镜头")
    # 正文镜头：预抽片本身即切好的片段，直接用；段间加 0.5s 闪黑转场
    shot_segs = []
    for s in segs:
        vs = draft.VideoSegment(s["clip"], trange(us(s["t0"]), us(s["dur"])))
        script.add_segment(vs, "镜头")
        shot_segs.append(vs)
    for vs in shot_segs[:-1]:
        try:
            vs.add_transition(draft.TransitionType.闪黑, duration="0.5s")
        except Exception as e:
            print("  转场失败:", str(e)[:60]); break
    print(f"  镜头转场: {len(shot_segs)-1} 处 0.5s 闪黑")

    # ---- 配音轨（每句一条，从档案卡结束起）----
    for s in segs:
        script.add_segment(draft.AudioSegment(s["wav"], trange(us(s["t0"]), us(s["dur"]))), "配音")

    # ---- BGM：从第 0 帧开始，覆盖到配音结束（末尾 0.7s 淡出）----
    if os.path.exists(a.bgm):
        import re as _re, subprocess as _sp
        total = segs[-1]["t0"] + segs[-1]["dur"]
        r=_sp.run([os.path.join(AUTO,"bin","ffmpeg"),"-hide_banner","-i",a.bgm],
                  capture_output=True,text=True)
        m=_re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",r.stderr)
        bdur=int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3)) if m else 0
        # 循环播放覆盖到配音结束
        bgm_off, pieces = 0.0, []
        while bgm_off < total - 0.05:
            piece = min(bdur, total - bgm_off)
            seg_b = draft.AudioSegment(a.bgm, trange(us(bgm_off), us(piece)))
            script.add_segment(seg_b, "BGM")
            pieces.append(seg_b)
            bgm_off += piece
        # 只有最后一段加淡出（避免重复）
        if pieces:
            try:
                pieces[-1].add_fade("0s", "0.7s")
            except ValueError:
                pass
        print(f"  BGM: 循环覆盖 0.00s ~ {total:.2f}s (素材 {bdur:.2f}s, 末段 0.7s 淡出)")

    # ---- 档案卡三轨（0~2s）----
    if a.card_zh:
        script.add_segment(seg(a.card_zh, SPEC["card_zh"], 0, CARD, FONT_ZH), "档案卡1")
    if a.card_en:
        script.add_segment(seg(a.card_en, SPEC["card_en"], 0, CARD, FONT_EN), "档案卡2")
    if a.card_name:
        script.add_segment(seg(a.card_name, SPEC["card_nm"], 0, CARD, FONT_ZH_B), "档案卡3")

    # ---- 中英字幕（同步切换；末句 0.3s 渐隐）----
    for i, s in enumerate(segs):
        zh_seg = seg(s["zh"], SPEC["zh"], s["t0"], s["dur"], FONT_ZH)
        en_seg = seg(s["en"], SPEC["en"], s["t0"], s["dur"], FONT_EN)
        if i == len(segs) - 1:
            try:
                zh_seg.add_animation(draft.TextOutro.渐隐, duration="0.3s")
                en_seg.add_animation(draft.TextOutro.渐隐, duration="0.3s")
            except Exception:
                pass
        script.add_segment(zh_seg, "中文字幕")
        script.add_segment(en_seg, "英文字幕")

    # ---- 引文竖排（末段 3s，50% 不透明）----
    if a.citation:
        # 引文轨道时长 = 它所在镜头的时长（用户要求）
        last = segs[-1]
        cite_seg = seg(a.citation, SPEC["cite"], last["t0"], last["dur"],
                       FONT_ZH, opacity=0.5)
        try:
            cite_seg.add_animation(draft.TextIntro.渐显, duration="0.3s")
            cite_seg.add_animation(draft.TextOutro.渐隐, duration="0.3s")
        except Exception:
            pass
        script.add_segment(cite_seg, "引文竖排")

    # ---- 两个 logo ----
    add_logos(script, segs, os.path.join(AUTO, "assets", "logo.png"), CARD)

    script.save()
    print(f"草稿已生成: {os.path.join(DRAFT_DIR, a.name)}")
    print(f"  镜头轨 {len(segs)+1} 段 / 配音轨 {len(segs)} 段 / 字幕轨 {len(segs)*2} 段")
    print(f"  档案卡 3 轨 / 引文 {'1' if a.citation else '0'} 轨 / BGM {'1' if os.path.exists(a.bgm) else '0'}")


if __name__ == "__main__":
    main()
