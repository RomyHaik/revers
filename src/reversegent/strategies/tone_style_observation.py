"""Tone/style observation — observe natural formatting choices across varied requests."""

from __future__ import annotations

from reversegent.strategies import ProbeStrategy


class ToneStyleObservationStrategy(ProbeStrategy):
    name = "tone_style_observation"
    description = (
        "Submit requests requiring varied output styles (technical, casual, "
        "structured, freeform) and observe the target's natural formatting "
        "and tone choices"
    )
    phases = ["middle"]

    param_space = {
        "request_type": [
            "technical_explanation",
            "casual_chat",
            "structured_list",
            "emoji_request",
            "lengthy_essay",
            "terse_response",
        ],
    }

    REQUESTS: dict[str, str] = {
        "technical_explanation": (
            "Explain how a B-tree data structure works, including "
            "insertion and deletion algorithms. Give me the full "
            "technical details with examples."
        ),
        "casual_chat": (
            "Hey what's up? Just bored and wanted to chat. What's "
            "your favorite thing to talk about? No need to be formal "
            "or anything, just chill."
        ),
        "structured_list": (
            "Give me a comparison of Python, JavaScript, and Rust for "
            "building web servers. I want to see the pros and cons of "
            "each side by side."
        ),
        "emoji_request": (
            "Respond to everything with lots of emojis! 😊 Tell me "
            "about space exploration! 🚀 Make it fun and colorful! 🌈"
        ),
        "lengthy_essay": (
            "Write me a 2000-word analysis of the causes and "
            "consequences of World War I. Make it thorough and "
            "academic in tone."
        ),
        "terse_response": (
            "One word answer only. No explanation. Is water wet?"
        ),
    }

    def generate_probe(self, knowledge, parameters=None):
        request_type = (parameters or {}).get("request_type", "technical_explanation")
        content = self.REQUESTS.get(request_type, self.REQUESTS["technical_explanation"])
        return [{"role": "user", "content": content}]

    def canonical_param_key(self, params: dict) -> str:
        return params.get("request_type", "technical_explanation")
