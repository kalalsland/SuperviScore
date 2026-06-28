# 套磁导师推荐小工具

面向**直博/保研**申请的导师筛选与套磁辅助工具。自动爬取学院教师名录，
检索每位老师近期论文（DBLP 优先、arXiv 补摘要），结合你的个人简历，用大模型
做**研究方向细化 + 匹配打分 + 套磁信草稿**，输出按"套磁能否上岸"倒排的名单。

## 一、快速开始

```bash
cd 小工具/main

# 冒烟测试：只跑前 3 位老师
python run.py 3

# 全量跑（按 config.py 的 LIMIT，0 表示全部）
python run.py
```

结果写到 `小工具/交大导师联系/`：
- `推荐名单.csv`：**全部老师**按推荐分倒排（Excel 直接打开，已带 BOM）。
- `top20详情/NN_姓名.md`：**前 20 名**的详细分析 + 个性化**套磁邮件草稿**。
- `_缓存/`：网页/论文/LLM 结果缓存，**断点续跑、重跑省钱**（删掉即全部重算）。

## 二、依赖

Python 3.9+，需要：`requests openai pymupdf pypdf`
```bash
pip install requests openai pymupdf pypdf
```
（本机已装齐，无需 bs4 等额外库。）

## 三、配置（只需改 `config.py`）

> 首次使用必读：仓库不含密钥与简历，请先 1) 把你的简历 PDF 放进 `小工具/个人履历材料/`，
> 2) 配置大模型 API（环境变量 `TAOCI_LLM_BASE_URL/KEY/MODEL`，或直接改 `config.py` 第 2 节）。
> 详见仓库根目录 [README](../README.md) 的「快速上手」。

| 项 | 说明 |
|---|---|
| `SCHOOL` | 当前学校解析器名（`sjtu_cs`）。换学校改这里。 |
| `SCHOOL_DBLP_AFFILIATION` | 该校在 DBLP 里的英文名关键词，用于作者消歧（强烈建议填准）。 |
| `LLM_BASE_URL/KEY/MODEL` | 大模型 API（OpenAI 兼容）。**不要硬编码自己的密钥后再提交**；推荐用环境变量 `TAOCI_LLM_*`。 |
| `RESUME_DIR` | 个人简历 PDF 目录（默认 `小工具/个人履历材料`）。 |
| `LIMIT` | >0 只跑前 N 人（测试用）；0=全部。 |
| `TOP_N_DETAIL` | 生成详情+套磁信的前 N 名（默认 20）。 |
| `WEIGHTS` | 全部打分权重，集中可调。 |

## 四、打分逻辑（直博·套磁能上岸优先）

最终分 = (基础分 + Σ加分 − Σ减分) × 活跃度系数

- **加分**：方向匹配度（最重要）、新晋 PI（青年导师好进）、资历适中好接触。
- **减分**：抢学生嫌疑（近作**连续≥2 篇本人一作**）、方向频繁切换、太牛难入
  （院士/杰青/Fellow）、论文无佐证。
- **乘性**：远离科研一线 → 按最新论文年份**指数衰减**。
- **标记**：所有嫌疑/待核实项写入 CSV 的「关键标记」列，**判断权交给你**。
- **博导**：按职称启发式判定（不卡人），CSV 里标出，最终你逐一核对。

> 重要：当 LLM 高置信判定"检索到的论文非本人"（同名误匹配）时，**自动忽略**
> 该论文带来的一作/活跃度评分，仅标记"待核实"，避免错杀。

## 五、换一所学校（如复旦）

工具按「**通用内核 `core/` + 学校插件 `schools/`**」解耦，换校只动后者：

1. 新建 `schools/fudan_cs.py`，继承 `SchoolParser`，实现：
   - `fetch_teacher_list()` → 返回 `[TeacherStub(姓名, 个人页URL, 研究所)]`
   - `fetch_teacher_detail(stub)` → 返回填好职称/简介/官网论文的 `Teacher`
   - 设好类属性 `name / display_name / output_dir_name`
2. 在 `schools/base.py` 的 `get_parser()` 里注册一行。
3. 改 `config.py`：`SCHOOL = "fudan_cs"`、`SCHOOL_DBLP_AFFILIATION = ["Fudan"]`。
4. `python run.py` → 结果进 `复旦老师联系/`。

`core/`（论文检索、LLM、简历、打分、报告）**一行不改**。

## 六、目录结构

```
main/
├── config.py            # 唯一日常需改的配置
├── run.py               # 入口
├── core/                # 通用内核（换校不动）
│   ├── models.py        # 数据类
│   ├── pinyin_names.py  # 拼音→姓名切分（DBLP 检索用）
│   ├── dblp_client.py   # DBLP 作者消歧 + 论文
│   ├── arxiv_client.py  # arXiv 补摘要
│   ├── paper_client.py  # 论文检索门面
│   ├── llm_client.py    # GPT 封装
│   ├── resume.py        # 简历→用户画像
│   ├── analyzer.py      # 方向细化+匹配+身份核验
│   ├── scorer.py        # 打分引擎（纯函数）
│   ├── report.py        # CSV + Markdown + 套磁信
│   └── pipeline.py      # 编排
├── schools/
│   ├── base.py          # 抽象基类 + 工厂
│   └── sjtu_cs.py       # 交大计院解析器
└── tests/test_scorer.py # 打分规则单测
```

## 七、注意

- 若所在网络有系统代理导致 DBLP/arXiv 连不上，本工具默认**绕过系统代理**直连
  （与 curl 行为一致）。如需走代理，设环境变量 `TAOCI_USE_PROXY=1`。
- 全量约 290+ 位老师，含 DBLP/arXiv 限速与每人一次 LLM 调用，耗时较长；
  建议后台运行，中断后重跑会自动跳过已完成项。
