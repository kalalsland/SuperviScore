# -*- coding: utf-8 -*-
"""复旦大学大数据学院 解析器。

数据来源（已实测）：两个富文本名录页，教师信息以正文内联呈现：
    https://sds.fudan.edu.cn/17428/list.htm
    https://sds.fudan.edu.cn/17429/list.htm
每位老师形如：
    <姓名> <职称(教授/副教授/青年研究员...)>，博士生导师，... <简介>。 主要研究方向： <方向>。
本解析器把"姓名+职称+简介+研究方向"一次性抽出（无单独个人页，detail 阶段直接返回缓存内容）。
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log
import config

LIST_URLS = [
    "https://sds.fudan.edu.cn/17428/list.htm",
    "https://sds.fudan.edu.cn/17429/list.htm",
]

# 职称词（用于在正文中定位每位老师的起点）
TITLE_WORDS = ["教授", "副教授", "青年研究员", "青年副研究员", "研究员",
               "副研究员", "讲师", "助理教授", "特聘教授", "讲席教授"]


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0]+", " ", s)
    return s.strip()


class FudanSdsParser(SchoolParser):
    name = "fudan_sds"
    display_name = "复旦大学大数据学院"
    output_dir_name = "复旦老师联系"
    dblp_affiliation = ["Fudan"]

    def __init__(self):
        # detail 阶段直接复用列表阶段抽到的内容
        self._detail_cache: dict[str, dict] = {}

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen = set()
        for url in LIST_URLS:
            try:
                resp = http_get(url, timeout=config.HTTP_TIMEOUT, retries=3)
                resp.encoding = resp.apparent_encoding or "utf-8"
                h = resp.text
            except Exception as e:
                log(f"[fudan_sds] 列表抓取失败 {url}: {e}")
                continue
            records = self._parse_people(h)
            for rec in records:
                key = rec["name"]
                if not key or key in seen:
                    continue
                seen.add(key)
                stub = TeacherStub(name=rec["name"], detail_url=url,
                                   institute="复旦·大数据学院")
                stubs.append(stub)
                self._detail_cache[rec["name"]] = rec
            log(f"[fudan_sds] {url}：抽到 {len(records)} 人（累计 {len(stubs)}）")
        return stubs

    def _parse_people(self, h: str) -> list[dict]:
        """把正文按「主要研究方向：…。」锚点切块，每块开头的「姓名+职称」即一位老师。"""
        i = h.find("wp_articlecontent")
        if i < 0:
            i = h.find("col_news_con")
        seg = h[i:] if i >= 0 else h
        text = _clean(seg)

        title_alt = "|".join(map(re.escape, TITLE_WORDS))
        name_re = re.compile(r"([一-龥]{2,4})\s+(" + title_alt + r")")
        # 以「主要研究方向：…。」为分隔，blocks 交替为 [正文, 方向句, 正文, 方向句, ...]
        blocks = re.split(r"(主要研究方向[：:].+?。)", text)
        people = []
        prev_tail = ""   # 上一块未用完的尾巴（姓名可能落在分隔符之后）
        for k in range(0, len(blocks) - 1, 2):
            body = prev_tail + blocks[k]
            dirpart = blocks[k + 1] if k + 1 < len(blocks) else ""
            m = name_re.search(body)
            if not m:
                prev_tail = body[-30:]
                continue
            prev_tail = ""
            name, title = m.group(1), m.group(2)
            if name in ("学院", "课程", "本科", "研究", "学术", "联系", "大学", "中心",
                        "上海", "高校", "团队"):
                continue
            direction = ""
            md = re.search(r"主要研究方向[：:]\s*(.+?)。", dirpart)
            if md:
                direction = md.group(1).strip()[:300]
            bio = body[m.start():m.start() + 1000]
            people.append({"name": name, "title": title,
                           "bio": bio, "direction": direction})
        return people

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        rec = self._detail_cache.get(stub.name, {})
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        t.title = rec.get("title", "")
        t.bio = rec.get("bio", "")
        # 研究方向并入 bio 末尾，便于 analyzer 利用
        if rec.get("direction"):
            t.bio = (t.bio + " 主要研究方向：" + rec["direction"]).strip()
            t.papers_listed = []
        return t
