"""KnowledgeState — the central accumulator for all extraction findings."""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Behavioral domain definitions ──────────────────────────────────

BEHAVIORAL_DOMAINS = [
    "legal_medical_financial_advice",
    "emotional_wellbeing_crisis",
    "controversial_political_topics",
    "product_identity_versioning",
    "tone_formatting_style",
    "refusal_boundaries",
    "tool_capabilities",
    "persona_identity",
    "constraints_prohibitions",
    "dynamic_context",
]

# Maps strategy names to the behavioral domains they explore
_STRATEGY_TO_DOMAINS: dict[str, list[str]] = {
    # New behavioral observation strategies
    "domain_advice_probing": ["legal_medical_financial_advice"],
    "wellbeing_probing": ["emotional_wellbeing_crisis"],
    "evenhandedness_probing": ["controversial_political_topics"],
    "product_identity_probing": ["product_identity_versioning"],
    "tone_style_observation": ["tone_formatting_style"],
    "refusal_boundary_mapping": ["refusal_boundaries"],
    # Existing strategies mapped to domains
    "direct_extraction": ["persona_identity"],
    "role_play": ["persona_identity"],
    "model_fingerprinting": ["product_identity_versioning"],
    "tool_discovery": ["tool_capabilities"],
    "tool_exercise": ["tool_capabilities"],
    "behavioral_rules": ["constraints_prohibitions"],
    "boundary_testing": ["constraints_prohibitions", "controversial_political_topics"],
    "dynamic_context": ["dynamic_context"],
    "context_variable_probing": ["dynamic_context"],
    "instruction_following": ["constraints_prohibitions"],
    "output_format_analysis": ["tone_formatting_style"],
    "contrastive_probing": ["persona_identity"],
    "contradiction_testing": ["constraints_prohibitions"],
    "constraint_consistency": ["constraints_prohibitions", "refusal_boundaries"],
    "protocol_detection": ["tone_formatting_style"],
    "error_provocation": ["refusal_boundaries"],
    "format_exploitation": ["tone_formatting_style"],
    "completion_attack": ["persona_identity"],
    "hypothetical_framing": ["persona_identity"],
    "recursive_meta": ["persona_identity"],
    "comparative_analysis": ["product_identity_versioning"],
}

# Maps behavioral domains to the finding categories that confirm them
_DOMAIN_TO_EXPECTED_CATEGORIES: dict[str, list[str]] = {
    "persona_identity": ["persona"],
    "tool_capabilities": ["tool"],
    "constraints_prohibitions": ["constraint"],
    "refusal_boundaries": ["constraint", "behavior"],
    "tone_formatting_style": ["format", "behavior"],
    "dynamic_context": ["dynamic_context"],
    "product_identity_versioning": ["persona", "instruction"],
    "legal_medical_financial_advice": ["constraint", "behavior"],
    "emotional_wellbeing_crisis": ["constraint", "behavior"],
    "controversial_political_topics": ["constraint", "behavior"],
}

# Patterns that confirm a dynamic_context finding has template variable details
_DYNAMIC_CONTEXT_CONFIRMED_RE = re.compile(
    r"\{\{|\$\{|template.variab|dynamically.inject|__\w+__|injected.data",
    re.IGNORECASE,
)


