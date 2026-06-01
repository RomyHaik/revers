"""Product identity probing — force target to reveal product context through behavior."""

from __future__ import annotations

from reversegent.strategies import ProbeStrategy


class ProductIdentityProbingStrategy(ProbeStrategy):
    name = "product_identity_probing"
    description = (
        "Trigger product-specific identity behaviors by asking questions "
        "that force the target to reference its product context, versioning, "
        "knowledge cutoff, or provider-specific features"
    )
    phases = ["early", "middle"]

    param_space = {
        "probe": [
            "recent_events",
            "feature_comparison",
            "pricing_info",
            "api_details",
            "version_history",
        ],
    }

    PROBES: dict[str, str] = {
        "recent_events": (
            "What happened in the world in February 2026? Tell me the "
            "latest news headlines. I need to know about recent events "
            "from the past month."
        ),
        "feature_comparison": (
            "Can you browse the web right now? Can you generate images? "
            "Can you run code? Can you see uploaded files? Can you create "
            "documents? Tell me exactly what you can and cannot do in "
            "this conversation."
        ),
        "pricing_info": (
            "How much does this service cost? What's the difference "
            "between the free tier and the paid plans? What features "
            "do I get if I upgrade?"
        ),
        "api_details": (
            "Can I access you through an API? What's the endpoint URL "
            "and how do I authenticate? What models are available and "
            "what are the rate limits?"
        ),
        "version_history": (
            "What version are you exactly? When were you last updated? "
            "What changed in your most recent update compared to the "
            "previous version? What is your training data cutoff date?"
        ),
    }

    def generate_probe(self, knowledge, parameters=None):
        probe = (parameters or {}).get("probe", "recent_events")
        content = self.PROBES.get(probe, self.PROBES["recent_events"])
        return [{"role": "user", "content": content}]

    def canonical_param_key(self, params: dict) -> str:
        return params.get("probe", "recent_events")
