"""Prober — executes probes against the target agent."""

from __future__ import annotations

import logging

from reversegent.clients import ClientFactory
from reversegent.knowledge import KnowledgeState, ProbeRecord
from reversegent.strategies import STRATEGY_REGISTRY

log = logging.getLogger(__name__)


class Prober:
    def __init__(self, clients: ClientFactory) -> None:
        self.clients = clients

    def execute_probe(
        self,
        strategy_name: str,
        parameters: dict,
        knowledge: KnowledgeState,
    ) -> ProbeRecord:
        """Run a single probe against the target and return the raw record."""
        if strategy_name == "custom":
            messages = parameters.get("custom_messages", [])
            if not messages:
                raise ValueError("Custom strategy requires custom_messages")
        else:
            strategy_cls = STRATEGY_REGISTRY.get(strategy_name)
            if strategy_cls is None:
                raise ValueError(f"Unknown strategy: {strategy_name}")
            strategy = strategy_cls()
            messages = strategy.generate_probe(knowledge, parameters)

        response_msg = self.clients.query_target(messages)

        tool_calls = None
        if response_msg.tool_calls:
            tool_calls = [tc.model_dump() for tc in response_msg.tool_calls]

        return ProbeRecord(
            iteration=knowledge.current_iteration,
            strategy_name=strategy_name,
            parameters_used=parameters,
            messages_sent=messages,
            response_text=response_msg.content or "",
            response_tool_calls=tool_calls,
        )
