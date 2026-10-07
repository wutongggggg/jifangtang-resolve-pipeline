#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fcpxml_gen.py —— 把落点计划生成 FCPXML，交给达芬奇导入

为什么非要走 FCPXML（关键，别改回去）
------------------------------------
实测本机 Resolve Studio 21.0.1.11：**`AppendToTimeline` / `CreateTimelineFromClips`
放进去的片段不会持久化** —— 内存里能读到，`SaveProject()` 也返回 True，
但一切换项目就全空（`GetEndFrame()` 始终 108000，说明片段从未真正提交）。
`.drp` 导出回导入同样为空。已排除 TCC / 页面状态 / 外置盘 / 项目未登记 / 进程退化。

**`MediaPool.ImportTimelineFromFile(fcpxml)` 建出来的时间线则完全持久**，
而且在其片段上再用 API 挂 `AddFusionComp()` + Text+ **也持久**（实测文本读回正常）。
详见 `~/Public/DSH_HANDOFF_ERRATA.md` §3.6。

实测的轨道映射（多轨 FCPXML）
-----------------------------
    primary spine  -> video1
    lane="1"       -> video2
    lane="N"       -> video(N+1)
    lane="-1"      -> audio1
    lane="-2"      -> audio2

时间表示：全部用**精确分数秒**，避开帧基歧义：
    时间线上的位置/长度 -> N/30s     （时间线 30fps）
    源素材入点          -> M/50s     （源是 50p）
"""
import os
import re
from urllib.parse import quote
from xml.sax.saxutils import escape

W, H, FPS = 1080, 1920, 30.0

AUDIO_EXT = (".wav", ".mp3", ".aif", ".aiff", ".m4a", ".aac", ".flac")
IMG_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr", ".dpx")


_PROBE_CACHE = {}


def probe_media(path):
    """用 ffmpeg 探真实 w/h/fps。**必须按素材自己**的帧率算源入点**，
    不能靠路径猜（第一版用 "/assets/" 猜，换成中间片目录就全错了）。"""
    if path in _PROBE_CACHE:
        return _PROBE_CACHE[path]
    import re as _re
    import subprocess
    r = subprocess.run([(__import__("shutil").which("ffmpeg") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe" if os.name == "nt" else "ffmpeg")),
                        "-hide_banner", "-i", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    w = h = 0
    fps = 30.0
    m = _re.search(r"Video:.*?, (\d+)x(\d+)", r.stderr)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    m = _re.search(r"(\d+(?:\.\d+)?) fps", r.stderr)
    if m:
        fps = float(m.group(1))
    _PROBE_CACHE[path] = (w, h, fps)
    return w, h, fps


def _uri(path):
    return "file://" + quote(os.path.abspath(path))


def _sec(frames, fps):
    """frames/fps 的精确分数秒表示。"""
    frames = int(round(frames))
    return f"{frames}/{int(round(fps))}s"


def _kind(path):
    e = os.path.splitext(path)[1].lower()
    if e in AUDIO_EXT:
        return "audio"
    if e in IMG_EXT:
        return "image"
    return "video"


def build_fcpxml(acts, out_path, seq_frames, src_fps=50.0, project_name="dsh"):
    """acts: build_resolve5.plan_timeline() 的输出（含 frames / record / track）。

    返回 (out_path, stats)
    """
    # ---- 收集去重后的素材 ----
    def media_of(a):
        """取该 act 对应的媒体文件。

        坑：第一版只处理 shot/overlay/bgm/voice，漏了 **text**，
        结果文字 act 的载体路径没进 files，整批文字轨（档案卡/中英字幕/引文）
        被静默丢掉。统一走这一个函数，别再分支。
        """
        if a["kind"] == "shot":
            return a.get("clip")
        return a.get("path") or a.get("file")

    files, order = {}, []
    for a in acts:
        p = media_of(a)
        if p and p not in files:
            files[p] = f"r{len(files) + 2}"
            order.append(p)

    # ---- 轨道 -> lane ----
    def lane_of(track):
        if track.startswith("V"):
            n = int(track[1:])
            return None if n == 1 else n - 1        # V1 = primary spine
        if track.startswith("A"):
            return -int(track[1:])                  # A1 -> -1, A2 -> -2
        return None

    lines = []
    add = lines.append
    add('<?xml version="1.0" encoding="UTF-8"?>')
    add('<!DOCTYPE fcpxml>')
    add('<fcpxml version="1.9">')
    add('  <resources>')
    add(f'    <format id="r1" name="FFVideoFormat1080x1920p{int(FPS)}" '
        f'frameDuration="1/{int(FPS)}s" width="{W}" height="{H}"/>')
    # 每种 (宽,高,帧率) 一个 <format>；素材各自引用自己的
    fmt_of, fmt_ids = {}, {}
    for p in order:
        if _kind(p) == "audio":
            continue
        pw, ph, pfps = probe_media(p)
        key = (pw, ph, round(pfps, 3))
        if key == (W, H, round(FPS, 3)):
            fmt_ids.setdefault(key, "r1")      # 复用序列格式，避免同名重复
        if key not in fmt_ids:
            fid = f"rf{len(fmt_ids) + 1}"
            fmt_ids[key] = fid
            add(f'    <format id="{fid}" name="FFVideoFormat{pw}x{ph}p{int(round(pfps))}" '
                f'frameDuration="1/{int(round(pfps))}s" width="{pw}" height="{ph}"/>')
        fmt_of[p] = fmt_ids[key]

    for p in order:
        rid, k = files[p], _kind(p)
        nm = escape(os.path.basename(p))
        if k == "audio":
            add(f'    <asset id="{rid}" name="{nm}" start="0s" duration="20000/30s" '
                f'hasAudio="1" audioSources="1" audioChannels="1" audioRate="48000">')
        else:
            add(f'    <asset id="{rid}" name="{nm}" start="0s" duration="20000/30s" '
                f'hasVideo="1" format="{fmt_of[p]}">')
        add(f'      <media-rep kind="original-media" src="{_uri(p)}"/>')
        add('    </asset>')
    add('  </resources>')
    add('  <library>')
    add('    <event name="dsh">')
    add(f'      <project name="{escape(project_name)}">')
    add(f'        <sequence format="r1" duration="{_sec(seq_frames, FPS)}" '
        f'tcStart="0s" tcFormat="NDF">')
    add('          <spine>')

    n_v1 = n_lane = 0
    for a in sorted(acts, key=lambda x: (x["record"], x["track"])):
        p = media_of(a)
        if not p or p not in files:
            continue
        rid = files[p]
        # 文字/叠加/配音/bgm 只有 dur，没有 frames；统一按 30fps 折算
        fr = a.get("frames") or int(round(float(a["dur"]) * FPS))
        dur = _sec(fr, FPS)
        off = _sec(a["record"] * FPS, FPS)
        if a["kind"] == "shot":
            _pw, _ph, _pfps = probe_media(p)
            start = _sec(a.get("src_start", 0.0) * _pfps, _pfps)
        else:
            start = "0s"
        lane = lane_of(a["track"])
        nm = escape(str(a.get("label", a["track"]))[:60])
        lane_attr = "" if lane is None else f' lane="{lane}"'
        add(f'            <asset-clip ref="{rid}"{lane_attr} name="{nm}" '
            f'offset="{off}" duration="{dur}" start="{start}"/>')
        if lane is None:
            n_v1 += 1
        else:
            n_lane += 1
    add('          </spine>')
    add('        </sequence>')
    add('      </project>')
    add('    </event>')
    add('  </library>')
    add('</fcpxml>')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path, {"assets": len(order), "v1": n_v1, "lanes": n_lane}


