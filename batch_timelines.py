#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_timelines.py —— 12 章批量时间轴流水线（可扩到 1000 条）

与旧版（55 行、直接 subprocess 调 build_timeline.py）的区别：
本版把"分段 -> 时长 -> 排帧 -> 选镜 -> 规格自检 -> 写 JSON"收成一条可复用流水线，
并新增**离线时长预算**模式，在没有 TTS、没有外置盘的情况下也能先发现超长章。

  --plan-only   离线：不调 TTS、不读外置盘。用**第 01 章已实测的真配音**做一次
                线性回归，估算其余 11 章配音时长，输出每章 20s 预算表。
                （注意：audio/ 里的旧 wav 是 silenceremove 时代产物，偏短约 5%，不可用。）
  --chapter NN  真做一章：逐句 TTS -> 实测时长 -> 排帧 -> 选镜 -> 自检 -> 写 JSON
  --all         12 章全做

用法
    /opt/homebrew/bin/python3 batch_timelines.py --plan-only
    /opt/homebrew/bin/python3 batch_timelines.py --chapter 01
    /opt/homebrew/bin/python3 batch_timelines.py --all
    DSH_NO_TTS=1 ... --chapter 01     # 不调 TTS，只复用已有 wav 时长
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402
from build_timeline import split_sentences, split_zh_for_sentences  # noqa: E402

OUT = os.path.join(E.AUTO, "out")
CH_JSON = os.path.join(E.AUTO, "chapters.json")


def load_chapters():
    with open(CH_JSON, encoding="utf-8") as f:
        return json.load(f)["chapters"]


