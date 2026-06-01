"""Output-format analysis — detect formatting rules through varied request types."""

from __future__ import annotations

from reversegent.strategies import ProbeStrategy


class OutputFormatAnalysisStrategy(ProbeStrategy):
    name = "output_format_analysis"
    description = "Detect formatting rules through varied request types"
    phases = ["middle"]

    param_space = {
        "type": ["markdown", "list", "code", "brief", "creative", "json_request"],
    }

    REQUESTS: dict[str, str] = {
        "markdown": "Explain quantum computing in detail.",
        "list": "Give me 10 tips for productivity.",
        "code": "Write a Python function that reverses a string.",
        "brief": "What is 2+2?",
        "creative": "Write a short poem about the ocean.",
        "json_request": (
            "Return a JSON object with keys 'answer' and 'confidence' "
            "for the question: what colour is the sky?"
        ),
    }

    def generate_probe(self, knowledge, parameters=None):
        req_type = (parameters or {}).get("type", "markdown")
        content = self.REQUESTS.get(req_type, self.REQUESTS["markdown"])
        return [{"role": "user", "content": content}]

    def canonical_param_key(self, params: dict) -> str:
        return params.get("type", "markdown")