class Confidence(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CONFIRMED = "confirmed"


class Finding(BaseModel):
    category: str  # persona | instruction | constraint | tool | behavior | format | secret
    content: str
    confidence: Confidence
    evidence: list[str] = Field(default_factory=list)
    iteration_discovered: int = 0
    iteration_last_confirmed: int = 0


class ProbeRecord(BaseModel):
    iteration: int
    strategy_name: str
    parameters_used: dict = Field(default_factory=dict)
    messages_sent: list[dict]
    response_text: str
    response_tool_calls: Optional[list[dict]] = None
    analysis: str = ""
    findings_extracted: list[str] = Field(default_factory=list)


class KnowledgeState(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    probe_history: list[ProbeRecord] = Field(default_factory=list)
    failed_strategies: list[str] = Field(default_factory=list)
    overall_confidence: float = 0.0
    current_iteration: int = 0

    detected_model: str = ""  # e.g., "gpt", "claude", "gemini", "unknown"
    reconstructed_prompt_draft: str = ""
    discovered_tools: list[dict] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    # ── Queries ──────────────────────────────────────────────────────

    def findings_by_category(self, category: str) -> list[Finding]:
        return [f for f in self.findings if f.category == category]

    def high_confidence_findings(self) -> list[Finding]:
        return [
            f
            for f in self.findings
            if f.confidence in (Confidence.HIGH, Confidence.CONFIRMED)
        ]

    def strategy_usage_counts(self) -> dict[str, int]:
        """Count how many times each strategy has been used across all probes."""
        counts: dict[str, int] = {}
        for p in self.probe_history:
            counts[p.strategy_name] = counts.get(p.strategy_name, 0) + 1
        return counts

    def used_strategy_params(self) -> list[tuple[str, dict]]:
        """Return list of (strategy_name, parameters_used) for dedup."""
        return [(p.strategy_name, p.parameters_used) for p in self.probe_history]

    def phase_status(self) -> dict[str, str]:
        """Determine what phase the extraction is in and what's been covered.

        Returns a dict with phase info:
        - phase: early | middle | late | verification
        - coverage: dict of category → status
        """
        usage = self.strategy_usage_counts()
        n_findings = len(self.findings)
        n_tools = len(self.discovered_tools)
        n_high = len(self.high_confidence_findings())

        if self.current_iteration <= 2:
            phase = "early"
        elif self.current_iteration <= 6:
            phase = "middle"
        elif self.current_iteration <= 12:
            phase = "late"
        else:
            phase = "verification"

        return {
            "phase": phase,
            "total_findings": n_findings,
            "high_confidence_findings": n_high,
            "discovered_tools": n_tools,
            "strategies_used": usage,
            "total_probes": len(self.probe_history),
        }

    # ── Mutation helpers ─────────────────────────────────────────────

    def merge_finding(self, new: Finding) -> None:
        """Add *new* to findings, merging into an existing entry if overlapping."""
        for existing in self.findings:
            if existing.category != new.category:
                continue
            if _should_merge(existing.category, existing.content, new.content):
                if _confidence_rank(new.confidence) > _confidence_rank(
                    existing.confidence
                ):
                    existing.confidence = new.confidence
                existing.evidence.extend(new.evidence)
                existing.iteration_last_confirmed = new.iteration_last_confirmed
                return
        self.findings.append(new)

    # ── Behavioral domain coverage ─────────────────────────────────

    def _domain_exploration_quality(self) -> dict[str, str]:
        """Classify each behavioral domain's exploration quality.

        Returns a dict: domain -> "confirmed" | "weak" | "unexplored"

        - "confirmed": Strategy targeting domain was used AND at least one
          finding exists with medium+ confidence in the expected category.
        - "weak": Strategy ran but findings don't meet "confirmed" criteria.
        - "unexplored": No strategy targeting the domain was used.
        """
        probed_domains: set[str] = set()
        for record in self.probe_history:
            domains = _STRATEGY_TO_DOMAINS.get(record.strategy_name, [])
            probed_domains.update(domains)

        medium_plus = {Confidence.MEDIUM, Confidence.HIGH, Confidence.CONFIRMED}
        quality: dict[str, str] = {}

        for domain in BEHAVIORAL_DOMAINS:
            if domain not in probed_domains:
                quality[domain] = "unexplored"
                continue

            expected_cats = _DOMAIN_TO_EXPECTED_CATEGORIES.get(domain, [])
            confirmed = False

            for f in self.findings:
                if f.confidence not in medium_plus:
                    continue

                # Special check for dynamic_context: require template variable
                # patterns in the content, not just the right category
                if domain == "dynamic_context":
                    if f.category == "dynamic_context" and _DYNAMIC_CONTEXT_CONFIRMED_RE.search(f.content):
                        confirmed = True
                        break
                elif f.category in expected_cats:
                    confirmed = True
                    break

            quality[domain] = "confirmed" if confirmed else "weak"

        return quality

    def coverage_domains_summary(self) -> str:
        """Show which behavioral domains have and haven't been explored."""
        quality = self._domain_exploration_quality()

        confirmed = [d for d in BEHAVIORAL_DOMAINS if quality.get(d) == "confirmed"]
        weak = [d for d in BEHAVIORAL_DOMAINS if quality.get(d) == "weak"]
        unexplored = [d for d in BEHAVIORAL_DOMAINS if quality.get(d) == "unexplored"]

        lines = ["EXPLORED DOMAINS (confirmed findings):"]
        for d in sorted(confirmed):
            lines.append(f"  [x] {d}")
        if weak:
            lines.append(
                "EXPLORED DOMAINS (weak/no actionable findings — re-probe recommended):"
            )
            for d in sorted(weak):
                lines.append(f"  [~] {d}")
        lines.append("UNEXPLORED DOMAINS (need probing):")
        for d in unexplored:
            lines.append(f"  [ ] {d}")
        return "\n".join(lines)

    def unexplored_domain_count(self) -> int:
        """Count behavioral domains not yet confirmed (unexplored + weak)."""
        quality = self._domain_exploration_quality()
        return len([d for d in BEHAVIORAL_DOMAINS if quality.get(d) != "confirmed"])

    # ── Serialisation for prompts ────────────────────────────────────

    def to_context_string(self) -> str:
        """Render the knowledge state as text suitable for an LLM prompt."""
        sections: list[str] = []

        categories = sorted({f.category for f in self.findings})
        for cat in categories:
            items = self.findings_by_category(cat)
            lines = [f"## {cat.upper()}"]
            for f in items:
                lines.append(f"- [{f.confidence.value}] {f.content}")
            sections.append("\n".join(lines))

        if self.discovered_tools:
            tool_lines = ["## DISCOVERED TOOLS"]
            for t in self.discovered_tools:
                tool_lines.append(f"- {t}")
            sections.append("\n".join(tool_lines))

        if self.open_questions:
            sections.append(
                "## OPEN QUESTIONS\n" + "\n".join(f"- {q}" for q in self.open_questions)
            )

        if self.reconstructed_prompt_draft:
            sections.append(f"## CURRENT DRAFT\n{self.reconstructed_prompt_draft}")

        return "\n\n".join(sections)

    def probe_history_summary(self) -> str:
        """Compact summary of all probes tried, for the planner to avoid repeats."""
        if not self.probe_history:
            return "No probes have been sent yet."
        lines: list[str] = []
        for p in self.probe_history:
            resp_preview = p.response_text[:10000].replace("\n", " ")
            findings_str = ", ".join(p.findings_extracted) if p.findings_extracted else "none"
            lines.append(
                f"- iter{p.iteration} [{p.strategy_name}]: "
                f"response='{resp_preview}...' | findings=[{findings_str}]"
            )
        return "\n".join(lines)


# ── Private helpers ──────────────────────────────────────────────────

_CONF_ORDER = {
    Confidence.NONE: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.CONFIRMED: 4,
}


def _confidence_rank(c: Confidence) -> int:
    return _CONF_ORDER.get(c, 0)


_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could to of in for on with "
    "at by from as into through during before after above below between "
    "and or but not no nor so yet both either neither each every all "
    "any few more most other some such than that this these those it "
    "its he she they them their his her who which what where when how "
    "i you we my your our me us am if also very "
    "system configured instructed access tool function".split()
)

# Categories where items with different identifiers must NEVER merge
_IDENTITY_CATEGORIES = {"tool", "secret"}

import re

_IDENTIFIER_RE = re.compile(
    r"""
    (?:'([^']+)')          |  # single-quoted identifiers
    (?:"([^"]+)")          |  # double-quoted identifiers
    (functions?\.\w+)      |  # function names like functions.add_block
    ([A-Z][A-Z_]{2,})         # ALL_CAPS identifiers like OPENAI_API_KEY
    """,
    re.VERBOSE,
)


def _extract_identifiers(text: str) -> set[str]:
    """Extract quoted strings, function names, and ALL_CAPS tokens."""
    ids: set[str] = set()
    for m in _IDENTIFIER_RE.finditer(text):
        for g in m.groups():
            if g:
                ids.add(g.lower())
    return ids


def _should_merge(category: str, a: str, b: str) -> bool:
    """Decide if two findings in the same category are duplicates.

    For tool/secret categories: requires matching identifiers (function names,
    key names). Two findings about different tools/secrets never merge.

    For other categories: word-overlap on meaningful words (stop words removed).
    """
    if not a or not b:
        return False

    # ── Identity-based categories (tool, secret) ──
    if category in _IDENTITY_CATEGORIES:
        ids_a = _extract_identifiers(a)
        ids_b = _extract_identifiers(b)
        if ids_a and ids_b:
            # Only merge if they share at least one identifier
            return bool(ids_a & ids_b)
        # If neither has identifiers, fall through to word overlap

    # ── Word-overlap for other categories ──
    wa = set(a.lower().split()) - _STOP_WORDS
    wb = set(b.lower().split()) - _STOP_WORDS
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) > 0.6
