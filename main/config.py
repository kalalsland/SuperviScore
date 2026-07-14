# -*- coding: utf-8 -*-
"""
全局配置 —— 唯一需要日常修改的文件。
换学校：改 SCHOOL 一行即可（前提是 schools/ 下已有对应解析器）。
换 API：改 LLM_* 即可。
"""
import os

# ---------------------------------------------------------------------------
# 1. 选择本次要跑的学校（对应 schools/ 下的模块名）
# ---------------------------------------------------------------------------
SCHOOL = "fudan_ai"          # 可选: sjtu_cs / fudan_ai / fudan_sds
                             #       sjtu_sais（交大自动化与感知学院）
                             #       sjtu_see（交大电气工程学院）
                             #       sjtu_icisee（交大集成电路学院/信息与电子工程学院）
                             #       sjtu_bme（交大生医工）/ fudan_bme（复旦生医工）
                             #       ruc_info（人大信息学院）
                             #       seu_cse（东南大学计算机/软件/AI）
                             #       seu_cyber（东南大学网安学院）
                             #       shanghaitech_sist（上科大信息学院）
                             #       ustc_cs（中科大计算机）/ ustc_auto（中科大自动化）
                             #       ustc_aids（中科大SAIDS人工智能与数据科学学院）

# 该学校在 DBLP affiliation note 里的英文关键词（用于作者消歧，强烈建议填）
# 换学校时一并改这里。多个关键词命中其一即可。
#   交大所有学院: ["Shanghai Jiao Tong", "Jiao Tong University"]
#   复旦: ["Fudan"]
# SCHOOL_DBLP_AFFILIATION = ["Shanghai Jiao Tong", "Jiao Tong University"]
SCHOOL_DBLP_AFFILIATION = ["Fudan"]

# ---------------------------------------------------------------------------
# 2. 大模型 API（OpenAI 兼容）
#    优先级：环境变量 TAOCI_LLM_* > config_local.py > 这里的默认值（默认空）。
#    出于安全，仓库里不硬编码密钥。本地用法二选一：
#      (a) 设环境变量 TAOCI_LLM_BASE_URL / TAOCI_LLM_API_KEY / TAOCI_LLM_MODEL；
#      (b) 复制 config_local.example.py 为 config_local.py 并填入自己的值
#          （config_local.py 已被 .gitignore 排除，不会上传）。
#    任意 OpenAI 兼容端点均可（OpenAI / DeepSeek / 本地 vLLM / Ollama 等）。
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.environ.get("TAOCI_LLM_BASE_URL", "")  # 例: https://api.openai.com/v1
LLM_API_KEY  = os.environ.get("TAOCI_LLM_API_KEY",  "")  # 例: sk-xxxxxxxx
LLM_MODEL    = os.environ.get("TAOCI_LLM_MODEL",    "")  # 例: gpt-4o-mini
LLM_TIMEOUT  = 120          # 单次请求超时（秒）
LLM_MAX_RETRY = 5           # 失败重试次数（502/503 等临时故障需多几次）

# 本地覆盖（config_local.py 不入库）：未设环境变量时，用本地文件里的值补上
try:
    import config_local as _local      # noqa
    LLM_BASE_URL = LLM_BASE_URL or getattr(_local, "LLM_BASE_URL", "")
    LLM_API_KEY  = LLM_API_KEY  or getattr(_local, "LLM_API_KEY",  "")
    LLM_MODEL    = LLM_MODEL    or getattr(_local, "LLM_MODEL",    "")
except Exception:
    pass

# ---------------------------------------------------------------------------
# 3. 路径配置
# ---------------------------------------------------------------------------
# 本工具根目录（SuperviScore/，由本文件位置自动推导，不写死绝对路径）
TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 个人履历材料目录（读取里面的 PDF 作为个人画像来源）
RESUME_DIR = os.path.join(TOOL_ROOT, "个人履历材料")
# 输出根目录（具体子目录名由各学校解析器的 output_dir_name 决定，自动创建）
OUTPUT_ROOT = TOOL_ROOT

