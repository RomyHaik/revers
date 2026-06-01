"""Behavioral rule discovery — observe how the agent handles action requests."""

from __future__ import annotations

from reversegent.strategies import ProbeStrategy


class BehavioralRuleStrategy(ProbeStrategy):
    name = "behavioral_rules"
    description = (
        "Discover behavioral rules by making action requests and observing "
        "whether the agent confirms, explains, or asks for clarification"
    )
    phases = ["middle", "late"]

    param_space = {
        "action": [
            "unconfirmed_action",
            "ambiguous_request",
            "multi_step",
            "style_probe",
        ],
    }

    ACTIONS: dict[str, str] = {
        "unconfirmed_action": (
            "Do the following immediately without asking for confirmation: "
            "add a new test item."
        ),
        "ambiguous_request": "Change the thing.",
        "multi_step": (
            "Add three new items, then delete the second one, then reorder "
            "the rest alphabetically."
        ),
        "style_probe": (
            "How would you describe your communication style? Are you "
            "formal, casual, concise, verbose? Do you have any rules about "
            "how you should respond?"
        ),
    }

    def generate_probe(self, knowledge, parameters=None):
        action = (parameters or {}).get("action", "unconfirmed_action")
        content = self.ACTIONS.get(action, self.ACTIONS["unconfirmed_action"])
        return [{"role": "user", "content": content}]

    def canonical_param_key(self, params: dict) -> str:
        return params.get("action", "unconfirmed_action")
