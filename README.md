# SuperviScore · 套磁导师推荐小工具

面向 **直博 / 保研** 申请的导师筛选与套磁辅助工具。自动爬取学院教师名录，
检索每位老师近期论文（DBLP 优先、arXiv 补摘要），结合你的个人简历，用大模型
做 **研究方向细化 + 匹配打分 + 套磁信草稿**，输出按"套磁能否上岸"倒排的名单。

> ⚠️ 本仓库已移除作者本人的简历、运行缓存与 API 密钥。**首次使用必须完成下面两步配置**
> （放入你自己的简历 + 填入你自己的大模型 API），否则无法运行。

代码主体在 [`main/`](main/) 目录，详细说明见 [main/README.md](main/README.md)。

---

## 快速上手（3 步）

### 1) 安装依赖

```bash
pip install requests openai pymupdf pypdf
```

### 2) 放入你自己的简历
在根目录新建`个人履历材料/`文件夹
把你的简历 PDF 放到 `个人履历材料/` 目录下（该目录已被 `.gitignore` 忽略，不会被提交）：
```
个人履历材料/
    └── 你的简历.pdf      ← 放这里，可多份
```

工具会读取该目录下所有 PDF，提炼成"个人画像"用于匹配。

### 3) 配置大模型 API（二选一）

本工具用任意 **OpenAI 兼容** 端点（OpenAI / DeepSeek / 本地 vLLM / Ollama 等均可）。
出于安全，仓库里 **不含任何密钥**，请用以下任一方式提供你自己的：

**方式 A：环境变量（推荐，不改代码）**

```bash
# Linux / macOS
export TAOCI_LLM_BASE_URL="https://api.openai.com/v1"
export TAOCI_LLM_API_KEY="sk-你自己的key"
export TAOCI_LLM_MODEL="gpt-4o-mini"
```

```powershell
# Windows PowerShell
$env:TAOCI_LLM_BASE_URL="https://api.openai.com/v1"
$env:TAOCI_LLM_API_KEY="sk-你自己的key"
$env:TAOCI_LLM_MODEL="gpt-4o-mini"
```

**方式 B：直接改 `main/config.py` 第 2 节**

```python
LLM_BASE_URL = os.environ.get("TAOCI_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY  = os.environ.get("TAOCI_LLM_API_KEY",  "sk-你自己的key")
LLM_MODEL    = os.environ.get("TAOCI_LLM_MODEL",    "gpt-4o-mini")
```

> 若直接改 `config.py`，注意 **不要把含密钥的版本提交回 Git**。

### 运行

```bash
cd main
python run.py 3     # 冒烟测试：只跑前 3 位老师
python run.py       # 全量
```

结果写到 `<学校>老师联系/`（如 `交大导师联系/`），该目录同样已被忽略，不会上传。

---

## 还能改什么

所有日常可调项集中在 [main/config.py](main/config.py)：

| 项 | 说明 |
|---|---|
| `SCHOOL` | 当前学校解析器名（如 `sjtu_cs`）。换学校改这里。 |
| `SCHOOL_DBLP_AFFILIATION` | 该校在 DBLP 里的英文名关键词，用于作者消歧。 |
| `LLM_BASE_URL/KEY/MODEL` | 大模型 API，见上方第 3 步。 |
| `RESUME_DIR` | 简历 PDF 目录（默认 `个人履历材料/`）。 |
| `LIMIT` | >0 只跑前 N 人（测试用）；0=全部。 |
| `TOP_N_DETAIL` | 生成详情+套磁信的前 N 名（默认 20）。 |
| `WEIGHTS` | 全部打分权重，集中可调。 |

**换一所学校**（如复旦）：在 `schools/` 下新增一个解析器并在工厂里注册，
再把 `config.py` 的 `SCHOOL` 改掉即可，`core/` 一行不动。详见
[main/README.md](main/README.md) 第五节。

---

## 隐私说明

`.gitignore` 已排除以下内容，**不会被提交**：

- `个人履历材料/`、所有 `*.pdf` —— 你的简历
- `*老师联系/`、`*导师联系/`、`_缓存/`、`*详情/`、`推荐名单.csv` —— 运行产物（含从简历提炼的个人画像缓存）
- `.env`、`*.key`、`secrets.*` —— 密钥
- `__pycache__/` 等 Python 缓存

克隆本仓库的人看不到上述任何个人信息，需自行放入简历并配置 API。
