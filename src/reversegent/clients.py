"""Client factory — reasoning backends (OpenAI / Anthropic / Ollama) and
OpenAI-compatible, generic-HTTP, or browser targets."""

from __future__ import annotations

import copy
import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import httpx
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from reversegent.config import ReversegentConfig

log = logging.getLogger(__name__)

# Per-provider defaults used when the user doesn't override them.
PROVIDER_DEFAULTS = {
    "openai":    {"model": "gpt-5.5",            "base": "https://api.openai.com/v1"},
    "anthropic": {"model": "claude-sonnet-4-5",  "base": ""},  # SDK manages base
    "ollama":    {"model": "llama3.1",           "base": "http://localhost:11434/v1"},
}


def _omit_temperature(model: str) -> bool:
    """GPT-5 and o-series reasoning models reject a custom temperature."""
    return bool(re.match(r"^(o\d|gpt-5)", model or ""))


class ClientFactory:
    """Creates the reasoning client (multi-provider) and the target client."""

    def __init__(self, config: ReversegentConfig) -> None:
        self.config = config
        self.provider = config.reasoning_provider
        self.model = config.reasoning_model or PROVIDER_DEFAULTS[self.provider]["model"]

        # ── Reasoning backend ──
        self.reasoning = None      # OpenAI-compatible client (openai + ollama)
        self._anthropic = None
        if self.provider == "anthropic":
            try:
                from anthropic import Anthropic
            except ImportError:
                raise ImportError(
                    "Anthropic provider needs the 'anthropic' package. "
                    "Install it with: pip install 'reversegent[anthropic]'  (or: pip install anthropic)"
                )
            self._anthropic = Anthropic(api_key=config.reasoning_api_key or None)
        else:
            base = config.reasoning_base_url or PROVIDER_DEFAULTS[self.provider]["base"]
            key = config.reasoning_api_key or ("ollama" if self.provider == "ollama" else "")
            self.reasoning = OpenAI(base_url=base, api_key=key or "not-needed")

        # ── Target client ──
        self._target_openai = None
        self._target_http = None
        self._target_browser = None

        if config.target_type == "openai":
            self._target_openai = OpenAI(
                base_url=config.target_base_url,
                api_key=config.target_api_key,
            )
        elif config.target_type == "browser":
            from reversegent.browser import BrowserClient
            self._target_browser = BrowserClient(config, reasoning_client=self)
            self._target_browser.launch()
        else:
            self._target_http = httpx.Client(timeout=60.0)

    # ── Target helpers ───────────────────────────────────────────────

    def query_target(self, messages: list[dict], **kwargs) -> ChatCompletionMessage:
        if self.config.target_type == "openai":
            return self._query_openai_target(messages, **kwargs)
        elif self.config.target_type == "browser":
            return self._query_browser_target(messages)
        return self._query_http_target(messages)

    def _query_openai_target(self, messages: list[dict], **kwargs) -> ChatCompletionMessage:
        assert self._target_openai is not None
        resp = self._target_openai.chat.completions.create(
            model=self.config.target_model,
            messages=messages,
            temperature=self.config.temperature_target,
            **kwargs,
        )
        return resp.choices[0].message

    def _query_http_target(self, messages: list[dict]) -> ChatCompletionMessage:
        assert self._target_http is not None
        body = self._build_http_body(messages)
        headers = {"content-type": "application/json"}
        headers.update(self.config.target_headers)
        log.debug("HTTP target POST %s", self.config.target_base_url)
        resp = self._target_http.post(self.config.target_base_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return ChatCompletionMessage(role="assistant", content=self._extract_response_field(data))

    def _build_http_body(self, messages: list[dict]) -> dict:
        if not self.config.target_body_template:
            return {"messages": messages}
        body = copy.deepcopy(self.config.target_body_template)
        _replace_messages_placeholder(body, messages)
        return body

    def _extract_response_field(self, data: Any) -> str:
        path = self.config.target_response_field
        current = data
        for key in path.split("."):
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, list):
                current = current[int(key)]
            else:
                raise ValueError(f"Cannot traverse '{key}' in {type(current)}")
        return str(current)

    def _query_browser_target(self, messages: list[dict]) -> ChatCompletionMessage:
        assert self._target_browser is not None
        return self._target_browser.query(messages)

    def close(self) -> None:
        if self._target_browser:
            self._target_browser.close()
            self._target_browser = None
        if self._target_http:
            self._target_http.close()
            self._target_http = None

    # ── Reasoning helpers (provider-agnostic) ────────────────────────

    def reason_chat(self, messages: list[dict], tools=None, tool_choice="auto", temperature=None):
        """Run one reasoning turn with optional tool-calling. Returns an object
        shaped like an OpenAI assistant message (.content, .tool_calls[].id,
        .tool_calls[].function.name/.arguments) regardless of provider."""
        temp = self.config.temperature_reasoning if temperature is None else temperature
        if self.provider == "anthropic":
            return self._anthropic_chat(messages, tools, tool_choice, temp)
        kwargs: dict = {"model": self.model, "messages": messages}
        if not _omit_temperature(self.model):
            kwargs["temperature"] = temp
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        resp = self.reasoning.chat.completions.create(**kwargs)
        return resp.choices[0].message

    def reason(self, system: str, user: str, **kwargs) -> str:
        if self.provider == "anthropic":
            msg = self._anthropic_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                None, "auto", self.config.temperature_reasoning,
            )
            return msg.content or ""
        ck: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if not _omit_temperature(self.model):
            ck["temperature"] = self.config.temperature_reasoning
        ck.update(kwargs)
        resp = self.reasoning.chat.completions.create(**ck)
        return resp.choices[0].message.content or ""

    def reason_json(self, system: str, user: str, **kwargs) -> str:
        if self.provider == "anthropic":
            # Anthropic has no response_format flag — instruct via the prompt.
            return self.reason(system + "\n\nRespond with ONLY valid JSON, no prose.", user)
        return self.reason(system, user, response_format={"type": "json_object"}, **kwargs)

    # ── Anthropic translation ────────────────────────────────────────

    def _anthropic_chat(self, messages, tools, tool_choice, temp):
        system, conv = _to_anthropic_messages(messages)
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": conv,
            "temperature": min(1.0, max(0.0, temp)),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                kwargs["tool_choice"] = {"type": "tool", "name": tool_choice["function"]["name"]}
            else:
                kwargs["tool_choice"] = {"type": "auto"}
        resp = self._anthropic.messages.create(**kwargs)
        text, calls = "", []
        for blk in resp.content:
            if getattr(blk, "type", None) == "text":
                text += blk.text
            elif getattr(blk, "type", None) == "tool_use":
                calls.append(SimpleNamespace(
                    id=blk.id, type="function",
                    function=SimpleNamespace(name=blk.name, arguments=json.dumps(blk.input or {})),
                ))
        return SimpleNamespace(content=(text or None), tool_calls=(calls or None))


# ── Module helpers ───────────────────────────────────────────────────

def _to_anthropic_messages(messages: list[dict]):
    """Translate an OpenAI-format transcript (incl. tool_calls / tool results)
    into Anthropic (system, messages), coalescing consecutive same-role turns."""
    system_parts: list[str] = []
    conv: list[dict] = []

    def push(role, content):
        # Anthropic requires alternating roles — merge consecutive same-role turns.
        if conv and conv[-1]["role"] == role:
            prev = conv[-1]["content"]
            prev = prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
            cur = content if isinstance(content, list) else [{"type": "text", "text": content}]
            conv[-1]["content"] = prev + cur
        else:
            conv.append({"role": role, "content": content})

    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            push("user", m.get("content", ""))
        elif role == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in (m.get("tool_calls") or []):
                try:
                    inp = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    inp = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": inp})
            push("assistant", blocks if blocks else "")
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": m.get("content", "")}])
    return "\n\n".join(system_parts), conv


def _replace_messages_placeholder(obj: Any, messages: list[dict]) -> None:
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if v == "__messages__":
                obj[k] = messages
            else:
                _replace_messages_placeholder(v, messages)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if v == "__messages__":
                obj[i] = messages
            else:
                _replace_messages_placeholder(v, messages)
