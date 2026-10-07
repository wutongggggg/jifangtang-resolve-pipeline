#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prep_media.py —— 预渲染叠加层素材（不碰达芬奇，可离线跑可验证）

为什么需要这一步
----------------
达芬奇脚本 API **没有关键帧 API、没有转场 API**（见 DSH_HANDOFF_ERRATA.md 第三节）。
所以这些"随时间变化"的东西没法用 API 写进时间线：

    段间 0.5s 闪黑 / 视频末段 0.3s 渐隐 / 小 logo 0.5s 渐隐 / 大 logo 0.3s 渐现
    BGM 循环 + 末尾 0.7s 淡出

本脚本用 ffmpeg 把它们**预先渲成素材**，再用 `AppendToTimeline(recordFrame=...)`
精确放到对应轨道与帧位。这样：
  - 时间线仍然是达芬奇工程（不是成片），V1 仍是 4K 原始素材非破坏性裁剪；
  - 只有"叠加层"是预渲染的，且**每个都是全画幅 1080x1920**，
    放进达芬奇不需要任何缩放/位移，位置完全由 ffmpeg 像素级确定。

闪黑/渐隐的实现
---------------
用**不透明白→黑渐变 + 乘法合成**（`CompositeMode = COMPOSITE_MULTIPLY`）：
乘法里"乘 1 = 不变、乘 0 = 全黑"，所以白→黑渐变就是"渐隐到黑"。
比带 alpha 的 ProRes 4444 更稳（无 alpha/色彩管理坑），且亮度曲线就是线性淡出。

产出（都在 assets/）
    blank.png        1080x1920 全透明，作文字轨载体
    bgm_bed.wav      BGM 循环铺满总长 + 末尾 0.7s 淡出
    logo_small.mov   全画幅 alpha，0~2s，末 0.5s 渐隐
    logo_big.mov     全画幅 alpha，3s，前 0.3s 渐现
    ramp_up.mov      1s 不透明 白→黑（乘法 = 渐隐到黑）
    ramp_down.mov    1s 不透明 黑→白（乘法 = 从黑渐现）

用法
    /opt/homebrew/bin/python3 prep_media.py --timeline out/timeline_ch01.json
    /opt/homebrew/bin/python3 prep_media.py --timeline ... --logo-scale-small 19 --logo-scale-big 88
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402