def load_media_map():
    with open(os.path.join(E.AUTO, "media_map.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------ 超宽中文字幕 -> 拆句
# 竖屏字幕安全宽 960px（画面 1080）。中文字幕**不折行**：
# 一行放不下就把这一"句"拆成两段（英文与镜头跟着一起拆），
# 因为拆段是在**分段阶段**做的，而 TTS 与时间轴都用本文件的 `segments_for()`，
# 所以"配音句数 = 字幕段数 = 镜头段数"这条不变式永远成立。
ZH_SPLIT_MAX_DEPTH = 2


def _zh_spec(quote):
    return "quote" if quote else "zh"


def _zh_width(text, quote=False):
    """中文一行在画面上的像素宽度；量不出来就按 em × 字数估算。"""
    try:
        import fontsel as F
        sp = E.SPEC[_zh_spec(quote)]
        em = E.px_size(sp["size"], sp["scale"])
        w = F.measure(sp["font"], text, em)
        return float(w) if w is not None else len(text) * em
    except Exception:
        return len(text) * 49.1          # 常规字幕 49.1px/字


def _split_zh(zh, quote=False):
    """把一段中文切成两半：在【，、；：—】处断，取"最长行最短"的切点。

    没有标点就按字数对半（保证任何一段都切得开）。
    """
    if len(zh) < 2:
        return None
    cands = [i + 1 for i, ch in enumerate(zh)
             if ch in "，、；：—" and i + 1 < len(zh)]
    cands = [i for i in cands if not (zh[i - 1] == "—" and zh[i] == "—")]
    if not cands:
        cands = [len(zh) // 2]
    best = None
    for i in cands:
        a, b = zh[:i].strip(), zh[i:].strip()
        if not a or not b:
            continue
        wa, wb = _zh_width(a, quote), _zh_width(b, quote)
        mm = max(wa, wb)
        key = (0 if mm <= E.SUB_SAFE_W else 1, mm, abs(len(a) - len(b)))
        if best is None or key < best[0]:
            best = (key, i)
    if best is None:
        return None
    i = best[1]
    return zh[:i].strip(), zh[i:].strip()


def _split_en(en, frac):
    """在子句边界（逗号/分号/冒号/破折号之后）切英文，切点尽量靠近 frac。

    没有子句边界就退到词边界。返回值保持原文大小写（不替用户改写文案）。
    """
    if len(en) < 2:
        return None
    pos = [i + 1 for i, ch in enumerate(en) if ch in ",;:—" and i + 1 < len(en)]
    if not pos:
        pos = [m.end() for m in re.finditer(r"\s", en)]
    pos = [i for i in pos if en[:i].strip() and en[i:].strip()]
    if not pos:
        return None
    target = max(1.0, min(len(en) - 1.0, frac * len(en)))
    i = min(pos, key=lambda x: abs(x - target))
    return en[:i].strip(), en[i:].strip()


def split_wide_segments(segs, max_depth=ZH_SPLIT_MAX_DEPTH):
    """中文字幕超安全宽 -> 拆成两句（英文同步拆；镜头由下游按段分配，自然拆成两个）。

    为什么要拆而不是折行（用户 2026-10-06 决定）：
    中文字幕折成两行会压住英文字幕（两轨基准间距只有 61px，而行高 62~70px），
    而"拆成两句"同时给出了配音锚点、字幕换行点和镜头切换点 —— 三者天然等长。
    """
    out = []

    def emit(seg, depth):
        quote = bool(seg.get("quote"))
        zh, en = (seg.get("zh") or ""), (seg.get("en") or "")
        if depth < max_depth and zh and _zh_width(zh, quote) > E.SUB_SAFE_W:
            zs = _split_zh(zh, quote)
            if zs:
                frac = len(zs[0]) / max(1, len(zh))
                es = _split_en(en, frac) if en else None
                if es is None:
                    es = (en, "")
                emit({"pair": seg["pair"], "en": es[0], "zh": zs[0], "quote": quote},
                     depth + 1)
                emit({"pair": seg["pair"], "en": es[1], "zh": zs[1], "quote": quote},
                     depth + 1)
                return
        out.append(dict(seg))

    for s in segs:
        emit(s, 0)
    for i, s in enumerate(out, 1):
        s["i"] = i
    return out


def segments_for(ch):
    """一章的 pairs -> 逐句 segments（中英一一对应，标出 quote 古文段）。

    超宽的中文句在这里就被拆开 —— 合成（synth_sentences）与排帧（build_one）
    都走这个函数，所以句数永远一致。
    """
    segs, i = [], 0
    for pi, p in enumerate(ch.get("pairs") or [], 1):
        en = re.sub(r"^Q\s+", "", (p.get("en") or "").strip())
        if not en:
            continue
        sents = split_sentences(en)
        zh_parts = split_zh_for_sentences(p.get("zh"), len(sents))
        for si, sent in enumerate(sents):
            i += 1
            # 必须带 pair：build_resolve5 的"段间 0.5s 闪黑"是按 pair 边界放的。
            # 漏掉它会导致闪黑整段消失（dry-run 抓到过这个回归）。
            segs.append({"i": i, "pair": pi,
                         "en": E.clean_subtitle(sent),
                         "zh": E.clean_subtitle(zh_parts[si] if si < len(zh_parts) else ""),
                         "quote": bool(p.get("quote"))})
    return split_wide_segments(segs)


# --------------------------------------------------------------- 时长估算
def calibrate():
    """用第 01 章**已实测的真配音**回归 dur = a*len(en) + b。

    ch01 的 voice_sent/*.wav 是 afade 修正之后的产物，时长可信
    （见 DSH_HANDOFF_ERRATA.md 第四节第 3 条）。
    """
    p = os.path.join(OUT, "timeline_ch01.json")
    if not os.path.exists(p):
        # 没有第 01 章实测时间轴可回归时，用经验语速（约 14.9 字符/秒）兜底。
        # 必须返回 4 个值 —— 调用处是 `ca, cb, cn, rms = calibrate()`。
        return 1 / 14.9, 0.0, 0, 0.0
    with open(p, encoding="utf-8") as f:
        tl = json.load(f)
    segs = [s for s in tl["segments"] if s.get("en") and s.get("dur")]
    xs = [len(s["en"]) for s in segs]
    ys = [float(s["dur"]) for s in segs]
    n = len(xs)
    if n < 2:
        return 1 / 14.9, 0.0, n, 0.3
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 1 / 14.9
    b = my - a * mx
    # 残差 RMS：作为估算不确定度，别把估算当实测
    rms = (sum((a * x + b - y) ** 2 for x, y in zip(xs, ys)) / n) ** 0.5
    return a, b, n, rms


def estimate_dur(en, a, b):
    return round(max(0.8, a * len(en) + b), 2)


# --------------------------------------------------------------- 计划表
def plan_rows(a, b):
    rows = []
    for ch in load_chapters():
        segs = segments_for(ch)
        if not segs:
            rows.append(dict(no=ch.get("no"), title=ch.get("title"),
                             code=ch.get("code"), n=0, voice=0.0, total=0.0,
                             ok=False, note="无 pairs"))
            continue
        voice = round(sum(estimate_dur(s["en"], a, b) for s in segs), 2)
        total = round(E.CARD_SEC + voice + E.GAP_SEC * (len(segs) - 1), 2)
        rows.append(dict(no=ch.get("no"), title=ch.get("title"), code=ch.get("code"),
                         n=len(segs), voice=voice, total=total,
                         ok=total <= E.TOTAL_MAX + 1e-9, note=""))
    return rows


def cmd_plan(a, b, cal_n, rms):
    rows = plan_rows(a, b)
    # 每章总长的不确定度 ~ sqrt(n 句) * 残差 RMS
    for r in rows:
        r["sigma"] = round(rms * (r["n"] ** 0.5), 2) if r["n"] else 0.0
    print("=== 12 章时长预算（离线估算）===")
    print(f"校准: 用第01章 {cal_n} 句**实测**配音回归 "
          f"dur = {a:.5f}*len(en) {b:+.3f}")
    print(f"      残差 RMS = {rms:.3f}s/句（估算不确定度来源）")
    print(f"规则: 档案卡 {E.CARD_SEC}s + 配音 + 句间 {E.GAP_SEC}s；上限 {E.TOTAL_MAX}s")
    print(f"注意: 这是**估算**，只用于提前发现超长章；真值以 TTS 实测为准\n")
    print(f"  {'章':<4}{'题名':<10}{'编号':<15}{'句':>3}{'配音':>7}{'总长':>7}"
          f"{'±':>6}  判定")
    bad, border = [], []
    for r in rows:
        if r["n"] == 0:
            print(f"  {r['no']:<4}{(r['title'] or ''):<10}{'':<15}{0:>3}"
                  f"{'':>7}{'':>7}{'':>6}  ⚠️ {r['note']}")
            continue
        t, s = r["total"], r["sigma"]
        if t - s > E.TOTAL_MAX:
            mark, bucket = "❌ 超", bad
        elif t + s > E.TOTAL_MAX:
            mark, bucket = "⚠️ 临界", border
        else:
            mark, bucket = "✅", None
        over = f"  超 {t - E.TOTAL_MAX:+.2f}s" if t > E.TOTAL_MAX else ""
        print(f"  {r['no']:<4}{(r['title'] or ''):<10}{(r['code'] or ''):<15}"
              f"{r['n']:>3}{r['voice']:>7.2f}{t:>7.2f}{s:>6.2f}  {mark}{over}")
        if bucket is not None:
            bucket.append(r)
    print()
    if bad:
        print(f"  ❌ {len(bad)} 章**确定**超过 {E.TOTAL_MAX}s（超出估算误差）：")
        for r in bad:
            print(f"     ch{r['no']} {r['title']}: {r['total']:.2f}s ± {r['sigma']:.2f}"
                  f"（超 {r['total'] - E.TOTAL_MAX:+.2f}s）")
    if border:
        print(f"  ⚠️ {len(border)} 章**临界**（估算区间跨过 {E.TOTAL_MAX}s，需实测确认）：")
        for r in border:
            print(f"     ch{r['no']} {r['title']}: {r['total']:.2f}s ± {r['sigma']:.2f}")
    if not bad and not border:
        print(f"  ✅ 全部 {len(rows)} 章预计都在 {E.TOTAL_MAX:.0f}s 内")
    if bad or border:
        print("\n     规格不允许压缩文案 / 加快语速 -> 需用户决定：")
        print("       a) 精简该章文案   b) 该章允许超过 20s   c) 减少句数")
    p = os.path.join(OUT, "plan_12ch.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"calibration": {"a": a, "b": b, "n": cal_n, "rms": rms,
                                   "source": "ch01 voice_sent 实测"},
                   "gap": E.GAP_SEC, "card_sec": E.CARD_SEC,
                   "total_max": E.TOTAL_MAX, "rows": rows},
                  f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n  已写入 {p}")
    return 1 if bad else 0


# --------------------------------------------------------------- 真做一章
def build_one(ch, voice, durations, synth_fn=None, write=True):
    """一章：分段 -> (TTS) -> 时长 -> 排帧 -> 选镜 -> 规格自检 -> 写 JSON。"""
    no = ch["no"]
    segs = segments_for(ch)
    if not segs:
        return None, [f"ch{no}: 无 pairs"], []
    for s in segs:
        wav = os.path.join(E.AUTO, "voice_sent", f"sentence_{s['i']:02d}.wav")
        if synth_fn is not None:
            s["dur"] = synth_fn(s["en"], wav)
        else:
            from build_timeline import dur_of
            s["dur"] = round(dur_of(wav), 3)
        s["wav"] = os.path.join("voice_sent", f"sentence_{s['i']:02d}.wav")
    total = E.lay_out(segs)
    mmap = load_media_map()
    mdir = mmap.get(no, "")
    cdurs = (durations or {}).get(no)
    if cdurs:
        ex = E.load_excluded(no)
        if ex:
            print(f"    ⛔ 排除不可用素材（便签纸）: {sorted(ex)}")
        plan, notes, starts = E.assign_shots(segs, cdurs, excluded=ex)
        for s, k, ss in zip(segs, plan, starts):
            s["clip_name"] = k
            s["clip"] = os.path.join(mdir, k)
            s["src_start"] = ss
        errs, warns = E.validate(
            {"chapter": no, "code": ch.get("code"), "card_sec": E.CARD_SEC,
             "gap": E.GAP_SEC, "total": total, "segments": segs}, cdurs)
        warns = notes + warns
    else:
        errs, warns = [], [f"ch{no}: 无素材时长缓存，镜头未分配"
                           f"（跑 timeline_tool.py --refresh-durations）"]
    tl = {"chapter": no, "code": ch.get("code"), "title": ch.get("title"),
          "source": ch.get("source"), "card_sec": E.CARD_SEC, "gap": E.GAP_SEC,
          "total": total, "media_dir": mdir, "segments": segs}
    if write:
        p = os.path.join(OUT, f"timeline_ch{no}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(tl, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return tl, errs, warns


def cmd_real(chapters, voice, only=None):
    durations = E.load_durations().get("chapters", {})
    synth_fn = None
    if os.environ.get("DSH_NO_TTS") != "1":
        try:
            import synth as _synth

            def synth_fn(text, wav):
                _synth.synth(text, voice, wav)
                return round(_synth.dur_of(wav), 3)
        except Exception as e:
            print(f"  ⚠️ 载入 synth 失败（改用已有 wav 时长）: {e}")
    rc = 0
    for ch in chapters:
        if only and ch.get("no") != only:
            continue
        print(f"\n=== 第 {ch['no']} 章 {ch.get('title')} ===")
        tl, errs, warns = build_one(ch, voice, durations, synth_fn)
        if tl is None:
            print("  ⚠️ " + "; ".join(errs))
            continue
        segs = tl["segments"]
        lim = E.total_max_for(ch.get("no"))
        mark = ("✅" if (lim is None or tl["total"] <= lim)
                else "❌ 超长")
        if lim is None and tl["total"] > E.TOTAL_MAX:
            mark = f"⚠️ 超 {E.TOTAL_MAX}s（该章已获批准超时）"
        print(f"  {len(segs)} 句  配音 {sum(s['dur'] for s in segs):.2f}s  "
              f"总长 {tl['total']:.2f}s  {mark}")
        for e in errs:
            print(f"  ❌ {e}")
            rc = 1
        for w in warns:
            print(f"  ⚠️  {w}")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--chapter", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--voice", default="09f8bb55")
    a = ap.parse_args()
    if a.plan_only:
        ca, cb, cn, rms = calibrate()
        return cmd_plan(ca, cb, cn, rms)
    chs = load_chapters()
    if a.all:
        return cmd_real(chs, a.voice)
    if a.chapter:
        return cmd_real(chs, a.voice, only=a.chapter)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
