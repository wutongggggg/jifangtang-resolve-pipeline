#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timeline_tool.py —— 时间轴离线工具：校验 / 修正 / 重建

在**不启动达芬奇、不读外置盘**的前提下，把时间轴数据修到符合规格。

  # 只自检
  python3 timeline_tool.py out/timeline_ch01.json --check

  # 修正后写回（重排 t0、重算 total、重选镜头、清理切句残留破折号、标注 quote）
  python3 timeline_tool.py out/timeline_ch01.json --fix

  # 权限恢复后，从原素材重新实测时长
  python3 timeline_tool.py out/timeline_ch01.json --refresh-durations
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

PUNCT = re.compile(r"[\s，。、；：！？,.;:!?\"'“”‘’（）()《》〈〉·—–\-]")


def norm(s):
    return PUNCT.sub("", s or "")


def load_chapter(code=None, no=None):
    """chapters.json 里的章数据。优先按 no（'01'）匹配，其次按 code（'20260626-39'）。"""
    p = os.path.join(E.AUTO, "chapters.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    chs = doc.get("chapters") or []
    if no is not None:
        for c in chs:
            if str(c.get("no")) == str(no):
                return c
    if code:
        want = code.replace("～", "-").replace("~", "-").strip()
        for c in chs:
            if str(c.get("code", "")).replace("～", "-").replace("~", "-") == want:
                return c
    return None


def code_from_media_dir(d):
    """'/Volumes/备份盘/20260626/20260626～39' -> '20260626-39'"""
    if not d:
        return ""
    return os.path.basename(d.rstrip("/")).replace("～", "-").replace("~", "-")


def tag_pairs(segs, chapter):
    """把每个 segment 关联到 chapters.json 的 pair，标出 quote（古文段）。"""
    pairs = (chapter or {}).get("pairs") or []
    if not pairs:
        return 0
    hits = 0
    for s in segs:
        sn = norm(s.get("zh", ""))
        if not sn:
            continue
        for i, pr in enumerate(pairs):
            pn = norm(pr.get("zh", ""))
            if not pn:
                continue
            if sn in pn or pn in sn or sn == pn:
                s["pair"] = i + 1
                s["quote"] = bool(pr.get("quote"))
                hits += 1
                break
    return hits


def cmd_check(tl, args):
    durs = E.load_durations()
    chapter_durs = (durs.get("chapters") or {}).get(tl.get("chapter"), {})
    errs, warns = E.validate(tl, chapter_durs or None)
    print(f"=== 自检 {args.timeline} (第{tl.get('chapter')}章) ===")
    segs = tl.get("segments", [])
    print(f"  句数 {len(segs)}   档案卡 {tl.get('card_sec')}s   停顿 {tl.get('gap')}s")
    print(f"  配音合计 {E.voice_total(segs):.2f}s   "
          f"登记总长 {tl.get('total')}s   实际总长 {E.timeline_total(segs, tl.get('card_sec', E.CARD_SEC), tl.get('gap', E.GAP_SEC)):.2f}s")
    lim = E.total_max_for(tl.get("chapter"))
    actual = E.timeline_total(segs, tl.get("card_sec", E.CARD_SEC),
                              tl.get("gap", E.GAP_SEC))
    if lim is None:
        verdict = ("✅ 合规" if actual <= E.TOTAL_MAX
                   else f"⚠️ 超 {E.TOTAL_MAX}s 上限（该章已获批准超时，不拦）")
        lim_txt = f"{E.TOTAL_MAX}（第{tl.get('chapter')}章豁免）"
    else:
        verdict = "✅ 合规" if actual <= lim else "❌ 超长"
        lim_txt = f"{lim}"
    print(f"  上限 {lim_txt}s -> {verdict}")
    for e in errs:
        print(f"  ❌ {e}")
    for w in warns:
        print(f"  ⚠️  {w}")
    if not errs and not warns:
        print("  ✅ 无问题")
    return 1 if errs else 0


def cmd_fix(tl, args):
    segs = tl["segments"]
    # 0) 补 media_dir（原素材目录），供重建 clip 绝对路径
    if not tl.get("media_dir"):
        mp = os.path.join(E.AUTO, "media_map.json")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as f:
                tl["media_dir"] = json.load(f).get(tl.get("chapter"), "")
    # 1) 档案卡 / 停顿 / 总长
    tl["card_sec"] = E.CARD_SEC
    tl["gap"] = E.GAP_SEC
    total = E.lay_out(segs, tl["card_sec"], tl["gap"])
    tl["total"] = total

    # 2) 字幕清理（去掉切句残留的悬空破折号）
    for s in segs:
        for k in ("zh", "en"):
            if s.get(k):
                s[k] = E.clean_subtitle(s[k])

    # 3) 古文段标注
    tl["code"] = tl.get("code") or code_from_media_dir(tl.get("media_dir"))
    chapter = load_chapter(tl.get("code"), tl.get("chapter"))
    if chapter is None:
        print(f"  ⚠️ chapters.json 里找不到第{tl.get('chapter')}章"
              f"（code={tl.get('code')!r}），跳过古文段标注")
    hits = tag_pairs(segs, chapter)
    print(f"  古文段标注: {hits}/{len(segs)} 句匹配到 chapters.json 的 pair")

    # 4) 重选镜头
    durs = E.load_durations()
    cdurs = (durs.get("chapters") or {}).get(tl.get("chapter"), {})
    if not cdurs:
        print("  ⚠️ 无时长缓存，跳过重选镜头（--refresh-durations 可重建）")
    else:
        # 必须带上"不可用素材"排除表！否则这里会把 batch_timelines 的排除**覆盖掉**
        # （踩过：B1054 是便签纸镜头，被 --fix 又选回来了）。
        ex = E.load_excluded(tl.get("chapter"))
        if ex:
            print(f"    ⛔ 排除不可用素材（便签纸）: {sorted(ex)}")
        plan, notes, starts = E.assign_shots(segs, cdurs, excluded=ex)
        for i, (s, k, ss) in enumerate(zip(segs, plan, starts), 1):
            tag = "全景" if k.upper().startswith("A") else "特写"
            s["clip_name"] = k
            s["clip"] = os.path.join(tl.get("media_dir", ""), k) if tl.get("media_dir") else s.get("clip", "")
            s["src_start"] = ss
            ok = "✅" if cdurs[k] >= s["dur"] else "❌"
            print(f"    {i} [{tag}] {k:<12} 素材{cdurs[k]:6.2f}s "
                  f"入点{ss:5.2f} 配音{s['dur']:5.2f}s {ok}")
        for n in notes:
            print(f"    ⚠️  {n}")

    with open(args.timeline, "w", encoding="utf-8") as f:
        json.dump(tl, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  已写回 {args.timeline}")
    return 0


def cmd_refresh(tl, args):
    """权限恢复后，从原素材实测时长并写入缓存。"""
    import subprocess
    mp = os.path.join(E.AUTO, "media_map.json")
    with open(mp, encoding="utf-8") as f:
        mmap = json.load(f)
    cache_p = os.path.join(E.AUTO, "media_durations.json")
    cache = E.load_durations()
    cache.setdefault("chapters", {})
    cache.setdefault("_note", "素材时长缓存（秒），由 timeline_tool.py --refresh-durations 实测生成")
    rc = 0
    for ch, d in sorted(mmap.items()):
        if not os.path.isdir(d):
            print(f"  ❌ 第{ch}章 目录不可读: {d}")
            rc = 1
            continue
        got = {}
        for fn in sorted(os.listdir(d)):
            if not fn.upper().endswith((".MP4", ".MOV")) or fn.startswith("._"):
                continue
            r = subprocess.run([E.FFMPEG, "-hide_banner", "-i", os.path.join(d, fn)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
            if m:
                got[fn] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60
                                + float(m.group(3)), 3)
        if got:
            cache["chapters"][ch] = got
            print(f"  第{ch}章: {len(got)} 条 " +
                  ", ".join(f"{k}={v:.2f}" for k, v in sorted(got.items())))
    with open(cache_p, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  已写入 {cache_p}")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--refresh-durations", action="store_true")
    a = ap.parse_args()
    with open(a.timeline, encoding="utf-8") as f:
        tl = json.load(f)
    if a.refresh_durations:
        return cmd_refresh(tl, a)
    if a.fix:
        rc = cmd_fix(tl, a)
        with open(a.timeline, encoding="utf-8") as f:
            tl = json.load(f)
    return cmd_check(tl, a)


if __name__ == "__main__":
    sys.exit(main())