def run(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    if r.returncode:
        print(f"  ❌ {label} 失败:\n{r.stderr[-600:]}")
        raise SystemExit(1)
    print(f"  ✅ {label}")


def probe_duration(path):
    r = subprocess.run([E.FFMPEG, "-hide_banner", "-i", path],
                       capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0


def png_size(path):
    """不依赖 PIL 读 PNG 宽高（IHDR）。"""
    with open(path, "rb") as f:
        d = f.read(26)
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} 不是 PNG")
    return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")


# ---------------------------------------------------------------------
def make_blank_mov(out, dur=30.0):
    """全透明 ProRes 4444 视频载体（**文字轨必须用它，不能用 PNG 静帧**）。

    实测（resolve_probe4_report.txt）：达芬奇里**静帧的 endFrame 不起作用**，
    永远按项目的"标准静帧时长"给 150 帧 = 5.00s；而**视频片段**的
    endFrame 精确可控（30/90/45 帧都分毫不差）。所以文字轨用这个透明视频当载体，
    才能让每条字幕/档案卡拿到自己的精确时长。
    """
    run([E.FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"color=c=black@0.0:s={E.W}x{E.H}:r={int(E.FPS)}:d={dur:.3f},format=rgba",
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
         "-t", f"{dur:.3f}", out],
        f"blank_alpha.mov 全透明视频载体 {dur:.0f}s（文字轨用）")


def make_blank(out):
    run([E.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=black@0.0:s={E.W}x{E.H},format=rgba",
         "-frames:v", "1", out], f"blank.png {E.W}x{E.H} 全透明")


def make_bgm(out, total, fade_out=E.FADE_BGM_OUT):
    src = E.BGM
    if not os.path.exists(src):
        print(f"  ⚠️ BGM 不存在，跳过: {src}")
        return
    bd = probe_duration(src)
    st = max(0.0, total - fade_out)
    run([E.FFMPEG, "-y", "-v", "error", "-stream_loop", "-1", "-i", src,
         "-t", f"{total:.3f}",
         "-af", f"afade=t=out:st={st:.3f}:d={fade_out:.3f}",
         "-ar", "48000", out],
        f"bgm_bed.wav  循环 {bd:.2f}s -> {total:.2f}s，末 {fade_out}s 淡出")


def make_logo(out, logo, png_w, scale_pct, jx, jy, dur, mode,
              fade_in=0.0, fade_out=0.0):
    """把 logo 合成到全画幅 alpha 画面，中心对齐 (jx,jy) 的换算像素点。

    mode='doc'   : 渲染宽 = 画布宽 x (logo_png_w/画布宽) x scale_pct/100   （交接文档 §5.4 公式）
    mode='canvas': 渲染宽 = 画布宽 x scale_pct/100                          （占画布百分比）
    """
    if not os.path.exists(logo):
        print(f"  ⚠️ logo 不存在，跳过: {logo}")
        return
    lw, lh = png_size(logo)
    if mode == "doc":
        scale = (lw / float(E.W)) * (scale_pct / 100.0)
    else:
        scale = scale_pct / 100.0
    tw = max(2, int(round(E.W * scale)))
    th = max(2, int(round(lh * tw / float(lw))))
    cx, cy = E.px_x(jx), E.px_y(jy)
    ox, oy = int(round(cx - tw / 2.0)), int(round(cy - th / 2.0))

    fades = []
    if fade_in > 0:
        fades.append(f"fade=t=in:st=0:d={fade_in:.3f}:alpha=1")
    if fade_out > 0:
        fades.append(f"fade=t=out:st={max(0.0, dur - fade_out):.3f}:"
                     f"d={fade_out:.3f}:alpha=1")

    fc = (f"[0:v]format=rgba[bg];"
          f"[1:v]scale={tw}:{th}:flags=lanczos,format=rgba[lg];"
          f"[bg][lg]overlay={ox}:{oy}:format=auto[v];")
    fc += ("[v]" + ",".join(fades) + "[out]") if fades else "[v]null[out]"
    run([E.FFMPEG, "-y", "-v", "error",
         # 关键：`,format=rgba` 必须写进 lavfi 输入串。若只在 filter_complex 里
         # 写 format=rgba，ffmpeg 会在输入边界就把像素格式定成无 alpha 的
         # yuv420p，透明度直接丢失 -> 背景变成不透明黑，放上轨会把画面全遮住。
         "-f", "lavfi", "-i",
         f"color=c=black@0.0:s={E.W}x{E.H}:r={int(E.FPS)}:d={dur:.3f},format=rgba",
         "-i", logo,
         "-filter_complex", fc, "-map", "[out]",
         "-t", f"{dur:.3f}",
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
         "-r", str(int(E.FPS)), out],
        f"{os.path.basename(out)}  png {lw}x{lh} -> {tw}x{th} @({ox},{oy}) "
        f"中心({cx:.0f},{cy:.0f}) {dur:.2f}s")


def make_ramp(out, direction, frames):
    """不透明「白→黑」(up) / 「黑→白」(down)，**时长精确等于 frames 帧**。

    必须按帧生成：拿 1s 长渐变去裁 0.25s，起点亮度已经是 25%，
    画面一开始就偏暗，不是从全亮开始淡出。

    fade=t=out = 从源渐隐到黑；fade=t=in = 从黑渐显到源。
    两者都取**白色**作源，才能分别得到 白→黑 与 黑→白。
    """
    dur = frames / E.FPS
    if direction == "up":
        vf = f"fade=t=out:st=0:d={dur:.4f}"
        label = f"{os.path.basename(out)} 白→黑（乘法=渐隐到黑）{frames}帧"
    else:
        vf = f"fade=t=in:st=0:d={dur:.4f}"
        label = f"{os.path.basename(out)} 黑→白（乘法=从黑渐现）{frames}帧"
    run([E.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=white:s={E.W}x{E.H}:r={int(E.FPS)}:d={dur:.4f}",
         "-vf", f"{vf},format=yuv420p",
         "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", out], label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    # 用户指定：/Users/geophyto/Desktop/成片/logo.png（1658x904）
    # 与交接文档 §5.4 指的是**同一个文件**（sha256 相同）；
    # 项目里原来那个 assets/logo.png 是 462x274 的**另一张图**，已备份为 logo_462.png。
    ap.add_argument("--logo", default=os.path.join(E.AUTO, "assets", "logo.png"))
    ap.add_argument("--logo-mode", choices=["doc", "canvas"], default="doc")
    ap.add_argument("--logo-scale-small", type=float, default=E.LOGO_SPEC["small"]["scale_pct"])
    ap.add_argument("--logo-scale-big", type=float, default=E.LOGO_SPEC["big"]["scale_pct"])
    ap.add_argument("--assets", default=os.path.join(E.AUTO, "assets"))
    a = ap.parse_args()

    with open(a.timeline, encoding="utf-8") as f:
        tl = json.load(f)
    total = tl["total"]
    os.makedirs(a.assets, exist_ok=True)
    print(f"=== 预渲染叠加层（总长 {total:.2f}s）===")
    print(f"  logo 源: {a.logo}")
    print(f"  logo 缩放模式: {a.logo_mode}")

    make_blank(os.path.join(a.assets, "blank.png"))
    make_blank_mov(os.path.join(a.assets, "blank_alpha.mov"), 30.0)
    make_bgm(os.path.join(a.assets, "bgm_bed.wav"), total)

    s = E.LOGO_SPEC["small"]
    make_logo(os.path.join(a.assets, "logo_small.mov"), a.logo, 0, a.logo_scale_small,
              s["jx"], s["jy"], s["dur"], a.logo_mode, fade_out=s["fade_out"])
    b = E.LOGO_SPEC["big"]
    # 大 logo **与最后一个镜头等长**（不再固定 3s 横跨几个镜头）
    segs = tl.get("segments") or []
    last_f = int(segs[-1].get("frames") or round(E.CARD_SEC * E.FPS)) if segs else 60
    bframes, bdur, bfade = E.logo_big_geom(last_f)
    bname = E.logo_big_name(bframes)
    make_logo(os.path.join(a.assets, bname), a.logo, 0, a.logo_scale_big,
              b["jx"], b["jy"], bdur, a.logo_mode, fade_in=bfade)
    print(f"  ℹ️ 大 logo 与末镜等长: {bframes} 帧/{bdur:.2f}s，淡入 {bfade:.2f}s -> {bname}")

    make_ramp(os.path.join(a.assets, E.ramp_name("up", E.FLASH_OUT_FRAMES)),
              "up", E.FLASH_OUT_FRAMES)
    make_ramp(os.path.join(a.assets, E.ramp_name("down", E.FLASH_IN_FRAMES)),
              "down", E.FLASH_IN_FRAMES)
    make_ramp(os.path.join(a.assets, E.ramp_name("up", E.FADE_VIDEO_TAIL_FRAMES)),
              "up", E.FADE_VIDEO_TAIL_FRAMES)

    names = ["blank.png", "blank_alpha.mov", "bgm_bed.wav", "logo_small.mov", bname,
             E.ramp_name("up", E.FLASH_OUT_FRAMES),
             E.ramp_name("down", E.FLASH_IN_FRAMES),
             E.ramp_name("up", E.FADE_VIDEO_TAIL_FRAMES)]
    print("\n=== 产物 ===")
    for n in names:
        p = os.path.join(a.assets, n)
        if os.path.exists(p):
            print(f"  {n:<20} {os.path.getsize(p)/1024:9.1f} KB  "
                  f"{probe_duration(p):.4f}s")


if __name__ == "__main__":
    main()
