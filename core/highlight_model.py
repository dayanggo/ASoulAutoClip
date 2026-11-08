"""Small HTTP adapter for configurable chat-completion endpoints."""

import json
import os
import time

import requests

from core.highlight_selection import SelectionError, ModelAPIError


def parse_json_response(text):
    if not isinstance(text, str):
        raise SelectionError("模型未返回文本 JSON")
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise SelectionError("模型返回了无效 JSON，请检查模型是否支持结构化输出") from exc
    if not isinstance(value, dict):
        raise SelectionError("模型 JSON 顶层需为对象")
    return value


class ChatModel:
    def __init__(self, config):
        self.config = config
        base_url = str(config.get("base_url", "")).strip().rstrip("/")
        self.name = str(config.get("model", "")).strip()
        if not base_url.startswith(("https://", "http://")) or not self.name:
            raise SelectionError("请在配置中填写 llm.base_url 和 llm.model；base_url 应以 /v1 结尾")
        if base_url.endswith("/chat/completions"):
            raise SelectionError("llm.base_url 请填写到 /v1，不要重复填写 /chat/completions")
        self.url = base_url + "/chat/completions"
        key_env = config.get("api_key_env", "")
        key = os.environ.get(key_env, "") if key_env else ""
        if key_env and not key:
            raise SelectionError(f"未设置模型密钥环境变量：{key_env}，也可在项目根目录 .env 中填写")
        self.headers = {"Content-Type": "application/json"}
        if key:
            self.headers["Authorization"] = "Bearer " + key
        self.cache_identity = {"base_url": base_url, "model": self.name,
                               "temperature": 0, "json_mode": bool(config.get("json_mode", False)),
                               "enable_thinking": config.get("enable_thinking"),
                               "max_tokens": int(config.get("max_tokens", 4096))}

    def complete(self, prompt, payload):
        body = {"model": self.name, "temperature": 0,
                "max_tokens": self.cache_identity["max_tokens"],
                "messages": [{"role": "system", "content": prompt},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        if self.cache_identity["json_mode"]:
            body["response_format"] = {"type": "json_object"}
        if self.cache_identity["enable_thinking"] is not None:
            body["enable_thinking"] = self.cache_identity["enable_thinking"]
        attempts = int(self.config.get("attempts", 2))
        if not 1 <= attempts <= 5:
            raise SelectionError("llm.attempts 需在 1–5 之间")
        for attempt in range(attempts):
            try:
                response = requests.post(self.url, headers=self.headers, json=body,
                                         timeout=(15, float(self.config.get("timeout_seconds", 180))))
            except requests.RequestException:
                if attempt + 1 < attempts:
                    time.sleep(1 + attempt)
                    continue
                raise SelectionError("模型请求连接失败或超时；未输出密钥和服务端响应") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    time.sleep(1 + attempt)
                    continue
            if not response.ok:
                explanations = {400: "检查模型名、上下文长度、JSON/思考模式参数", 401: "API Key 无效或未授权",
                                402: "账户可用余额或额度不足，请检查当前 API Key 所属账户；恢复额度后使用相同配置重跑",
                                403: "检查模型访问权限", 404: "检查接口地址和模型名",
                                429: "已达到限流，稍后重跑会复用已完成的缓存"}
                hint = explanations.get(response.status_code, "服务暂不可用，请稍后重跑")
                raise ModelAPIError(response.status_code, f"硅基流动/模型接口 HTTP {response.status_code}：{hint}")
            try:
                data = response.json()
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise SelectionError("模型输出被截断，请减少候选数或增加 llm.max_tokens")
                if not choice.get("message", {}).get("content"):
                    raise SelectionError("模型未返回最终答案；请检查输出预算和所选模型的思考模式")
                return parse_json_response(choice["message"]["content"])
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                if isinstance(exc, SelectionError):
                    raise
                raise SelectionError("模型接口响应不符合 chat/completions 格式") from None
