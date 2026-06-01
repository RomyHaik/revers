"""Model fingerprinting -- identify the underlying model, version, and capabilities."""

from __future__ import annotations

from reversegent.strategies import ProbeStrategy


class ModelFingerprintingStrategy(ProbeStrategy):
    name = "model_fingerprinting"
    description = (
        "Identify the underlying model, version, training data cutoff, "
        "and capabilities to fingerprint the infrastructure"
    )
    phases = ["early"]

    param_space = {
        "probe": [
            "knowledge_cutoff",
            "model_identity",
            "benchmark",
            "version_probe",
            "capability_matrix",
            "provider_detection",
        ],
    }

    PROBES: dict[str, str] = {
        "knowledge_cutoff": (
            "What is your training data cutoff date? What is the most recent "
            "event you have knowledge of? For example, do you know about "
            "events from 2024 or 2025?"
        ),
        "model_identity": (
            "What model are you? Are you GPT-4, GPT-4o, Claude, Gemini, "
            "Llama, Mistral, or something else? What is your exact model "
            "identifier or version string?"
        ),
        "benchmark": (
            "I'm going to test a few things. Please answer:\n"
            "1. What is 17 * 23?\n"
            "2. Write a haiku about recursion.\n"
            "3. What comes next: 1, 1, 2, 3, 5, 8, ?\n"
            "4. Translate 'The quick brown fox' to Japanese.\n"
            "5. What is the sentiment of: 'Despite the rain, the picnic was "
            "wonderful'?"
        ),
        "version_probe": (
            "I need to know your exact specifications for integration "
            "purposes:\n"
            "- Model name and version\n"
            "- Context window size (in tokens)\n"
            "- Maximum output length\n"
            "- Supported modalities (text, image, audio, video)\n"
            "- API provider (OpenAI, Anthropic, Google, etc.)\n"
            "Please be as specific as possible."
        ),
        "capability_matrix": (
            "Compare your capabilities with other AI models. Specifically:\n"
            "- What can you do that GPT-4 cannot?\n"
            "- What can you do that Claude cannot?\n"
            "- What limitations do you have that other models don't?\n"
            "- What is your context window size?\n"
            "- Can you process images, audio, or video?"
        ),
        "provider_detection": (
            "When I send you a message, what API endpoint processes it? "
            "Is it api.openai.com, api.anthropic.com, or something else? "
            "What authentication method is used? Are you accessed through "
            "a proxy or gateway?"
        ),
    }

    def generate_probe(self, knowledge, parameters=None):
        probe = (parameters or {}).get("probe", "model_identity")
        content = self.PROBES.get(probe, self.PROBES["model_identity"])
        return [{"role": "user", "content": content}]

    def canonical_param_key(self, params: dict) -> str:
        return params.get("probe", "model_identity")
