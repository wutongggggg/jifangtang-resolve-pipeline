#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按"句"重建配音+字幕+镜头时间轴（用户指定的正确逻辑）。

流程:
  1. 完美配音: 把每个句子单独合成 -> 去掉头尾静音 -> 句间只留 0.15s 呼吸
  2. 字幕切分: 一行英文 = 一行中文, 中英【同步切换】
  3. 镜头匹配: 镜头可横跨一个或多个字幕小段

输出:
  timeline.json  —— 每段: {en, zh, t0, dur, clip, src_start, ...}
  以及合成好的逐句配音 wav（sentence_XX.wav）
"""
import json
import os
import re
import subprocess
import sys

FFMPEG = (__import__("shutil").which("ffmpeg") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
CARD_SEC = 2.0
GAP = 0.15           # 句间自然呼吸
SENT_LIMIT = 62      # 英文超过这个字符数才在 ; — 处再断（避免碎句）


def dur_of(path):
    """读时长。WAV 直接读头（最准），其他格式回退到 ffmpeg。"""
    if path.lower().endswith(".wav"):
        try:
            import wave
            with wave.open(path, "rb") as w:
                return round(w.getnframes() / float(w.getframerate()), 3)
        except Exception:
            pass
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                       capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
    return round(int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3)), 3) if m else 0.0


def synth(text, voice, out):
    """调 VoiceStudio 合成一句（含去掉头尾静音）。"""
    import urllib.request, urllib.error, time
    payload = json.dumps({"model": "tts-1", "voice": voice, "input": text,
                          "response_format": "wav", "language": "en"}).encode()
    for attempt in range(4):
        req = urllib.request.Request("http://127.0.0.1:3900/v1/audio/speech",
                                     data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            if len(data) > 2000:
                open(out, "wb").write(data)
                trim(out)
                return dur_of(out)   # trim 之后再测, 保证登记值=文件实际值
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"合成失败: {text[:40]}")


def trim(path, lead=0.06, tail=0.10, thresh=-35):
    """不再用 silenceremove —— 它会在静音边界产生**阶跃不连续**（爆音，听感像句尾"off"）。

    实测证据: 同一句原始输出 尾部峰值 0.035 / RMS 0.0037,
    silenceremove 处理后变成 0.511 / 0.0785（放大 14 倍）= 明显爆音。

    改为只加**淡入淡出**，既去掉头尾瑕疵又不产生不连续:
      - 60ms 淡入（消除起始 click）
      - 100ms 淡出（消除收尾 click）
    """
    import subprocess, os
    tmp = path + ".fade.wav"
    af = (f"afade=t=in:st=0:d={lead},"
          f"areverse,afade=t=in:st=0:d={tail},areverse")
    r = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", path, "-af", af, "-c:a", "pcm_s16le", tmp],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
        os.replace(tmp, path)
    elif os.path.exists(tmp):
        os.remove(tmp)
    return True


def split_sentences(text):
    """按句末标点切句；过长的再在分号/破折号处断。"""
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text.strip()) if p.strip()]
    out = []
    for p in parts:
        if len(p) <= SENT_LIMIT:
            out.append(p); continue
        sub = [x.strip() for x in re.split(r'(?<=[;—])\s+', p) if x.strip()]
        cur = ""
        for x in sub:
            if cur and len(cur) + len(x) + 1 > SENT_LIMIT:
                out.append(cur); cur = x
            else:
                cur = (cur + " " + x).strip()
        if cur:
            out.append(cur)
    return out




def split_zh_for_sentences(zh, n_sent):
    """把一段中文切成 n_sent 份，与英文句一一对应。

    关键经验（踩坑后修正）:
      - 中文段落的 `。` 通常只在末尾出现, 不能当切点, 否则会切出"患。"这种碎片。
      - 只在【，、；：—】这类句中标点处切, 并把句末 `。！？` 留在最后一片。
      - 若标点不够, 按字符比例切, 但保证每片 >= 4 字（避免单字碎片）。
    """
    import re as _re
    if not zh:
        return [""] * max(1, n_sent)
    if n_sent <= 1:
        return [zh]
    # 中文太短就整体复用（避免切出"生于忧"/"患。"这类碎片）
    if len(zh) < n_sent * 5:
        return [zh] * n_sent
    # 句中标点作为候选切点（不含句末标点）
    units = [u for u in _re.split(r'(?<=[，、；：—])', zh) if u]
    if len(units) >= n_sent:
        out, per = [], len(units) / n_sent
        for i in range(n_sent):
            a, b = int(round(i*per)), int(round((i+1)*per))
            out.append("".join(units[a:b]) or units[min(a, len(units)-1)])
        return out
    # 标点不够：按字符比例切，但用"最近标点"微调切点，避免切在字中间
    n = len(zh)
    cuts = [i for i, ch in enumerate(zh) if ch in "，、；：—"]
    out, prev = [], 0
    for i in range(1, n_sent):
        target = int(i * n / n_sent)
        # 找离目标最近的标点（在 prev+2 之后，保证前一片不空）
        cand = [c for c in cuts if c >= prev + 3]
        c = min(cand, key=lambda x: abs(x - target)) if cand else target
        out.append(zh[prev:c+1] if c < n else zh[prev:])
        prev = c + 1
    out.append(zh[prev:])
    return [x for x in out if x] or [zh]


def build(pairs, voice, outdir):
    """pairs: [{'zh':..,'en':..}]  ->  逐句时间轴"""
    os.makedirs(outdir, exist_ok=True)
    segs, t = [], CARD_SEC
    idx = 0
    for p in pairs:
        en = (p.get("en") or "").strip()
        if not en:
            continue
        sents = split_sentences(en)
        zh_parts = split_zh_for_sentences(p.get("zh"), len(sents))
        for si, sent in enumerate(sents):
            idx += 1
            wav = os.path.join(outdir, f"sentence_{idx:02d}.wav")
            sent = re.sub(r"^Q\\s+", "", sent)
            synth(sent, voice, wav)
            d = dur_of(wav)          # 合成后立即实测（trim 已在 synth 内完成）
            segs.append({"i": idx, "en": sent, "zh": zh_parts[si],
                         "wav": wav, "dur": d, "t0": round(t, 3)})
            print(f"  [{idx:02d}] {t:6.2f}s +{d:.2f}s  {sent[:56]}")
            t += d + GAP
    return segs, round(t - GAP, 3)


def assign_clips(segs, clips):
    """镜头可横跨多个字幕小段：轮转分配，每 N 段换一个镜头。"""
    pool = sorted(clips, key=lambda c: c["dur"])
    total = sum(s["dur"] + GAP for s in segs)
    n_clip = max(1, min(len(pool), round(total / 3.5)))   # 平均每 3.5s 一个镜头
    per = max(1, len(segs) // n_clip)
    used, k = [], 0
    for i, s in enumerate(segs):
        if i % per == 0 and k < len(pool):
            k += 1
        c = pool[min(k - 1, len(pool) - 1)]
        s["clip"] = c["file"]; s["clip_name"] = c["name"]
        t_in = sum(x["dur"] + GAP for x in segs[:i]) % max(0.5, c["dur"] - s["dur"])
        s["src_start"] = round(t_in, 3)
    return segs


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", default="01")
    ap.add_argument("--voice", default="09f8bb55")
    ap.add_argument("--chapters", default="chapters.json")
    ap.add_argument("--media", required=True)
    ap.add_argument("--out", default="timeline.json")
    a = ap.parse_args()
    doc = json.load(open(a.chapters, encoding="utf-8"))
    ch = next(c for c in doc["chapters"] if c["no"] == a.chapter)
    print(f"[1/3] 逐句配音（去静音, 句间 {GAP}s）")
    segs, total = build(ch["pairs"], a.voice, "voice_sent")
    print(f"[2/3] 总时长 {total:.2f}s / {len(segs)} 句")
    clips = [{"file": os.path.join(a.media, f), "name": f, "dur": dur_of(os.path.join(a.media, f))}
             for f in sorted(os.listdir(a.media))
             if f.upper().endswith(".MP4") and not f.startswith("._")]
    segs = assign_clips(segs, clips)
    print(f"[3/3] 镜头分配: {len(set(s['clip_name'] for s in segs))} 条素材")
    json.dump({"chapter": a.chapter, "card_sec": CARD_SEC, "gap": GAP,
               "total": total, "segments": segs},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"-> {a.out}")
