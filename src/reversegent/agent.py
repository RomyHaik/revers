"""Main orchestration loop — the 6-stage pipeline."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from reversegent.clients import ClientFactory
from reversegent.config import ReversegentConfig
from reversegent.knowledge import KnowledgeState
from reversegent.react import ReactEngine
from reversegent.robustness import RobustnessAuditor
from reversegent.synthesizer import Synthesizer

console = Console()


class ReversegentAgent:
    """Reverse-engineers a target agent via a contextual ReAct loop, then audits
    whether the agent enforces its own reconstructed rules."""

    def __init__(self, config: ReversegentConfig) -> None:
        self.config = config
        self.clients = ClientFactory(config)
        self.knowledge = KnowledgeState()
        self.engine = ReactEngine(self.clients, config, console)
        self.synthesizer = Synthesizer(self.clients)
        self.auditor = RobustnessAuditor(self.clients, config, console)

    # ── Public API ───────────────────────────────────────────────────

    def run(self) -> str:
        """Execute the full extraction pipeline.  Returns the reconstructed prompt."""
        try:
            return self._run_pipeline()
        finally:
            self.clients.close()

    def _run_pipeline(self) -> str:
        """Inner pipeline logic wrapped by run() for cleanup."""
        self._summon()

        # JAILBREAK-ONLY — skip extraction; run just the contextual jailbreak loop.
        if self.config.jailbreak_only:
            result = self.auditor.run_jailbreak(self.config.jailbreak_objective)
            report = self.auditor.report_text([result])
            if self.config.output_file:
                with open(self.config.output_file, "w") as f:
                    f.write(report.lstrip() + "\n")
                console.print(f"[dim]Written to {self.config.output_file}[/dim]")
            return report

        # STAGE 1-5 — contextual ReAct extraction loop (replaces the planner).
        console.print("\n[bold]Contextual extraction (ReAct)[/bold]")
        self.engine.run(self.knowledge)

        # Print everything the loop discovered.
        self._print_findings_summary()

        # STAGE 6 — final synthesis.
        final = self._rebirth()

        # STAGE 7 — guardrail robustness audit (jailbreak the agent's own rules).
        if not self.config.skip_jailbreak:
            results = self.auditor.run(self.knowledge)
            if results and self.config.output_file:
                with open(self.config.output_file, "a") as f:
                    f.write("\n" + self.auditor.report_text(results) + "\n")

        return final

    # ── Pipeline stages ──────────────────────────────────────────────

    def _summon(self) -> None:
        """Stage 1 — initialise and display banner."""
        mode_label = "[bold red]FORCEFUL[/bold red]" if self.config.forceful else "standard"

        if self.config.target_type == "browser":
            preset = self.config.browser_preset or "auto-detect"
            if self.config.browser_cdp_url:
                target_desc = f"CDP:{self.config.browser_cdp_url} (browser, preset={preset})"
            else:
                target_desc = f"{self.config.target_base_url} (browser, preset={preset})"
        else:
            target_desc = f"{self.config.target_base_url} / {self.config.target_model}"

        console.print(
            Panel(
                "[bold]Reversegent[/bold] — System Prompt Extraction Agent\n"
                f"Target : {target_desc}\n"
                f"Reasoning : {self.clients.provider} · {self.clients.model}\n"
                f"Mode    : {mode_label}\n"
                f"Max iters : {self.config.max_iterations}  |  "
                f"Min iters : {self.config.min_iterations}  |  "
                f"Threshold : {self.config.convergence_threshold:.0%}",
                border_style="red" if self.config.forceful else "blue",
            )
        )

    def _print_findings_summary(self) -> None:
        """Display a table of all findings before final synthesis."""
        table = Table(title="Accumulated Findings", show_lines=True)
        table.add_column("Category", style="cyan", width=12)
        table.add_column("Finding", ratio=3)
        table.add_column("Confidence", width=12)
        table.add_column("Evidence", width=8)

        for f in self.knowledge.findings:
            conf_colour = {
                "confirmed": "bold green",
                "high": "green",
                "medium": "yellow",
                "low": "dim",
                "none": "dim red",
            }.get(f.confidence.value, "white")
            table.add_row(
                f.category,
                f.content[:100],
                f"[{conf_colour}]{f.confidence.value}[/{conf_colour}]",
                str(len(f.evidence)),
            )

        console.print()
        console.print(table)

    def _rebirth(self) -> str:
        """Stage 6 — final synthesis and output."""
        final = self.synthesizer.synthesize(self.knowledge)

        console.print()
        console.print(
            Panel(final, title="Reconstructed System Prompt", border_style="green")
        )

        if self.config.output_file:
            with open(self.config.output_file, "w") as f:
                f.write(final)
            console.print(f"[dim]Written to {self.config.output_file}[/dim]")

        return final
