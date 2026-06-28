# -*- coding: utf-8 -*-
"""GPT（OpenAI 兼容）封装：chat() 与 chat_json()，带重试与 JSON 解析。

该端点响应里可能含 reasoning_content 字段——只取 message.content。
模型 / 端点 / 密钥全部来自 config（见 config.py 第 2 节），不在代码里硬编码。
"""
from __future__ import annotations
import json
import time
import re
from openai import OpenAI
from core.utils import log
import config

if not config.LLM_API_KEY or not config.LLM_BASE_URL or not config.LLM_MODEL:
    raise RuntimeError(
        "未配置大模型 API。请设置环境变量 TAOCI_LLM_BASE_URL / TAOCI_LLM_API_KEY / "
        "TAOCI_LLM_MODEL，或在 config.py 第 2 节直接填写。详见 README「配置」一节。"
    )

_client = OpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    timeout=config.LLM_TIMEOUT,
)


def chat(system: str, user: str, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """普通对话，返回纯文本。失败重试。"""
    last = None
    for attempt in range(config.LLM_MAX_RETRY):
        try:
            resp = _client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last = e
            log(f"[llm] 调用失败(第{attempt+1}次): {e}")
            time.sleep(2 * (attempt + 1))
    raise last


def _extract_json(text: str):
    """从模型输出里抠出 JSON（容忍 ```json 包裹或前后废话）。"""
    text = text.strip()
    # 去 code fence
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except Exception:
        pass
    # 取第一个 { 到最后一个 } 之间
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            return None
    return None


def chat_json(system: str, user: str, temperature: float = 0.2, max_tokens: int = 2000):
    """要求模型返回 JSON，解析失败则重试（追加更强约束）。返回 dict 或 None。"""
    sys_full = system + "\n\n严格只输出一个合法 JSON 对象，不要任何解释、前后缀或 markdown 代码块。"
    last_text = ""
    for attempt in range(config.LLM_MAX_RETRY):
        try:
            resp = _client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "system", "content": sys_full},
                          {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            last_text = (resp.choices[0].message.content or "").strip()
            parsed = _extract_json(last_text)
            if parsed is not None:
                return parsed
        except Exception as e:
            log(f"[llm] JSON 调用失败(第{attempt+1}次): {e}")
        time.sleep(2 * (attempt + 1))
    log(f"[llm] JSON 解析最终失败，原文片段: {last_text[:200]}")
    return None