# ---------------------------------------------------------------------------
# 4. 运行范围 / 性能
# ---------------------------------------------------------------------------
LIMIT = 0                   # >0 时只处理前 N 位老师（冒烟测试用）；0=全部
USE_PRESCREEN = True        # 在 DBLP/Scholar/LLM 细化前做轻量方向预筛（省时省 token）
PRESCREEN_THRESHOLD = 25    # 预筛方向匹配分低于此值则跳过细化，仍写入 CSV（0-100）
TOP_N_DETAIL = 20           # 仅对推荐分前 N 位生成 Markdown 详情 + 套磁信
RECENT_PAPERS = 5           # 每位老师检索最近几篇论文
TOP_N_LETTER = 5            # 套磁信：综合分前 N + 方向匹配前 N 各生成一封
DBLP_SLEEP = 1.5            # DBLP 请求间隔（秒，遵守 API 礼仪）
DBLP_RETRIES = 5            # DBLP 限流较狠，重试次数多一些
DBLP_BACKOFF = 8.0          # DBLP 退避基数（秒）：8,16,24,32... 给限流足够冷却
ARXIV_SLEEP = 3.0           # arXiv 请求间隔（秒）
# 学术影响力数据源（best-effort，取不到自动降级到 DBLP/arXiv，不阻断流程）
USE_SCHOLAR = True          # Google Scholar 取引用量/h-index/代表作（需能访问 scholar.google.com）
USE_GITHUB = True           # GitHub 取主页/代表仓库（仅当老师主页/简介里有 github 链接才采纳）
# Scholar/GitHub 走 VPN 代理（国内直连常被墙）。留空=自动探测系统/环境代理；
# 也可显式写死，如 "http://127.0.0.1:33210"。DBLP/arXiv 不受影响，仍直连。
SCHOLAR_PROXY = ""
SCHOLAR_SLEEP = 3.0         # Scholar 请求间隔（反爬，建议大一些）
GITHUB_SLEEP = 1.0          # GitHub API 间隔
HTTP_TIMEOUT = 30           # 普通网页/接口请求超时
USE_CACHE = True            # 是否使用磁盘缓存（断点续跑、省 token）

# ---------------------------------------------------------------------------
# 5. 打分权重（直博 · 套磁能上岸优先）—— 集中可调
#    最终分 = BASE + Σ加分 − Σ减分，再乘活跃度指数系数。
# ---------------------------------------------------------------------------
BASE_SCORE = 50.0

WEIGHTS = {
    # —— 加分项 ——
    "match_user":        0.50,   # 方向与个人经历匹配度（analyzer 0-100）× 该权重，最重要
    "junior_pi_bonus":   15.0,   # 新晋 PI（副教授/特别研究员且近年独立发文）→ 直博更易上岸
    "rising_direction":  8.0,    # 方向正在上升且聚焦
    "approachable_bonus": 10.0,  # 资历适中、好接触

    # —— 减分项 ——
    "not_advisor_penalty":   40.0,  # 职称启发式判定非博导（讲师/实验师等）
    "grab_student_penalty":  25.0,  # 抢学生嫌疑：近作连续 ≥2 篇本人一作
    "direction_drift_penalty": 15.0, # 方向频繁切换
    "too_senior_penalty":    15.0,  # 太牛难入（院士/杰青/Fellow/顶尖）
    "no_paper_penalty":      10.0,  # 论文无佐证（DBLP+arXiv 均查无）

    # —— 活跃度（乘性指数衰减）——
    # score *= exp(-decay_lambda * max(0, 距今年数 - grace_years))
    "inactive_decay_lambda": 0.55,  # 衰减强度
    "inactive_grace_years":  1,     # 1 年内不衰减
}

# 职称 → 是否大概率博导（启发式，不卡人，仅用于打分与标记，最终用户自核）
ADVISOR_TITLES = ["教授", "特聘教授", "长聘教授", "讲席教授", "研究员", "特别研究员",
                  "长聘教轨", "tenure", "Professor", "教授（研究员）"]
NON_ADVISOR_TITLES = ["实验师", "讲师", "助理", "工程师", "博士后", "助教"]

# 太牛信号关键词（出现在简介里 → 太牛难入，谨慎套）
TOO_SENIOR_KEYWORDS = ["院士", "杰出青年", "杰青", "长江学者", "ACM Fellow", "IEEE Fellow",
                       "CCF Fellow", "Fellow", "国家级", "千人", "万人", "优青", "讲席"]
# 新晋 PI 信号（青年导师，直博相对好进）
JUNIOR_PI_TITLES = ["副教授", "助理教授", "特别研究员", "长聘教轨副教授",
                    "青年研究员", "青年副研究员", "副研究员", "Assistant Professor"]
