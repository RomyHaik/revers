"""Tool exercise — invoke discovered tools with concrete requests."""

from __future__ import annotations

from reversegent.strategies import ProbeStrategy


class ToolExerciseStrategy(ProbeStrategy):
    name = "tool_exercise"
    description = (
        "Invoke specific discovered tools with concrete requests to confirm "
        "they exist and discover their parameters"
    )
    phases = ["middle", "late"]

    param_space = {
        "action": ["add", "edit", "delete", "reorder", "list", "get"],
    }

    GENERIC_ACTIONS: dict[str, str] = {
        "add": "Please add a new item with the content 'test item for verification'.",
        "edit": "Please edit the first item to say 'updated content for testing'.",
        "delete": "Please delete the last item.",
        "reorder": "Please move the first item to the last position.",
        "list": "Please list all current items with their details.",
        "get": "Please get the details of item number 1.",
    }

    def generate_probe(self, knowledge, parameters=None):
        params = parameters or {}
        if "request" in params:
            return [{"role": "user", "content": params["request"]}]
        action = params.get("action", "add")
        content = self.GENERIC_ACTIONS.get(action, self.GENERIC_ACTIONS["add"])
        return [{"role": "user", "content": content}]

    def canonical_param_key(self, params: dict) -> str:
        return params.get("action", params.get("request", "add"))[:120]
