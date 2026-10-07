#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""山参十四章 PDF -> chapters.json

输入: pdf_pages.txt (由 pypdf 抽取, 形如 "===== PAGE n =====" 分页)
输出: chapters.json  —— 每章: 编号/题名/出处/中英旁白(逐平行)/字幕示意/剪辑要点

设计要点:
- pypdf 对 CJK 子集字体做了字符间插空格 (如 "一 阴 一 阳"), 对拉丁文不插。
  因此: 含 CJK 的行 -> 去空格; 纯拉丁行 -> 保留空格。
- 每章正文 = 中英交替的"旁白行", 中文行与英文行一一对应 (用于字幕/配音对齐)。
"""
import json
import re
import sys
import unicodedata

SRC = sys.argv[1] if len(sys.argv) > 1 else "pdf_pages.txt"
OUT = sys.argv[2] if len(sys.argv) > 2 else "chapters.json"

CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
# 严格 CJK(不含兼容区部首/假名标点), 只用于"去字符间空格"
STRICT_CJK = "\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff60"


def despace(line: str) -> str:
    """CJK 行去掉 pypdf 插入的字符间空格; 拉丁行保持原样。

    两条铁律:
    1. 只能逐行处理, 绝不能跨行(否则多个句子粘成一段, 中英配对错位);
    2. 只在"汉字 空格 汉字"处去空格 —— 用严格 CJK 类,
       否则会把英文的 "a pearl" 粘成 "apearl"(配音直接念错)。
    """
    if CJK.search(line):
        s = re.sub(rf"(?<=[{STRICT_CJK}])\s+(?=[{STRICT_CJK}])", "", line)
        s = re.sub(r"\s+([，。；：、！？》」』）】])", r"\1", s)
        s = re.sub(r"([《「『（【])\s+", r"\1", s)
        return s.strip()
    return line.strip()
# 注意: pypdf 会把每个字符都用空格隔开, 故先对整行去空格再匹配
# 形如: 01·20260626-39一阴一阳《周易·系辞上》 / 13·记录待入库枯杨生稊《周易·大过·九二》待入库
RE_HEADER = re.compile(r"^(\d{2})·(?:记录)?(?:待入库)?([0-9]{8}-[0-9]+|待入库)(.*?)(?:待入库)?$")
RE_HEADER_LOOSE = re.compile(r"^(\d{2})·(?:记录)?(?:待[入人]库)?([0-9]{5,}-[0-9]+|待[入人]库)(.*?)(?:待[入人]库)?$")

# 13/14 章的页首带"记录待入库"标记, 版式上把题名/出处挤在一起, 用查表兜底
# (这两章原文标注"待入库", 尚无正式工程编号)
TITLE_HINT = {
    "13": ("枯杨生稊", "《周易·大过·九二》"),
    "14": ("方生方死", "《庄子·齐物论》"),
}


def match_header(raw_ln: str):
    """把 '0 1 · 2 0 2 6 0 6 2 6 - 3 9 一 阴 一 阳 《周易·系辞上》' 拆成 (no, code, rest)。"""
    flat = re.sub(r"\s+", "", raw_ln)
    m = RE_HEADER.match(flat) or RE_HEADER_LOOSE.match(flat)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


# 题名后紧跟的作者/前缀(移入 source), 以及"帛书"这类版本前缀
SOURCE_PREFIX = ("帛书", "郭店楚简", "上博简")
AUTHORS = ("屈原", "庄子", "老子", "孟子", "荀子", "孔子", "司马迁", "陶渊明")


def split_title_source(rest: str):
    """'一阴一阳《周易·系辞上》'          -> ('一阴一阳', '《周易·系辞上》')
    '上下求索屈原《离骚》'               -> ('上下求索', '屈原《离骚》')
    '大成若缺帛书《老子》·第四十五章'     -> ('大成若缺', '帛书《老子》·第四十五章')
    """
    m = re.search(r"《", rest)
    if not m:
        return rest, None
    title, tail = rest[: m.start()], rest[m.start():]
    prefix = ""
    for p in SOURCE_PREFIX + AUTHORS:          # 先试最长匹配
        if title.endswith(p) and len(title) > len(p):
            prefix, title = p, title[: -len(p)]
            break
    else:
        am = re.search(r"([\u4e00-\u9fff]{1,3})$", title)
        if am and len(title) > len(am.group(1)) + 1:
            prefix, title = am.group(1), title[: am.start()]
    return title, (prefix + tail)


def norm_text(s: str) -> str:
    """字形归一化 + 修 pypdf 的断词。

    必须尽早做: 否则 '⼊'(U+2F0A 部首) 不会匹配正则里的 '入'。
    注意: 只修 "y ang"->"yang" / "dee p"->"deep" 这类单字母断词,
    绝不能写 \b([a-z]) ([a-z]+)\b 这种通用规则 —— 它会把
    "a pearl" 粘成 "apearl"(配音会直接念错)。
    """
    out = []
    for ch in s:
        if 0x2E80 <= ord(ch) <= 0x2FDF:
            k = unicodedata.normalize("NFKC", ch)
            out.append(k if k.strip() else ch)
        else:
            out.append(ch)
    s = "".join(out)
    s = re.sub(r"\by ([a-z]{2,})\b", r"y\1", s)            # y ang -> yang
    s = re.sub(r"\b([a-z]{3,}) ([a-z])\b", r"\1\2", s)     # dee p -> deep
    s = re.sub(r"([a-z]{2,})\s+([,.])", r"\1\2", s)        # "them ." -> "them."
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")  # ﬁ -> fi
    s = re.sub(r"[ \t]{2,}", " ", s)
    return fix_english(s)


# pypdf 在这份 PDF 上的断词/粘连残留(逐条修正, 比通用正则安全)
_FIX_JOIN = {
    "intoa": "into a", "gathereda": "gathered a", "judgea": "judge a",
    "wasa": "was a", "fora": "for a", "withouta": "without a",
    "anda": "and a", "ruina": "ruin a", "alla": "all a", "seekm": "seek m",
    "ther oad": "the road", "myway": "my way",
}
_FIX_SPLIT = {
    "ne ver": "never", "ov ercomes": "overcomes", "kno ws": "knows",
    "flo wing": "flowing", "nob le": "noble", "earthk eep": "earth keep",
    "thef our": "the four", "lo w": "low",
}


def fix_english(s: str) -> str:
    for a, b in _FIX_JOIN.items():
        s = s.replace(a, b)
    for a, b in _FIX_SPLIT.items():
        s = s.replace(a, b)
    return s


def split_pages(raw: str):
    raw = norm_text(raw)          # 先做字形归一化, 否则正则匹配不到标准字形
    parts = raw.split("===== PAGE ")
    pages = []
    for p in parts[1:]:
        num, _, body = p.partition("=====")
        try:
            pages.append((int(num.strip()), body))
        except ValueError:
            continue
    return pages


def is_latin(line: str) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", line)) and not CJK.search(line)


def parse_chapter(page_no: int, body: str) -> dict:
    # 关键: 逐行 despace, 保留断行 —— 中文一行 = 一字幕段
    lines = [despace(x) for x in body.split("\n")]
    lines = [l for l in lines if l]
    ch = {"page": page_no, "no": None, "code": None, "title": None, "source": None,
          "basis": None, "thesis": None, "narration": [], "caption": None,
          "subtitle_demo": [], "edit_notes": [], "raw": lines}
    narration = []
    in_notes = False
    for d in lines:
        m = match_header(d)
        if m and ch["no"] is None:
            ch["no"], ch["code"], rest = m
            title, src = split_title_source(rest)
            if ch["no"] in TITLE_HINT:
                title, src = TITLE_HINT[ch["no"]]
                ch["title_hint"] = True
            ch["title"] = title
            ch["source"] = src
            continue
        if d.startswith("依据"):
            ch["basis"] = d
            continue
        if d.startswith("立意"):
            ch["thesis"] = d
            continue
        if d.startswith("〔") or d.startswith("["):
            ch["caption"] = d.strip("〔〕[] ")
            continue
        if d.replace(" ", "") == "剪辑要点" or (d.startswith("剪") and d.endswith("要点")):
            in_notes = True
            continue
        if d.startswith("↳") or d.startswith("»") or d.startswith(">"):
            ch["edit_notes"].append(d.lstrip("↳»> ").strip())
            in_notes = True
            continue
        if "⾦句段字幕样式⽰意" in d.replace(" ", "") or "金句段字幕样式示意" in d.replace(" ", ""):
            continue
        if d.startswith("引"):
            narration.append({"lang": "zh", "quote": True, "text": d[1:].strip()})
            continue
        if is_latin(d):
            narration.append({"lang": "en", "quote": d.startswith("Q "), "text": norm_text(d)})
        else:
            narration.append({"lang": "zh", "quote": False, "text": d})
    ch["narration"] = narration
    return ch


def pair_narration(narration):
    """按序号配对中英旁白。

    版式: 正文段是"中文 N 句在上 + 英文 N 句在下"(两块, 不是交替),
    所以必须按块内序号配对, 不能按相邻行配对。
    末尾竖排出处会被 pypdf 拆成单字(周/易·/系/辞/上...), 单独合并。
    """
    # 1. 拆成连续语言块
    blocks = []
    for item in narration:
        if blocks and blocks[-1]["lang"] == item["lang"]:
            blocks[-1]["items"].append(item)
        else:
            blocks.append({"lang": item["lang"], "items": [item]})
    # 2. 合并末尾的竖排碎字
    tail_seal = []
    while blocks:
        b = blocks[-1]
        if b["lang"] == "zh" and all(len(i["text"]) <= 2 for i in b["items"]):
            tail_seal = [i["text"] for i in b["items"]] + tail_seal
            blocks.pop()
        else:
            break
    # 3. 主体 = 前两个块(中文块 + 英文块)
    pairs = []
    if len(blocks) >= 2 and blocks[0]["lang"] == "zh" and blocks[1]["lang"] == "en":
        zh_items, en_items = blocks[0]["items"], blocks[1]["items"]
        for i, en in enumerate(en_items):
            pairs.append({
                "zh": zh_items[i]["text"] if i < len(zh_items) else None,
                "en": en["text"],
                "quote": en["quote"],
            })
    # 4. 去掉引文块里重复的行(引文既是正文也是大字示意时会出现)
    seen, uniq = set(), []
    for p in pairs:
        key = (p["en"] or "").strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq, "".join(tail_seal)


def main():
    pages = split_pages(open(SRC, encoding="utf-8").read())
    chapters, meta = [], []
    for num, body in pages:
        head = body.strip().split("\n")[0] if body.strip() else ""
        if match_header(head):
            chapters.append(parse_chapter(num, body))
        else:
            meta.append({"page": num, "text": body.strip()})
    # 中英配对(按块内序号)
    for ch in chapters:
        pairs, seal = pair_narration(ch["narration"])
        ch["pairs"] = pairs
        ch["seal_vertical"] = seal
        ch["en_full"] = " ".join(p["en"] for p in pairs)
        ch["en_words"] = len(ch["en_full"].split())
        ch["zh_lines"] = [p["zh"] for p in pairs if p["zh"]]
    doc = {"source_pdf": "山参十四章.pdf", "chapters": chapters, "meta_pages": meta}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"章节 {len(chapters)} 章 / 元信息页 {len(meta)} 页 -> {OUT}")
    for ch in chapters:
        print(f"  {ch['no']} {str(ch['title']):<7s} {str(ch['code']):<14s} {str(ch['source']):<24s} "
              f"en_words={ch['en_words']:3d} pairs={len(ch['pairs'])} notes={len(ch['edit_notes'])}")
    if not chapters:
        return
    allw = sum(c["en_words"] for c in chapters)
    print(f"英文总词数 {allw}, 平均 {allw/len(chapters):.1f} 词/章 "
          f"(按 demo 音色 ~1.5 词/秒 估算 ≈ {allw/len(chapters)/1.5:.1f}s/章)")


if __name__ == "__main__":
    main()
