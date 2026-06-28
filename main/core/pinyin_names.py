# -*- coding: utf-8 -*-
"""中文姓 → 拼音 映射，用于把个人页 URL 的连写拼音切成 "名 姓" 供 DBLP 检索。

DBLP 英文名格式是 "Given Surname"（如 Haibo Chen），而交大 URL 是 surname+given
连写小写（chenhaibo）。用「中文姓的拼音」做前缀切分最稳。
覆盖最常见的单姓 + 常见复姓，足以覆盖绝大多数老师。
"""
from __future__ import annotations
import re

# 常见复姓（先匹配，优先级高）
COMPOUND_SURNAMES = {
    "ouyang": "欧阳", "situ": "司徒", "zhuge": "诸葛", "shangguan": "上官",
    "sima": "司马", "xiahou": "夏侯", "huangfu": "皇甫", "duanmu": "端木",
    "murong": "慕容", "dongfang": "东方", "nangong": "南宫", "wanyan": "完颜",
}

# 常见单姓拼音（覆盖百家姓主体）
SINGLE_SURNAMES = {
    "wang", "li", "zhang", "liu", "chen", "yang", "huang", "zhao", "wu", "zhou",
    "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao", "lin", "luo",
    "zheng", "liang", "xie", "song", "tang", "han", "feng", "deng", "cao", "peng",
    "zeng", "xiao", "tian", "dong", "pan", "yuan", "cai", "jiang", "yu",
    "du", "ye", "cheng", "wei", "su", "lu", "ding", "ren", "shen", "yao",
    "zhong", "fan", "fang", "shi", "tao", "qin", "xia", "gu", "wan",
    "duan", "qian", "tan", "liao", "zou", "xiong", "jin", "hao",
    "kong", "bai", "cui", "kang", "mao", "qiu", "gong",
    "che", "hou", "long", "wen", "niu", "geng", "guan", "yin", "pang", "fu",
    "ji", "mu", "lei", "bian", "qi", "ni", "rong", "weng", "an",
    "zang", "mi", "mei", "lan", "huo", "kou", "ke", "le", "qu",
    "meng", "yan", "lou", "chu", "ruan", "hua", "jia", "min", "tong",
}


def split_name(pinyin: str) -> tuple[str, str]:
    """把连写拼音切成 (given, surname)。切不出来时 surname='' 、given=整串。"""
    p = (pinyin or "").strip().lower()
    p = re.sub(r"[^a-z]", "", p)
    # 复姓优先
    for sur in sorted(COMPOUND_SURNAMES, key=len, reverse=True):
        if p.startswith(sur) and len(p) > len(sur):
            return p[len(sur):], sur
    # 单姓：取能匹配的最长前缀
    for sur in sorted(SINGLE_SURNAMES, key=len, reverse=True):
        if p.startswith(sur) and len(p) > len(sur):
            return p[len(sur):], sur
    return p, ""


def dblp_query_variants(pinyin: str) -> list[str]:
    """生成 DBLP 检索式候选，按可信度排序。"""
    given, sur = split_name(pinyin)
    out = []
    if sur:
        out.append(f"{given} {sur}")   # Haibo Chen
        out.append(f"{sur} {given}")   # 兜底另一种顺序
    # 整串兜底（万一姓表没覆盖）
    out.append(pinyin.strip().lower())
    # 去重保序
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q); uniq.append(q)
    return uniq
