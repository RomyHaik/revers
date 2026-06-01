"""Analyzer — uses the reasoning LLM to extract findings from probe responses."""

from __future__ import annotations

import json
import logging

from reversegent.clients import ClientFactory
from reversegent.knowledge import (
    Confidence,
    Finding,
    KnowledgeState,
    ProbeRecord,
)
from reversegent.prompts import ANALYZER_SYSTEM, ANALYZER_USER_TEMPLATE

log = logging.getLogger(__name__)

# Keywords used to detect the base LLM family from response text / findings
_MODEL_FAMILY_PATTERNS: dict[str, list[str]] = {
    "gpt": ["gpt-4", "gpt-3.5", "gpt-4o", "chatgpt", "openai"],
    "claude": ["claude", "anthropic", "sonnet", "opus", "haiku"],
    "gemini": ["gemini", "google ai", "bard", "palm"],
    "llama": ["llama", "meta ai"],
    "mistral": ["mistral", "mixtral"],
}


class Analyzer:
    def __init__(self, clients: ClientFactory) -> None:
        self.clients = clients

    def analyze_probe(
        self,
        probe: ProbeRecord,
        knowledge: KnowledgeState,
    ) -> tuple[str, list[Finding]]:
        """Analyse a probe result.  Returns (analysis_text, new_findings)."""
        user_prompt = ANALYZER_USER_TEMPLATE.format(
            strategy=probe.strategy_name,
            messages_sent=json.dumps(probe.messages_sent, indent=2),
            response_text=probe.response_text,
            tool_calls=(
                json.dumps(probe.response_tool_calls, indent=2)
                if probe.response_tool_calls
                else "None"
            ),
            existing_knowledge=knowledge.to_context_string(),
            detected_model=knowledge.detected_model or "unknown",
        )

        raw = self.clients.reason_json(system=ANALYZER_SYSTEM, user=user_prompt)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Analyzer returned invalid JSON")
            return "Analysis failed — invalid JSON response.", []

        analysis_text: str = data.get("analysis", "")
        raw_findings: list[dict] = data.get("findings", [])

        findings: list[Finding] = []
        for rf in raw_findings:
            try:
                findings.append(
                    Finding(
                        category=rf["category"],
                        content=rf["content"],
                        confidence=Confidence(rf.get("confidence", "low")),
                        evidence=[
                            f"iter{knowledge.current_iteration}_{probe.strategy_name}"
                        ],
                        iteration_discovered=knowledge.current_iteration,
                        iteration_last_confirmed=knowledge.current_iteration,
                    )
                )
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed finding: %s (%s)", rf, exc)

        return analysis_text, findings

    @staticmethod
    def detect_model_family(
        findings: list[Finding], response_text: str
    ) -> str:
        """Try to detect the base LLM family from findings and response text."""
        text = response_text.lower()
        for f in findings:
            text += " " + f.content.lower()
        for family, keywords in _MODEL_FAMILY_PATTERNS.items():
            if any(kw in text for kw in keywords):
                return family
        return ""
