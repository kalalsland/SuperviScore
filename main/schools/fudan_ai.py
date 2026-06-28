# -*- coding: utf-8 -*-
"""复旦大学计算与智能创新学院 解析器。

数据来源（已实测）：
- 列表：POST https://ai.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome
        body: siteId=577, pageIndex, rows, conditions=[], orders, returnInfos, articleType=1, level=1
        返回 JSON: {total, data:[{title=姓名, cnUrl=个人主页, exField1=职称(常空), email}, ...]}
- 个人页（cnUrl）异构（多为各自模板），best-effort 抓正文文本作 bio；抓不到留空，
  研究方向由 DBLP/Scholar 论文经 analyzer 总结。
"""
from __future__ import annotations
import re
import json
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

LIST_API = "https://ai.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"
SITE_ID = "577"

_RETURN_INFOS = json.dumps([
    {"field": "title", "name": "title"},
    {"field": "cnUrl", "name": "cnUrl"},
    {"field": "exField1", "name": "exField1"},
    {"field": "email", "name": "email"},
], ensure_ascii=False)
_ORDERS = json.dumps([{"field": "letter", "type": "asc"}], ensure_ascii=False)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0​]+", " ", s)
    return s.strip()


class FudanAiParser(SchoolParser):
    name = "fudan_ai"
    display_name = "复旦大学计算与智能创新学院"
    output_dir_name = "复旦老师联系"

    # 该校在 DBLP affiliation note 里的关键词（供 paper_client 消歧；config 里也会覆盖）
    dblp_affiliation = ["Fudan"]

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen = set()
        page = 1
        while True:
            data = {
                "siteId": SITE_ID, "pageIndex": page, "rows": 100,
                "conditions": "[]", "orders": _ORDERS,
                "returnInfos": _RETURN_INFOS, "articleType": 1, "level": 1,
            }
            try:
                resp = http_post(LIST_API, data=data, timeout=config.HTTP_TIMEOUT,
                                 headers={"X-Requested-With": "XMLHttpRequest"})
                payload = resp.json()
            except Exception as e:
                log(f"[fudan_ai] 列表第 {page} 页失败: {e}")
                break
            rows = payload.get("data") or []
            new = 0
            for r in rows:
                name = _clean(r.get("title", ""))
                url = (r.get("cnUrl") or "").strip()
                title = _clean(r.get("exField1", ""))
                if not name or url in seen:
                    continue
                seen.add(url)
                st = TeacherStub(name=name, detail_url=url, institute="复旦·计算与智能创新学院")
                # 把职称/邮箱暂存到 stub 上（detail 阶段补全）
                st._title = title           # type: ignore[attr-defined]
                st._email = _clean(r.get("email", ""))  # type: ignore[attr-defined]
                stubs.append(st)
                new += 1
            total = payload.get("total", 0)
            log(f"[fudan_ai] 列表第 {page} 页：+{new}（累计 {len(stubs)}/{total}）")
            if new == 0 or len(stubs) >= int(total or 0):
                break
            page += 1
            if page > 20:
                break
            polite_sleep(0.5)
        return stubs

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        t.title = getattr(stub, "_title", "") or ""
        t.email = getattr(stub, "_email", "") or ""
        t.homepage = stub.detail_url

        # best-effort 抓个人页正文作 bio（页面异构，失败留空）
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT, retries=2)
            resp.encoding = resp.apparent_encoding or "utf-8"
            h = resp.text
            bio, more_title = self._extract_bio(h)
            t.bio = bio
            if not t.title and more_title:
                t.title = more_title
        except Exception as e:
            log(f"[fudan_ai] 个人页抓取失败(忽略) {stub.name}: {e}")
        return t

    def _extract_bio(self, h: str) -> tuple[str, str]:
        """从异构个人页抽正文文本与可能的职称。"""
        # 去 script/style
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)
        # 优先内容容器
        title = ""
        m = re.search(r"(教授|副教授|青年研究员|研究员|讲师|助理教授|博士生导师|博导)", h2)
        if m:
            title = m.group(1)
        for cls in ["wp_articlecontent", "article", "col_news_con", "infobox", "list szinfo"]:
            i = h2.find(cls)
            if i >= 0:
                seg = _clean(h2[i:i + 4000])
                if len(seg) > 80:
                    return seg[:1800], title
        # 兜底：全文可见文本里若含“研究方向/简介”，截取附近
        text = _clean(h2)
        for kw in ["研究方向", "研究兴趣", "个人简介", "简介"]:
            j = text.find(kw)
            if j >= 0:
                return text[j:j + 1200], title
        return "", title
