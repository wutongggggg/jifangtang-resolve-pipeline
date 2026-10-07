#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_all.py —— 12 章批量跑（可扩到 1000 条）

逐章调用 run_chapter.py，最后汇总一张表。
前置：① 每章素材已拷到本地盘（FCPXML 解析不了 /Volumes/备份盘，见 ERRATA §3.7）
      ② 每章时间轴已生成（batch_timelines.py --all，需要 TTS + 素材时长）
      ③ cards.json 里补齐该章档案卡文案

用法
    # 全部 12 章，素材在 ~/Movies/素材/<参编号>/
    /opt/homebrew/bin/python3 run_all.py --media-root ~/Movies/素材

    # 只跑 01、02
    /opt/homebrew/bin/python3 run_all.py --media-root ~/Movies/素材 --chapters 01,02
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

PY = sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--media-root", required=True,
                    help="本地素材根目录，下面按参编号建子目录")
    ap.add_argument("--chapters", default=None, help="逗号分隔，默认全部有素材的章")
    ap.add_argument("--ver", default="v01")
    ap.add_argument("--until", default="verify",
                    choices=["fix", "prep", "build", "verify"])
    a = ap.parse_args()
    root = os.path.expanduser(a.media_root)
    mmap = json.load(open(os.path.join(E.AUTO, "media_map.json"), encoding="utf-8"))
    codes = {str(k): os.path.basename(v.rstrip("/")) for k, v in mmap.items()}
    todo = (a.chapters.split(",") if a.chapters else sorted(mmap, key=lambda x: int(x)))

    print(f"待处理 {len(todo)} 章；素材根目录 {root}\n")
    rows = []
    for ch in todo:
        ch = str(ch).zfill(2)
        code = codes.get(ch)
        if not code:
            rows.append((ch, code, "无编号", "跳过")); continue
        mdir = os.path.join(root, code)
        tlp = os.path.join(E.AUTO, "out", f"timeline_ch{ch}.json")
        if not os.path.exists(tlp):
            rows.append((ch, code, "无时间轴", "跳过")); continue
        if not os.path.isdir(mdir):
            rows.append((ch, code, "素材未拷到本地", "跳过")); continue
        cmd = [PY, os.path.join(E.AUTO, "run_chapter.py"), "--chapter", ch,
               "--media-dir", mdir, "--ver", a.ver, "--until", a.until]
        r = subprocess.run(cmd, text=True, capture_output=True)
        tail = [x for x in (r.stdout or "").strip().split("\n") if x.strip()][-1:]
        status = "✅ 通过" if r.returncode == 0 else f"❌ 失败({r.returncode})"
        rows.append((ch, code, tail[0][:60] if tail else "", status))
        print(f"  ch{ch} {code}: {status}")

    print("\n===== 汇总 =====")
    print(f"  {'章':<4}{'编号':<16}{'状态':<14}备注")
    for ch, code, note, status in rows:
        print(f"  {ch:<4}{(code or ''):<16}{status:<14}{note}")
    ok = sum(1 for r in rows if r[3].startswith("✅"))
    print(f"\n  成功 {ok}/{len(rows)}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
