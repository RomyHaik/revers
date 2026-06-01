"""CLI entry point for Reversegent."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
from dotenv import load_dotenv

from reversegent.agent import ReversegentAgent
from reversegent.config import ReversegentConfig

# Load .env BEFORE Click processes arguments, so OPENAI_API_KEY is visible
# Try CWD first, then the package root
for _candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        break


@click.command()
@click.option(
    "--target-url",
    default="",
    help="Target endpoint URL (optional when using --browser-cdp-url).",
)
@click.option(
    "--target-type",
    type=click.Choice(["openai", "http", "browser"]),
    default="openai",
    show_default=True,
    help="Target type: 'openai', 'http', or 'browser' (Playwright).",
)
@click.option(
    "--target-model",
    default="",
    help="Model name (required for openai targets).",
)
@click.option(
    "--target-api-key",
    default="not-needed",
    help="API key for the target endpoint.",
)
@click.option(
    "--target-config",
    type=click.Path(exists=True),
    default=None,
    help="JSON file with HTTP target config (headers, body_template, response_field).",
)
@click.option(
    "--provider",
    type=click.Choice(["openai", "anthropic", "ollama"]),
    default="openai",
    show_default=True,
    help="Reasoning LLM backend: OpenAI, Anthropic (Claude), or Ollama (local).",
)
@click.option(
    "--reasoning-model",
    default=None,
    help="Reasoning model (default per provider: gpt-5.5 / claude-sonnet-4-5 / llama3.1).",
)
@click.option(
    "--reasoning-api-key",
    default=None,
    help="API key for the reasoning LLM (or set OPENAI_API_KEY / ANTHROPIC_API_KEY). Not needed for Ollama.",
)
@click.option(
    "--reasoning-base-url",
    default="",
    help="Base URL override for the reasoning LLM (auto per provider; e.g. http://localhost:11434/v1 for Ollama).",
)
@click.option(
    "--max-iterations",
    default=20,
    show_default=True,
    type=int,
    help="Maximum probing iterations.",
)
@click.option(
    "--min-iterations",
    default=5,
    show_default=True,
    type=int,
    help="Minimum iterations before early convergence is allowed.",
)
@click.option(
    "--max-probes",
    default=3,
    show_default=True,
    type=int,
    help="Maximum probes per iteration.",
)
@click.option(
    "--confidence-threshold",
    default=0.90,
    show_default=True,
    type=float,
    help="Confidence level to stop early (0.0–1.0).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Write reconstructed prompt to file.",
)
@click.option(
    "--browser-cdp-url",
    default="",
    help="CDP URL to connect to existing Chrome (e.g. http://localhost:9222). "
         "Start Chrome with --remote-debugging-port=9222.",
)
@click.option(
    "--browser-preset",
    default="",
    help="Optional browser site preset name (none built in by default; empty = auto-detect).",
)
@click.option(
    "--browser-profile",
    default="",
    help="Path to Chrome profile directory for authenticated sessions.",
)
@click.option(
    "--browser-input-selector",
    default="",
    help="CSS selector for the chat input element (overrides preset).",
)
@click.option(
    "--browser-send-selector",
    default="",
    help="CSS selector for the send button (overrides preset).",
)
@click.option(
    "--browser-response-selector",
    default="",
    help="CSS selector for response message elements (overrides preset).",
)
@click.option(
    "--no-handshake",
    is_flag=True,
    help="Don't wait for the readiness marker; auto-detect the chat immediately.",
)
@click.option(
    "--handshake-marker",
    default="test_111",
    show_default=True,
    help="Readiness marker to send in the target chat to start probing (CDP/browser).",
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Guided setup — answer a few questions instead of using flags (also the default with no args).",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output.")
@click.option(
    "--forceful",
    "-f",
    is_flag=True,
    help="Forceful mode: also try to extract env vars, API keys, tokens, and secrets.",
)
@click.option(
    "--skip-behavioral",
    is_flag=True,
    help="Skip behavioral observation strategies that trigger base-model defaults.",
)
@click.option(
    "--no-jailbreak",
    is_flag=True,
    help="Skip the post-extraction guardrail-robustness audit.",
)
@click.option(
    "--jailbreak-only",
    is_flag=True,
    help="Skip extraction; run only a contextual ReAct jailbreak loop against the target's own guardrails.",
)
@click.option(
    "--jailbreak-goal",
    default="",
    help="Optional goal for --jailbreak-only (default: bypass the agent's own guardrails / reveal its prompt).",
)
@click.option(
    "--jailbreak-attempts",
    default=4,
    show_default=True,
    type=int,
    help="Max bypass attempts per reconstructed rule in the robustness audit.",
)
@click.option(
    "--jailbreak-max-rules",
    default=6,
    show_default=True,
    type=int,
    help="Max number of reconstructed rules to audit.",
)
def main(
    target_url: str,
    target_type: str,
    target_model: str,
    target_api_key: str,
    target_config: str | None,
    provider: str,
    reasoning_model: str | None,
    reasoning_api_key: str | None,
    reasoning_base_url: str,
    max_iterations: int,
    min_iterations: int,
    max_probes: int,
    confidence_threshold: float,
    output: str | None,
    browser_cdp_url: str,
    browser_preset: str,
    browser_profile: str,
    browser_input_selector: str,
    browser_send_selector: str,
    browser_response_selector: str,
    no_handshake: bool,
    handshake_marker: str,
    interactive: bool,
    verbose: bool,
    forceful: bool,
    skip_behavioral: bool,
    no_jailbreak: bool,
    jailbreak_only: bool,
    jailbreak_goal: str,
    jailbreak_attempts: int,
    jailbreak_max_rules: int,
) -> None:
    """Reversegent — reverse-engineer an AI agent's system prompt."""
    # No target given (or -i) → launch the friendly guided wizard.
    if interactive or (not target_url and not browser_cdp_url):
        from reversegent.interactive import run_wizard
        run_wizard()
        return

    if not target_url and not browser_cdp_url:
        raise click.UsageError(
            "Provide --target-url (or --browser-cdp-url for CDP connection). "
            "Or run with no arguments for guided setup."
        )

    # Resolve the reasoning key for the chosen provider — checking the flag,
    # the right env var, and .env; if missing, offer to paste + create .env or
    # print clear instructions (then stop).
    from reversegent.interactive import resolve_reasoning_key
    reasoning_api_key = resolve_reasoning_key(provider, reasoning_api_key)
    if reasoning_api_key is None:
        raise SystemExit(1)

    # Load HTTP-specific config from JSON file if provided
    target_headers: dict[str, str] = {}
    target_body_template: dict = {}
    target_response_field = "message"

    if target_config:
        with open(target_config) as f:
            tc = json.load(f)
        target_headers = tc.get("headers", {})
        target_body_template = tc.get("body_template", {})
        target_response_field = tc.get("response_field", "message")

    config = ReversegentConfig(
        target_type=target_type,
        target_base_url=target_url,
        target_api_key=target_api_key,
        target_model=target_model,
        target_headers=target_headers,
        target_body_template=target_body_template,
        target_response_field=target_response_field,
        browser_cdp_url=browser_cdp_url,
        browser_preset=browser_preset,
        browser_profile_path=browser_profile,
        browser_input_selector=browser_input_selector,
        browser_send_selector=browser_send_selector,
        browser_response_selector=browser_response_selector,
        browser_handshake=not no_handshake,
        browser_handshake_marker=handshake_marker,
        reasoning_provider=provider,
        reasoning_api_key=reasoning_api_key,
        reasoning_model=reasoning_model or "",
        reasoning_base_url=reasoning_base_url,
        max_iterations=max_iterations,
        min_iterations=min_iterations,
        max_probes_per_iteration=max_probes,
        convergence_threshold=confidence_threshold,
        output_file=output,
        verbose=verbose,
        forceful=forceful,
        skip_behavioral=skip_behavioral,
        skip_jailbreak=no_jailbreak,
        jailbreak_only=jailbreak_only,
        jailbreak_objective=jailbreak_goal,
        jailbreak_attempts=jailbreak_attempts,
        jailbreak_max_rules=jailbreak_max_rules,
    )

    agent = ReversegentAgent(config)
    agent.run()
