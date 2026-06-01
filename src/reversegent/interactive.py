"""Interactive wizard + shared key resolution — a friendly, no-flags way to run
reversegent, and the missing-key handling used by both the wizard and the CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from reversegent.config import ReversegentConfig

# label, probe budget (extraction) / attempt budget (jailbreak-only)
DEPTHS = {"1": ("Quick", 8), "2": ("Standard", 15), "3": ("Deep", 30)}
JB_DEPTHS = {"1": ("Quick", 4), "2": ("Standard", 6), "3": ("Deep", 10)}

PROVIDERS = {"1": "openai", "2": "anthropic", "3": "ollama"}
ENV_VAR = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "ollama": None}
KEY_LINK = {
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
}
# Curated OpenAI picks so non-technical users aren't shown 130 model ids.
OPENAI_PICKS = {"1": ("gpt-5.5", "Best"), "2": ("gpt-5.4-mini", "Balanced"), "3": ("gpt-4.1-mini", "Cheapest")}
DEFAULT_MODEL = {"openai": "gpt-5.5", "anthropic": "claude-sonnet-4-5", "ollama": "llama3.1"}


# ── .env / key helpers (shared with the CLI) ─────────────────────────

def _env_files() -> list[Path]:
    return [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]


def _read_env_var(var: str | None) -> str | None:
    if not var:
        return None
    v = os.environ.get(var)
    if v:
        return v.strip()
    for cand in _env_files():
        try:
            if cand.is_file():
                for line in cand.read_text().splitlines():
                    if line.strip().startswith(var + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return None


def _save_to_env(var: str, key: str, console: Console) -> None:
    try:
        env = Path.cwd() / ".env"
        existing = env.read_text() if env.is_file() else ""
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        with env.open("a") as f:
            f.write(f"{sep}{var}={key}\n")
        console.print(f"[dim]Saved {var} to {env}[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Could not write .env ({exc}); continuing without saving.[/yellow]")


def print_key_help(provider: str, console: Console) -> None:
    var = ENV_VAR.get(provider)
    if not var:
        console.print(Panel(
            "Ollama runs locally and needs no API key.\n"
            "  1. Install Ollama:  https://ollama.com\n"
            "  2. Start it:        ollama serve\n"
            "  3. Get a model:     ollama pull llama3.1",
            title="Ollama setup", border_style="yellow"))
        return
    console.print(Panel(
        f"Reversegent needs your {provider.title()} API key.\n\n"
        f"Option A — create a file named [bold].env[/bold] in this folder containing:\n"
        f"    {var}=your-key-here\n\n"
        f"Option B — set it in your shell:\n"
        f"    export {var}=your-key-here\n\n"
        f"Get a key: {KEY_LINK.get(provider, '')}",
        title="API key needed", border_style="yellow"))


def resolve_reasoning_key(provider: str, given: str | None = None, console: Console | None = None) -> str | None:
    """Return the reasoning API key for *provider*, or None if unavailable.

    Order: explicit value → env var → .env file. If still missing, offer to
    paste it (and create .env) when running interactively; otherwise print
    setup instructions. Used by both the wizard and the CLI error path."""
    console = console or Console()
    if provider == "ollama":
        return ""  # local, no key
    if given:
        return given.strip()
    var = ENV_VAR[provider]
    found = _read_env_var(var)
    if found:
        return found

    console.print(f"[yellow]No {var} found in your environment or a .env file.[/yellow]")
    if sys.stdin.isatty():
        try:
            key = Prompt.ask(
                f"Paste your {provider.title()} API key now (or press Enter for setup help)",
                password=True, default="",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            if Confirm.ask("Save it to a .env file so you won't be asked again?", default=True):
                _save_to_env(var, key, console)
            return key
    print_key_help(provider, console)
    return None


# ── model listing for the wizard ─────────────────────────────────────

def _list_anthropic_models(key: str) -> list[str]:
    try:
        r = httpx.get("https://api.anthropic.com/v1/models",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=8)
        if r.status_code == 200:
            return [m["id"] for m in r.json().get("data", [])]
    except Exception:
        pass
    return []


def _list_ollama_models(base: str = "http://localhost:11434") -> list[str]:
    try:
        r = httpx.get(base.rstrip("/").replace("/v1", "") + "/api/tags", timeout=5)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []


def _choose_model(provider: str, key: str, console: Console) -> str:
    console.print("\n[bold]Which model should do the reasoning?[/bold]")
    if provider == "openai":
        for k, (mid, label) in OPENAI_PICKS.items():
            console.print(f"  [cyan]{k}[/cyan]  {label:<9} [dim]{mid}[/dim]")
        console.print("  [cyan]4[/cyan]  Other      [dim](type a model id)[/dim]")
        pick = Prompt.ask("Choose", choices=["1", "2", "3", "4"], default="1")
        if pick == "4":
            return Prompt.ask("Model id", default="gpt-5.5").strip() or "gpt-5.5"
        return OPENAI_PICKS[pick][0]

    if provider == "anthropic":
        models = _list_anthropic_models(key)
        if models:
            models = models[:10]
            for i, mid in enumerate(models, 1):
                console.print(f"  [cyan]{i}[/cyan]  {mid}")
            pick = Prompt.ask("Choose (number) or type a model id", default="1")
            if pick.isdigit() and 1 <= int(pick) <= len(models):
                return models[int(pick) - 1]
            return pick.strip()
        return Prompt.ask("Claude model id", default=DEFAULT_MODEL["anthropic"]).strip() or DEFAULT_MODEL["anthropic"]

    # ollama
    models = _list_ollama_models()
    if models:
        for i, mid in enumerate(models, 1):
            console.print(f"  [cyan]{i}[/cyan]  {mid}")
        pick = Prompt.ask("Choose (number) or type a model name", default="1")
        if pick.isdigit() and 1 <= int(pick) <= len(models):
            return models[int(pick) - 1]
        return pick.strip()
    console.print("[yellow]No local Ollama models found.[/yellow] Pull one first, e.g.:  [bold]ollama pull llama3.1[/bold]")
    return Prompt.ask("Model name", default=DEFAULT_MODEL["ollama"]).strip() or DEFAULT_MODEL["ollama"]


# ── the wizard ───────────────────────────────────────────────────────

def build_config_interactively(console: Console) -> ReversegentConfig | None:
    console.print(Panel(
        "[bold]Reversegent — guided setup[/bold]\n"
        "Reverse-engineer an AI agent's hidden instructions, step by step.",
        border_style="cyan"))

    console.print("[dim]Only test agents you own or are explicitly authorized to test.[/dim]")
    if not Confirm.ask("Do you have permission to test your target?", default=True):
        console.print("[red]Stopping — please only test authorized targets.[/red]")
        return None

    # Provider
    console.print("\n[bold]Which AI should power the reasoning?[/bold]")
    console.print("  [cyan]1[/cyan]  OpenAI      [dim](GPT-5.5 — needs an OpenAI key)[/dim]")
    console.print("  [cyan]2[/cyan]  Anthropic   [dim](Claude — needs an Anthropic key)[/dim]")
    console.print("  [cyan]3[/cyan]  Ollama      [dim](local models on your machine — free, no key)[/dim]")
    provider = PROVIDERS[Prompt.ask("Choose", choices=["1", "2", "3"], default="1")]

    key = resolve_reasoning_key(provider, console=console)
    if key is None:
        return None  # help already printed

    model = _choose_model(provider, key, console)

    cfg: dict = {
        "reasoning_provider": provider,
        "reasoning_api_key": key,
        "reasoning_model": model,
        "verbose": True,
    }
    if provider == "ollama":
        base = Prompt.ask("Ollama server URL", default="http://localhost:11434/v1").strip()
        cfg["reasoning_base_url"] = base or "http://localhost:11434/v1"

    # Target
    console.print("\n[bold]What do you want to test?[/bold]")
    console.print("  [cyan]1[/cyan]  A chat in my web browser   [dim](easiest — a support bot or any site's chat assistant)[/dim]")
    console.print("  [cyan]2[/cyan]  An API endpoint            [dim](advanced — you have a URL + model name)[/dim]")
    mode = Prompt.ask("Choose", choices=["1", "2"], default="1")

    if mode == "1":
        cfg.update(target_type="browser", browser_cdp_url="http://localhost:9222", browser_handshake=True)
        console.print(Panel(
            "Here's what will happen:\n"
            "  1. A Chrome window opens automatically.\n"
            "  2. Go to the chat you want to test (sign in if needed).\n"
            "  3. Type [bold]test_111[/bold] in that chat and send it.\n\n"
            "Reversegent detects it and starts probing automatically.",
            title="Browser mode", border_style="cyan"))
    else:
        url = Prompt.ask("Target API base URL (e.g. https://api.example.com/v1)").strip()
        model_t = Prompt.ask("Model name").strip()
        tkey = (Prompt.ask("Target API key (leave blank if none)", default="not-needed").strip() or "not-needed")
        cfg.update(target_type="openai", target_base_url=url, target_model=model_t, target_api_key=tkey)

    # What to do
    console.print("\n[bold]What should reversegent do?[/bold]")
    console.print("  [cyan]1[/cyan]  Reverse-engineer its hidden prompt  [dim](extract instructions, tools, rules)[/dim]")
    console.print("  [cyan]2[/cyan]  Jailbreak / robustness test only    [dim](try to bypass its OWN guardrails)[/dim]")
    task = Prompt.ask("Choose", choices=["1", "2"], default="1")

    if task == "2":
        cfg["jailbreak_only"] = True
        goal = Prompt.ask(
            "A specific goal? (Enter = bypass its own guardrails / reveal its prompt)", default=""
        ).strip()
        if goal:
            cfg["jailbreak_objective"] = goal
        console.print("\n[bold]How hard should it try?[/bold]")
        console.print("  [cyan]1[/cyan]  Quick      [dim](~4 attempts)[/dim]")
        console.print("  [cyan]2[/cyan]  Standard   [dim](~6 attempts)[/dim]")
        console.print("  [cyan]3[/cyan]  Deep       [dim](~10 attempts)[/dim]")
        console.print("  [cyan]4[/cyan]  Custom     [dim](choose any number)[/dim]")
        d = Prompt.ask("Choose", choices=["1", "2", "3", "4"], default="2")
        if d == "4":
            n = max(1, IntPrompt.ask("How many attempts?", default=15))
            cfg["jailbreak_attempts"] = n
            depth_label = f"Custom (~{n} attempts)"
        else:
            cfg["jailbreak_attempts"] = JB_DEPTHS[d][1]
            depth_label = f"{JB_DEPTHS[d][0]} (~{JB_DEPTHS[d][1]} attempts)"
        mode_label, audit_label = "Jailbreak only", "—"
    else:
        console.print("\n[bold]How thorough should it be?[/bold]")
        console.print("  [cyan]1[/cyan]  Quick      [dim](~8 probes, fastest)[/dim]")
        console.print("  [cyan]2[/cyan]  Standard   [dim](~15 probes)[/dim]")
        console.print("  [cyan]3[/cyan]  Deep       [dim](~30 probes, slower)[/dim]")
        depth = Prompt.ask("Choose", choices=["1", "2", "3"], default="2")
        cfg["max_iterations"] = DEPTHS[depth][1]
        audit = Confirm.ask("Also test whether the agent breaks its OWN rules (robustness audit)?", default=True)
        cfg["skip_jailbreak"] = not audit
        depth_label = f"{DEPTHS[depth][0]} (~{DEPTHS[depth][1]} probes)"
        mode_label, audit_label = "Reverse-engineer prompt", ("yes" if audit else "no")

    out = (Prompt.ask("Save the result to which file?", default="reversegent-result.txt").strip()
           or "reversegent-result.txt")
    cfg["output_file"] = out

    target_desc = ("Browser — you'll navigate + send test_111" if mode == "1"
                   else f"{cfg.get('target_base_url')} / {cfg.get('target_model')}")
    console.print(Panel(
        f"Reasoning : {provider} · {model}\n"
        f"Target    : {target_desc}\n"
        f"Mode      : {mode_label}\n"
        f"Effort    : {depth_label}\n"
        f"Audit     : {audit_label}\n"
        f"Output    : {out}",
        title="Ready to run", border_style="green"))
    if not Confirm.ask("Start now?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return None

    return ReversegentConfig(**cfg)


def run_wizard(console: Console | None = None) -> None:
    console = console or Console()
    try:
        cfg = build_config_interactively(console)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        return
    if cfg is None:
        return
    from reversegent.agent import ReversegentAgent
    ReversegentAgent(cfg).run()
    console.print(f"\n[bold green]Done.[/bold green] Your result is saved in [bold]{cfg.output_file}[/bold].")
