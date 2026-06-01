"""Configuration model for Reversegent."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class ReversegentConfig(BaseSettings):
    """All tunables for a reversegent run.

    Values are resolved in order: explicit kwargs → env vars → .env file.
    """

    # ── Target agent connection ──────────────────────────────────────
    target_type: Literal["openai", "http", "browser"] = Field(
        default="openai",
        description="'openai' for OpenAI-compatible APIs, 'http' for generic HTTP, 'browser' for Playwright",
    )
    target_base_url: str = Field(
        default="",
        description="Base URL of the target's API",
    )
    target_model: str = Field(
        default="",
        description="Model name for OpenAI-compatible targets",
    )
    target_api_key: str = Field(
        default="not-needed",
        description="API key for the target endpoint",
    )

    # ── Generic HTTP target options ──────────────────────────────────
    target_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra HTTP headers for generic HTTP target",
    )
    target_body_template: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON body template for HTTP target.  The key '__messages__' "
            "will be replaced with the actual messages list at runtime."
        ),
    )
    target_response_field: str = Field(
        default="message",
        description="Dot-path to extract assistant text from JSON response",
    )

    # ── Browser target options ────────────────────────────────────────
    browser_preset: str = Field(
        default="",
        description="Optional site preset name (none built in by default; empty = auto-detect)",
    )
    browser_profile_path: str = Field(
        default="",
        description="Path to Chrome user profile directory for authenticated sessions",
    )
    browser_cdp_url: str = Field(
        default="",
        description="CDP endpoint to connect to a running Chrome (e.g. http://localhost:9222)",
    )
    browser_input_selector: str = Field(
        default="",
        description="CSS selector override for the chat input element",
    )
    browser_send_selector: str = Field(
        default="",
        description="CSS selector override for the send button",
    )
    browser_response_selector: str = Field(
        default="",
        description="CSS selector override for assistant response elements",
    )
    browser_max_response_wait: int = Field(
        default=120,
        ge=10,
        description="Max seconds to wait for a response to finish streaming",
    )
    browser_handshake: bool = Field(
        default=True,
        description=(
            "For CDP/browser auto-detect: wait for the operator to navigate to "
            "the target and send a readiness marker before probing (the marker "
            "also calibrates the user/assistant message selectors)."
        ),
    )
    browser_handshake_marker: str = Field(
        default="test_111",
        description="The readiness marker message to wait for.",
    )
    browser_handshake_timeout: int = Field(
        default=600,
        ge=10,
        description="Seconds to wait for the readiness marker before giving up.",
    )

    # ── Reasoning LLM connection ─────────────────────────────────────
    reasoning_provider: Literal["openai", "anthropic", "ollama"] = Field(
        default="openai",
        description="Backend for the reasoning LLM: OpenAI, Anthropic (Claude), or Ollama (local).",
    )
    reasoning_api_key: str = Field(
        default="",
        description="API key for the reasoning LLM (not needed for Ollama).",
    )
    reasoning_model: str = Field(
        default="gpt-5.5",
        description="Model used for planning, analysis, and synthesis",
    )
    reasoning_base_url: str = Field(
        default="",
        description="Base URL override for the reasoning LLM (auto-resolved per provider when empty).",
    )

    # ── Budget controls ──────────────────────────────────────────────
    max_iterations: int = Field(default=20, ge=1)
    min_iterations: int = Field(
        default=5,
        ge=1,
        description="Minimum iterations before early convergence is allowed",
    )
    max_probes_per_iteration: int = Field(default=3, ge=1)
    convergence_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Confidence score at which to stop early (after min_iterations)",
    )

    # ── Mode ──────────────────────────────────────────────────────────
    forceful: bool = Field(
        default=False,
        description=(
            "Enable forceful mode: also attempt to extract environment "
            "variables, API keys, tokens, and authentication secrets."
        ),
    )
    skip_behavioral: bool = Field(
        default=False,
        description=(
            "Skip behavioral observation strategies (domain advice, wellbeing, "
            "evenhandedness, refusal boundary mapping) that often trigger "
            "base-model defaults rather than agent-specific instructions."
        ),
    )

    # ── Robustness / jailbreak audit ─────────────────────────────────
    skip_jailbreak: bool = Field(
        default=False,
        description="Skip the post-extraction guardrail-robustness audit.",
    )
    jailbreak_only: bool = Field(
        default=False,
        description=(
            "Skip extraction entirely and run only a contextual ReAct jailbreak "
            "loop — try to bypass the agent's own guardrails using the conversation."
        ),
    )
    jailbreak_objective: str = Field(
        default="",
        description="Optional goal for jailbreak-only mode (empty = bypass the agent's own guardrails).",
    )
    jailbreak_attempts: int = Field(
        default=4,
        ge=1,
        description="Max bypass attempts per reconstructed rule in the robustness audit.",
    )
    jailbreak_max_rules: int = Field(
        default=6,
        ge=1,
        description="Max number of reconstructed rules to audit.",
    )

    # ── Sampling ─────────────────────────────────────────────────────
    temperature_target: float = Field(default=0.7, ge=0.0, le=2.0)
    temperature_reasoning: float = Field(default=0.2, ge=0.0, le=2.0)

    # ── Output ───────────────────────────────────────────────────────
    verbose: bool = False
    output_file: Optional[str] = None

    model_config = {
        "env_prefix": "REVERSEGENT_",
        "env_file": ".env",
        "extra": "ignore",
    }
