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
SCHOOL = "sjtu_cs"          # 下次复旦改成 "fudan_cs"

# 该学校在 DBLP affiliation note 里的英文关键词（用于作者消歧，强烈建议填）
# 换学校时一并改这里。多个关键词命中其一即可。
SCHOOL_DBLP_AFFILIATION = ["Shanghai Jiao Tong", "Jiao Tong University"]

# ---------------------------------------------------------------------------
# 2. 大模型 API（OpenAI 兼容）
#    出于安全，这里不硬编码任何密钥。请通过环境变量提供，或直接在下面填入你自己的值。
#      export TAOCI_LLM_BASE_URL="https://your-endpoint/v1"
#      export TAOCI_LLM_API_KEY="sk-your-own-key"
#      export TAOCI_LLM_MODEL="your-model-name"
#    任意 OpenAI 兼容端点均可（OpenAI / DeepSeek / 本地 vLLM / Ollama 等）。
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.environ.get("TAOCI_LLM_BASE_URL", "")  # 例: https://api.openai.com/v1
LLM_API_KEY  = os.environ.get("TAOCI_LLM_API_KEY",  "")  # 例: sk-xxxxxxxx
LLM_MODEL    = os.environ.get("TAOCI_LLM_MODEL",    "")  # 例: gpt-4o-mini
LLM_TIMEOUT  = 120          # 单次请求超时（秒）
LLM_MAX_RETRY = 3           # 失败重试次数

# ---------------------------------------------------------------------------
# 3. 路径配置
# ---------------------------------------------------------------------------
# 本工具根目录（小工具/）
TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 个人履历材料目录（读取里面的 PDF 作为个人画像来源）
RESUME_DIR = os.path.join(TOOL_ROOT, "个人履历材料")
# 输出根目录（具体子目录名由各学校解析器的 output_dir_name 决定，自动创建）
OUTPUT_ROOT = TOOL_ROOT

# ---------------------------------------------------------------------------
# 4. 运行范围 / 性能
# ---------------------------------------------------------------------------
LIMIT = 0                   # >0 时只处理前 N 位老师（冒烟测试用）；0=全部
TOP_N_DETAIL = 20           # 仅对推荐分前 N 位生成 Markdown 详情 + 套磁信
RECENT_PAPERS = 5           # 每位老师检索最近几篇论文
DBLP_SLEEP = 1.5            # DBLP 请求间隔（秒，遵守 API 礼仪）
DBLP_RETRIES = 5            # DBLP 限流较狠，重试次数多一些
DBLP_BACKOFF = 8.0          # DBLP 退避基数（秒）：8,16,24,32... 给限流足够冷却
ARXIV_SLEEP = 3.0           # arXiv 请求间隔（秒）
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
# 新晋 PI 信号
JUNIOR_PI_TITLES = ["副教授", "助理教授", "特别研究员", "长聘教轨副教授", "Assistant Professor"]
