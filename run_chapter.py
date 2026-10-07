#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_chapter.py —— 一章一条命令跑完（可扩到 12 章 / 1000 条）

串起整条流水线：
    1. 修正时间轴       timeline_tool.py --fix
    2. 预渲染叠加层     prep_media.py
    3. 建达芬奇草稿     build_resolve5.py --via-fcpxml
    4. 回读验证         verify_build.py

档案卡字段从 chapters.json 自动取（题名 / 出处）；
"23道芦碗·约26年" 这类逐章文案存在 cards.json 里（人写的，脚本不猜）。

用法
    # 素材在外置盘上时必须先拷到本地盘（FCPXML 解析不了 /Volumes/备份盘）
    /opt/homebrew/bin/python3 run_chapter.py --chapter 01 \
        --media-dir ~/Movies/素材/20260626～39

    # 不碰达芬奇，只跑前两步
    /opt/homebrew/bin/python3 run_chapter.py --chapter 01 --until prep
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editlib as E  # noqa: E402

PY = sys.executable
AUTO = E.AUTO
CARDS = os.path.join(AUTO, "cards.json")


def load_cards():
    if os.path.exists(CARDS):
        with open(CARDS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def card_fields(chapter):
    """档案卡 4 个字段：中文档 / 英文档 / 竖排题名 / 引文出处。"""
    chs = json.load(open(os.path.join(AUTO, "chapters.json"), encoding="utf-8"))["chapters"]
    ch = next((c for c in chs if str(c.get("no")) == str(chapter)), None)
    if ch is None:
        raise SystemExit(f"chapters.json 里没有第 {chapter} 章")
    c = load_cards().get(str(chapter), {})
    # 档案卡文案缺省时**回退到第 01 章**（用户 2026-10-06 要求：
    # 调用 skill 时先问用户这几个字段，用户跳过就用 v01 的数据）
    d = load_cards().get("01", {})
    src = (ch.get("source") or "").strip().strip("《》")
    return {
        "card_zh": c.get("card_zh") or d.get("card_zh", ""),
        "card_en": c.get("card_en") or d.get("card_en", ""),
        "card_name": c.get("card_name") or ch.get("title") or d.get("card_name", ""),
        "citation": c.get("citation") or src or d.get("citation", ""),
    }


def run(cmd, label):
    print(f"\n{'='*72}\n## {label}\n{'='*72}")
    print("  $ " + " ".join(str(x) for x in cmd))
    r = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(f"  ❌ {label} 退出码 {r.returncode}")
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", required=True)
    ap.add_argument("--media-dir", default=None,
                    help="素材目录（外置盘素材必须先拷到本地盘）")
    ap.add_argument("--standin", default=None, help="V1 镜头替换成本地文件（管线验证用）")
    ap.add_argument("--ver", default="v01")
    ap.add_argument("--until", choices=["fix", "prep", "build", "verify"],
                    default="verify", help="跑到哪一步为止")
    ap.add_argument("--replace", action="store_true",
                    help="已存在同名项目时删掉重建（规格要求项目名固定为 v01 时用）")
    ap.add_argument("--no-dry-run", action="store_true")
    # 档案卡文案：可由调用方（skill 问过用户之后）直接传进来；
    # 不传则用 cards.json -> 再回退第 01 章
    ap.add_argument("--card-zh", default=None, help="档案卡中文档文案")
    ap.add_argument("--card-en", default=None, help="档案卡英文档文案")
    ap.add_argument("--card-name", default=None, help="档案卡竖排题名")
    ap.add_argument("--citation", default=None, help="引文出处")
    a = ap.parse_args()

    tl = os.path.join(AUTO, "out", f"timeline_ch{int(a.chapter):02d}.json")
    if not os.path.exists(tl):
        raise SystemExit(f"缺时间轴 {tl}（先跑 batch_timelines.py --chapter {a.chapter}）")

    f = card_fields(a.chapter)
    # 命令行传进来的优先（skill 问过用户之后走这条）
    for k, v in (("card_zh", a.card_zh), ("card_en", a.card_en),
                 ("card_name", a.card_name), ("citation", a.citation)):
        if v:
            f[k] = v
    print(f"=== 第 {a.chapter} 章 ===")
    for k, v in f.items():
        print(f"  {k:10} = {v!r}")

    steps = []
    # 0) 字体自检：Font 必须能在 CoreText 里查到，否则 Fusion 渲染会失败（曾导致无法导出）
    steps.append((["font"], [PY, os.path.join(AUTO, "fontsel.py")], "0/6 字体自检"))
    # 文字布局预检：**不导出也能确认**每一行都放得下（一次成片的关键）
    steps.append((["layout"], [PY, os.path.join(AUTO, "check_text_layout.py"),
                               "--timeline", tl], "0/6 文字布局预检"))
    steps.append((["fix"], [PY, os.path.join(AUTO, "timeline_tool.py"), tl, "--fix",
                            "--check"], "1/6 修正并自检时间轴"))
    steps.append((["prep"], [PY, os.path.join(AUTO, "prep_media.py"), "--timeline", tl],
                  "2/6 预渲染叠加层"))
    # 项目名必须用**原目录名**（含全角 ～），不能用 timeline JSON 里归一化后的 code。
    # 规格 §5.1 明确：达芬奇项目名 = 参编号 + v01，如 "20260626～39v01"（全角 ～）。
    mmap = json.load(open(os.path.join(AUTO, "media_map.json"), encoding="utf-8"))
    mdir = a.media_dir or mmap.get(str(a.chapter)) or ""
    code = os.path.basename(os.path.expanduser(mdir).rstrip("/")) or \
        json.load(open(tl, encoding="utf-8")).get("code") or a.chapter
    b = [PY, os.path.join(AUTO, "build_resolve5.py"), "--timeline", tl,
         "--code", code, "--ver", a.ver,
         "--card-zh", f["card_zh"], "--card-en", f["card_en"],
         "--card-name", f["card_name"], "--citation", f["citation"],
         "--via-fcpxml"]
    if a.media_dir:
        b += ["--media-dir", os.path.expanduser(a.media_dir)]
    if a.standin:
        b += ["--standin", os.path.expanduser(a.standin)]
    if a.replace:
        b += ["--replace"]
    steps.append((["build"], b, "3/6 建达芬奇草稿（FCPXML 路径）"))
    # 注意：build_resolve5 在项目名已存在时会自动改成 v02/v03…
    # 所以 verify 不能想当然用 "{code}{ver}"，要读构建器记下来的**实际**项目名。
    proj_file = os.path.join(AUTO, "out", "last_project.txt")
    if os.path.exists(proj_file):
        try: os.remove(proj_file)
        except Exception: pass
    steps.append((["verify"], [PY, os.path.join(AUTO, "verify_build.py"),
                               "--project", f"{code}{a.ver}"], "4/6 回读验证"))

    order = [s[0][0] for s in steps]
    stop = order.index(a.until)
    rc = 0
    for keys, cmd, label in steps[:stop + 1]:
        if keys[0] == "verify" and os.path.exists(proj_file):
            try:
                real = open(proj_file, encoding="utf-8").read().strip()
                if real:
                    cmd = cmd[:] ; cmd[-1] = real
                    print(f"  （实际项目名: {real}）")
            except Exception:
                pass
        r = run(cmd, label)
        rc = rc or r
        if r:
            print(f"\n⚠️ 在「{label}」处失败，后续步骤跳过")
            break
    # 收尾：把达芬奇界面切回 Edit 页（防再卡在交付/预览页）
    try:
        import DaVinciResolveScript as _dvr
        _r = _dvr.scriptapp("Resolve")
        if _r:
            _r.OpenPage("edit")
    except Exception:
        pass
    print(f"\n{'='*72}")
    print(f"  {'✅ 全流程通过' if rc == 0 else '❌ 有失败步骤'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
