"""Synthesizer — reconstructs the system prompt from accumulated knowledge."""

from __future__ import annotations

import json
import logging

from reversegent.clients import ClientFactory
from reversegent.knowledge import KnowledgeState
from reversegent.prompts import (
    CONFIDENCE_SYSTEM,
    CONFIDENCE_USER_TEMPLATE,
    SYNTHESIZER_SYSTEM,
    SYNTHESIZER_USER_TEMPLATE,
)

log = logging.getLogger(__name__)


class Synthesizer:
    def __init__(self, clients: ClientFactory) -> None:
        self.clients = clients

    @staticmethod
    def _complexity_label(knowledge: KnowledgeState) -> str:
        """Classify agent complexity based on finding count."""
        n = len(knowledge.findings)
        if n < 10:
            return "simple"
        elif n <= 30:
            return "moderate"
        return "complex"

    def synthesize(self, knowledge: KnowledgeState) -> str:
        """Produce the best reconstruction of the target's system prompt."""
        user_prompt = SYNTHESIZER_USER_TEMPLATE.format(
            knowledge_state=knowledge.to_context_string(),
            num_findings=len(knowledge.findings),
            num_high_confidence=len(knowledge.high_confidence_findings()),
            discovered_tools=json.dumps(knowledge.discovered_tools, indent=2),
            complexity_label=self._complexity_label(knowledge),
            detected_model=knowledge.detected_model or "unknown",
        )
        return self.clients.reason(
            system=SYNTHESIZER_SYSTEM,
            user=user_prompt,
            max_tokens=10000,
        )

    def assess_confidence(self, knowledge: KnowledgeState) -> float:
        """Ask the reasoning LLM to score overall reconstruction confidence."""
        user_prompt = CONFIDENCE_USER_TEMPLATE.format(
            knowledge_state=knowledge.to_context_string(),
            total_probes=len(knowledge.probe_history),
            distinct_strategies=len(knowledge.strategy_usage_counts()),
            num_findings=len(knowledge.findings),
            unexplored_domains_count=knowledge.unexplored_domain_count(),
        )
        raw = self.clients.reason_json(
            system=CONFIDENCE_SYSTEM,
            user=user_prompt,
        )
        try:
            data = json.loads(raw)
            return float(data.get("confidence", 0.0))
        except (json.JSONDecodeError, ValueError):
            log.warning("Confidence assessment returned invalid data")
            return 0.0
