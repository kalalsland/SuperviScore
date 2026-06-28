# -*- coding: utf-8 -*-
"""流水线编排：抓列表 → 抓个人页 → DBLP/arXiv 论文 → LLM 分析 → 打分 → 输出。

特性：每位老师独立容错、磁盘缓存断点续跑、限速、进度日志。
"""
from __future__ import annotations
import os
import re
import traceback

from core.utils import log, Cache
from core import resume, paper_client, analyzer, scorer, report
from schools.base import get_parser
import config


def _pinyin_from_url(url: str) -> str:
    """从 .../jiaoshiml/chenhaibo.html 取 chenhaibo。"""
    m = re.search(r"/([^/]+)\.html?$", url or "")
    return m.group(1) if m else ""


def run():
    parser = get_parser(config.SCHOOL)
    log(f"==== 套磁推荐工具 · {parser.display_name} ====")

    out_dir = os.path.join(config.OUTPUT_ROOT, parser.output_dir_name)
    os.makedirs(out_dir, exist_ok=True)
    cache = Cache(os.path.join(out_dir, "_缓存"), enabled=config.USE_CACHE)

    # 1) 用户画像
    profile = resume.build_user_profile(cache=cache)
    log(f"[pipeline] 用户画像: {profile.get('name','?')} | "
        f"方向 {profile.get('research_areas', [])}")

    # 2) 教师列表
    stubs = parser.fetch_teacher_list()
    if config.LIMIT and config.LIMIT > 0:
        stubs = stubs[:config.LIMIT]
        log(f"[pipeline] LIMIT={config.LIMIT}，仅处理前 {len(stubs)} 人")
    log(f"[pipeline] 待处理老师：{len(stubs)} 人")

    # 3) 逐位处理
    teachers = []
    total = len(stubs)
    for idx, stub in enumerate(stubs, 1):
        try:
            log(f"\n[{idx}/{total}] 处理 {stub.name}（{stub.institute}）")
            t = parser.fetch_teacher_detail(stub)
            pinyin = _pinyin_from_url(stub.detail_url)

            pr = paper_client.recent_papers(t.name, pinyin, t.institute, cache=cache)
            t.papers = pr["papers"]
            t.identity_confidence = pr["identity_confidence"]
            t.dblp_url = pr["dblp_url"]

            t.analysis = analyzer.analyze(t, profile, cache=cache)
            t.score = scorer.score(t, t.analysis)
            log(f"    → 推荐分 {t.score.final_score} | 匹配 {t.score.match_score} | "
                f"{'博导' if t.score.is_phd_advisor_guess else '非博导?'} | "
                f"flags={len(t.score.flags)}")
            teachers.append(t)
        except Exception as e:
            log(f"    ✗ 处理失败 {stub.name}: {e}")
            log(traceback.format_exc())
            continue

    # 4) 排序 + 输出
    teachers.sort(key=lambda x: (x.score.final_score if x.score else 0), reverse=True)
    report.write_all(teachers, profile, out_dir)
    log(f"\n==== 完成。共 {len(teachers)} 人，结果在: {out_dir} ====")
    return teachers
