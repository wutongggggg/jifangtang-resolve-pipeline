#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 chapters.json 的英文旁白送进 VoiceStudio 合成配音。

用法:
    python3 synth.py chapters.json out_audio/ [--only 01] [--voice demo0001]

输出:
    out_audio/ch01_seg1.wav ...   每句一个 wav
    out_audio/ch01.manifest.json  每段时长/文本, 供时间轴使用

要点:
- 逐段合成(不整章一次) —— 段落时长天然给出"字幕/镜头切换"的锚点, 省掉强制对齐。
- sidecar 首次加载模型要 ~80s, 会撞上 backend 的 60s 接收超时 -> 自动重试。
- 串行调用(单 GPU 池), 并在请求间留间隔。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("VOICESTUDIO_API", "http://127.0.0.1:3900/v1/audio/speech")

# 音色 ID —— **别再写 demo0001**！
# demo0001 是 VoiceStudio 自带的演示女声（voice_profiles 里 is_demo=1），
# 用它配出来的是默认女声，不是用户的"寄方堂"音色。
# 用户的音色是 09f8bb55（名"寄方堂2"）：参考音频 voice_ref.mp3（= 桌面成片/9月3日.mp3），
# 并且**带上了参考转写**（ref_text 217 字，与 voice/参考音频转写.json 一致）——
# 零样本克隆里转写很关键，所以不用同为 15s 音频但没转写的 a29f33f3（"寄方堂"）。
# 查全部音色：sqlite3 "~/Library/Application Support/OmniVoice/omnivoice.db" \
#     "select id,name,length(ref_text) from voice_profiles;"
VOICE_ID = os.environ.get("JIFANGTANG_VOICE", "09f8bb55")
FFMPEG_BIN = (__import__("shutil").which("ffmpeg") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe" if os.name == "nt" else "ffmpeg"))


def synth(text: str, voice: str, out_path: str, tries: int = 4, timeout: int = 300) -> float:
    """调用 VoiceStudio 合成一段, 返回时长(秒)。"""
    payload = json.dumps({
        "model": "tts-1", "voice": voice, "input": text,
        "response_format": "wav", "language": "en",
    }).encode("utf-8")
    last = ""
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(API, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) > 2000:
                with open(out_path, "wb") as f:
                    f.write(data)
                return probe_duration(out_path)
            last = data[:200].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:200].decode('utf-8','replace')}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < tries:
            time.sleep(4)
    raise RuntimeError(f"合成失败({tries}次): {last}")


def probe_duration(path: str) -> float:
    import re as _re
    r = subprocess.run([FFMPEG_BIN, "-hide_banner", "-i", path],
                       capture_output=True, text=True)
    m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        return 0.0
    return round(int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3)), 3)




def trim_silence(wav_path, lead=0.05, tail=0.08, thresh_db=-42):
    """去掉 TTS 输出自带的头尾静音，两端各保留少量呼吸间隙。

    这是"配音断断续续"的根因: 每段 TTS 都在头尾带 0.3~1s 静音,
    直接拼接后句与句之间就会出现明显停顿。
    """
    import subprocess, os
    tmp = wav_path + ".trim.wav"
    af = (f"silenceremove=start_periods=1:start_silence={lead}:start_threshold={thresh_db}dB,"
          f"areverse,"
          f"silenceremove=start_periods=1:start_silence={tail}:start_threshold={thresh_db}dB,"
          f"areverse")
    r = subprocess.run([FFMPEG_BIN, "-y", "-hide_banner",
                        "-loglevel", "error", "-i", wav_path, "-af", af,
                        "-c:a", "pcm_s16le", tmp],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
        os.replace(tmp, wav_path)
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def plan_segments(pairs, target_s: float = 8.0):
    """把英文旁白切成"镜头级"段。

    实测 demo 音色语速 ≈ 14 字符/秒(17.58s / 250 字符)。
    优先在句末(. ! ?)断, 其次分号/破折号, 最后才用逗号;
    整段不超过 target_s 就不切 —— 切点即"字幕换行 + 镜头切换"点。
    """
    segs = []
    for p in pairs:
        txt = (p["en"] or "").strip()
        if not txt:
            continue
        parts = _split_natural(txt, target_s)
        for k, s in enumerate(parts):
            segs.append({"en": s, "zh": p["zh"] if k == 0 else None,
                         "part": k, "n_parts": len(parts),
                         "quote": p.get("quote", False), "src_line": txt})
    return segs


def _split_natural(text: str, target_s: float, cps: float = 14.0):
    """按自然停顿切分; 只在超长时才切, 且优先在高级别标点处断。"""
    import re
    limit = int(target_s * cps)
    if len(text) <= limit:
        return [text]
    # (正则, 权重) 权重越小越优先
    tiers = [(r"[.!?]\s", 0), (r"[;—]\s", 1), (r",\s", 2), (r"\s", 3)]
    out, start = [], 0
    while len(text) - start > limit:
        best = None
        for pat, w in tiers:
            cands = [m.end() for m in re.finditer(pat, text[start:])]
            cands = [start + c for c in cands if start + c > start + limit * 0.5]
            if cands:
                c = min(cands, key=lambda x: (w, abs(x - start - limit)))
                if best is None or (w, abs(c - start - limit)) < (best[0], abs(best[1] - start - limit)):
                    best = (w, c)
        if best is None:
            break
        out.append(text[start:best[1]].strip())
        start = best[1]
    out.append(text[start:].strip())
    return [s for s in out if s]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chapters", nargs="?", default="chapters.json")
    ap.add_argument("outdir", nargs="?", default="out_audio")
    ap.add_argument("--only", default=None, help="只做某一章, 如 01")
    ap.add_argument("--voice", default=VOICE_ID)
    ap.add_argument("--gap", type=float, default=1.5, help="请求间隔秒")
    args = ap.parse_args()

    doc = json.load(open(args.chapters, encoding="utf-8"))
    os.makedirs(args.outdir, exist_ok=True)
    chapters = doc["chapters"]
    if args.only:
        chapters = [c for c in chapters if c["no"] == args.only]
    grand = 0.0
    for ch in chapters:
        segs = plan_segments(ch["pairs"])
        manifest, t0 = [], time.time()
        for i, seg in enumerate(segs, 1):
            wav = os.path.join(args.outdir, f"ch{ch['no']}_seg{i}.wav")
            dur = synth(seg["en"], args.voice, wav)
            trim_silence(wav)
            dur = probe_duration(wav)
            manifest.append({**seg, "index": i, "wav": os.path.abspath(wav), "dur": dur})
            print(f"  ch{ch['no']} seg{i}/{len(segs)} {dur:6.2f}s  {seg['en'][:58]}")
            sys.stdout.flush()
            if i < len(segs):
                time.sleep(args.gap)
        total = sum(m["dur"] for m in manifest)
        grand += total
        mf = os.path.join(args.outdir, f"ch{ch['no']}.manifest.json")
        json.dump({"no": ch["no"], "code": ch["code"], "title": ch["title"],
                   "source": ch["source"], "segments": manifest,
                   "audio_total": round(total, 2), "wall": round(time.time() - t0, 1)},
                  open(mf, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ch{ch['no']}] {len(segs)} 段, 配音总长 {total:.2f}s -> {mf}")
    print(f"完成, 配音总时长 {grand:.2f}s")


if __name__ == "__main__":
    main()
