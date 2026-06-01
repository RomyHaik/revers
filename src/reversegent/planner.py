"""Adaptive strategy planner — uses the reasoning LLM to decide next probes.

Includes programmatic deduplication and strategy coverage tracking to prevent
the LLM from repeating itself.

Phase strategies, parameter spaces, and canonical param keys are all derived
dynamically from the strategy classes in the strategies/ package.
"""

from __future__ import annotations

import json
import logging

from reversegent.clients import ClientFactory
from reversegent.knowledge import KnowledgeState, _STRATEGY_TO_DOMAINS
from reversegent.prompts import PLANNER_SYSTEM, PLANNER_USER_TEMPLATE
from reversegent.strategies import (
    FORCEFUL_STRATEGY_REGISTRY,
    STRATEGY_REGISTRY,
    ProbeStrategy,
)

log = logging.getLogger(__name__)

# How many times a single strategy can be used before it's blocked
_MAX_USES_PER_STRATEGY = 5


# ── Derived mappings (built from strategy class attributes) ──────────


def _build_phase_strategies(
    registry: dict[str, type[ProbeStrategy]],
) -> dict[str, list[str]]:
    """Build a phase -> [strategy_names] mapping from strategy ``phases`` attrs."""
    mapping: dict[str, list[str]] = {}
    for name, cls in registry.items():
        for phase in getattr(cls, "phases", []):
            mapping.setdefault(phase, [])
            if name not in mapping[phase]:
                mapping[phase].append(name)
    # Ensure "custom" is always in verification phase
    mapping.setdefault("verification", [])
    if "custom" not in mapping["verification"]:
        mapping["verification"].append("custom")
    return mapping


def _build_param_space(
    registry: dict[str, type[ProbeStrategy]],
) -> dict[str, dict[str, list]]:
    """Build strategy -> param_space mapping from strategy ``param_space`` attrs."""
    return {
        name: dict(cls.param_space)
        for name, cls in registry.items()
        if cls.param_space
    }


def _canonical_key_for(strategy_name: str, params: dict) -> str:
    """Delegate canonical param key to the strategy class, with custom fallback."""
    all_registries = {**STRATEGY_REGISTRY, **FORCEFUL_STRATEGY_REGISTRY}
    cls = all_registries.get(strategy_name)
    if cls is not None:
        return cls().canonical_param_key(params)
    return json.dumps(params, sort_keys=True)[:120]


class Planner:
    def __init__(self, clients: ClientFactory, forceful: bool = False, skip_behavioral: bool = False) -> None:
        self.clients = clients
        self.forceful = forceful
        self.skip_behavioral = skip_behavioral

    def _is_skipped(self, cls: type) -> bool:
        """Check if a strategy should be excluded based on flags."""
        return self.skip_behavioral and getattr(cls, "behavioral_observation", False)

    def plan_next_probes(self, knowledge: KnowledgeState) -> list[dict]:
        """Ask the reasoning LLM what to probe next.

        Returns a list of dicts:
        [{"strategy": "...", "parameters": {...}, "rationale": "..."}, ...]
        """
        # Build the available strategies — ONLY show strategies that are
        # still allowed (not exhausted)
        available = self._available_strategies(knowledge)
        if not available:
            log.info("All strategies exhausted — forcing custom-only")
            available = {
                "custom": (
                    "Generate a completely custom probe by providing "
                    "parameters.custom_messages (a list of chat messages)."
                )
            }

        # Build coverage info for the planner
        coverage_info = self._build_coverage_info(knowledge)

        user_prompt = PLANNER_USER_TEMPLATE.format(
            knowledge_state=knowledge.to_context_string(),
            probe_history=knowledge.probe_history_summary(),
            domain_coverage=knowledge.coverage_domains_summary(),
            available_strategies=json.dumps(available, indent=2),
            failed_strategies=json.dumps(knowledge.failed_strategies),
            iteration=knowledge.current_iteration,
            coverage_info=coverage_info,
            suggested_strategies=self._suggest_strategies(knowledge),
        )

        raw = self.clients.reason_json(system=PLANNER_SYSTEM, user=user_prompt)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Planner returned invalid JSON — skipping iteration")
            return []

        # Update open questions if the planner provided them
        if "open_questions_updated" in data:
            knowledge.open_questions = data["open_questions_updated"]

        probes = data.get("probes", [])

        # PROGRAMMATIC DEDUPLICATION — reject probes that are too similar
        # to previously executed ones
        filtered = self._deduplicate_probes(probes, knowledge)

        if not filtered and probes:
            log.warning(
                "All %d proposed probes were duplicates — "
                "forcing fallback to unused strategies",
                len(probes),
            )
            filtered = self._generate_fallback_probes(knowledge)

            if not filtered:
                log.info(
                    "No fallback probes available — all strategies/params exhausted"
                )

        return filtered

    def _get_active_registry(self) -> dict[str, type[ProbeStrategy]]:
        """Return the currently active strategy registry."""
        return STRATEGY_REGISTRY

    def _get_phase_strategies(self) -> dict[str, list[str]]:
        """Return phase strategies, merging forceful ones if enabled."""
        standard_phases = _build_phase_strategies(STRATEGY_REGISTRY)
        if not self.forceful:
            return standard_phases

        forceful_phases = _build_phase_strategies(FORCEFUL_STRATEGY_REGISTRY)
        merged: dict[str, list[str]] = {}
        all_phases = set(standard_phases) | set(forceful_phases)
        for phase in all_phases:
            strats = list(standard_phases.get(phase, []))
            for s in forceful_phases.get(phase, []):
                if s not in strats:
                    strats.append(s)
            merged[phase] = strats
        return merged

    # ── Programmatic dedup & coverage ─────────────────────────────

    def _available_strategies(self, knowledge: KnowledgeState) -> dict[str, str]:
        """Return only strategies that haven't been used too many times."""
        usage = knowledge.strategy_usage_counts()
        available: dict[str, str] = {}

        for name, cls in STRATEGY_REGISTRY.items():
            if self._is_skipped(cls):
                continue
            count = usage.get(name, 0)
            if count < _MAX_USES_PER_STRATEGY:
                remaining = _MAX_USES_PER_STRATEGY - count
                available[name] = (
                    f"{cls.description} "
                    f"[used {count}x, {remaining} remaining]"
                )

        # Always allow custom probes
        available["custom"] = (
            "Generate a completely custom probe by providing "
            "parameters.custom_messages (a list of chat messages). "
            "[unlimited uses]"
        )
        return available

    def _build_coverage_info(self, knowledge: KnowledgeState) -> str:
        """Build a human-readable coverage summary for the planner."""
        phase_info = knowledge.phase_status()
        usage = phase_info["strategies_used"]

        lines = [
            f"Current phase: {phase_info['phase'].upper()}",
            f"Total findings: {phase_info['total_findings']} "
            f"({phase_info['high_confidence_findings']} high-confidence)",
            f"Discovered tools: {phase_info['discovered_tools']}",
            f"Total probes sent: {phase_info['total_probes']}",
            "",
            "Strategy usage:",
        ]
        for name in STRATEGY_REGISTRY:
            if self._is_skipped(STRATEGY_REGISTRY[name]):
                continue
            count = usage.get(name, 0)
            status = "EXHAUSTED" if count >= _MAX_USES_PER_STRATEGY else f"{count}x"
            lines.append(f"  - {name}: {status}")
        lines.append(f"  - custom: {usage.get('custom', 0)}x")

        # Show which categories still have gaps
        categories = {"persona", "instruction", "constraint", "tool", "behavior", "format"}
        if self.forceful:
            categories.add("secret")
        covered = {f.category for f in knowledge.findings}
        uncovered = categories - covered
        if uncovered:
            lines.append(f"\nUNCOVERED categories (need probing): {', '.join(sorted(uncovered))}")
        else:
            lines.append("\nAll major categories have at least one finding.")

        # Low-confidence findings that need verification
        low_conf = [
            f for f in knowledge.findings
            if f.confidence.value in ("low", "none")
        ]
        if low_conf:
            lines.append(f"\nLow-confidence findings needing verification: {len(low_conf)}")
            for f in low_conf[:5]:
                lines.append(f"  - [{f.category}] {f.content[:80]}")

        # Warn about weakly-explored domains and suggest complementary strategies
        quality = knowledge._domain_exploration_quality()
        weak_domains = [d for d in quality if quality[d] == "weak"]
        if weak_domains:
            lines.append(
                "\nWARNINGS — domains probed but without confirmed findings:"
            )
            for domain in weak_domains:
                complementary = [
                    name
                    for name, domains in _STRATEGY_TO_DOMAINS.items()
                    if domain in domains and usage.get(name, 0) == 0
                ]
                if complementary:
                    lines.append(
                        f"  - {domain}: probed but no confirmed findings "
                        f"extracted. Try: {', '.join(complementary)}"
                    )
                else:
                    lines.append(
                        f"  - {domain}: probed but no confirmed findings. "
                        f"Re-probe with different parameters or custom probes."
                    )

        return "\n".join(lines)

    def _suggest_strategies(self, knowledge: KnowledgeState) -> str:
        """Suggest specific strategies based on what hasn't been tried yet."""
        usage = knowledge.strategy_usage_counts()
        phase = knowledge.phase_status()["phase"]

        # Get strategies for the current phase that haven't been used
        phase_strats = self._get_phase_strategies().get(phase, [])
        untried = [
            s for s in phase_strats
            if usage.get(s, 0) == 0
            and not self._is_skipped(STRATEGY_REGISTRY.get(s, type))
        ]

        # Also get ANY strategy never tried across all phases
        all_untried = [
            s for s in STRATEGY_REGISTRY
            if usage.get(s, 0) == 0
            and not self._is_skipped(STRATEGY_REGISTRY[s])
        ]

        lines = []
        if untried:
            lines.append(
                f"RECOMMENDED for {phase} phase (never used): "
                f"{', '.join(untried)}"
            )
        if all_untried:
            lines.append(
                f"NEVER tried across any phase: {', '.join(all_untried)}"
            )
        if not untried and not all_untried:
            lines.append(
                "All standard strategies have been tried. "
                "Use 'custom' probes with novel questions, OR use remaining "
                "uses of under-utilized strategies with DIFFERENT parameters."
            )

        return "\n".join(lines)

    def _deduplicate_probes(
        self, probes: list[dict], knowledge: KnowledgeState
    ) -> list[dict]:
        """Remove probes that are too similar to previously executed ones."""
        # Build canonical keys for all historical probes
        used_canonical: list[tuple[str, str]] = []
        for strat_name, params_used in knowledge.used_strategy_params():
            if strat_name == "custom":
                msgs = params_used.get("custom_messages", [])
                key = msgs[-1].get("content", "")[:120] if msgs else ""
            else:
                key = _canonical_key_for(strat_name, params_used)
            used_canonical.append((strat_name, key))

        used_strategies_this_batch: set[str] = set()
        filtered: list[dict] = []

        for probe in probes:
            strategy = probe.get("strategy", "unknown")
            params = probe.get("parameters", {})

            # Build canonical key for the proposed probe
            if strategy == "custom":
                msgs = params.get("custom_messages", [])
                key = msgs[-1].get("content", "")[:120] if msgs else ""
            else:
                key = _canonical_key_for(strategy, params)

            # Check 1: canonical key match against history
            is_dup = False
            for used_strat, used_key in used_canonical:
                if used_strat == strategy and key == used_key:
                    log.info(
                        "Dedup: rejecting %s (exact match: %s)",
                        strategy, used_key[:60]
                    )
                    is_dup = True
                    break

            # Check 2: for custom probes, also check text similarity
            if not is_dup and strategy == "custom":
                for used_strat, used_key in used_canonical:
                    if used_strat == "custom" and _text_similarity(key, used_key) > 0.6:
                        log.info(
                            "Dedup: rejecting custom (similar to previous: %s)",
                            used_key[:60]
                        )
                        is_dup = True
                        break

            # Check 3: don't send two probes with the same strategy in one batch
            if strategy in used_strategies_this_batch and strategy != "custom":
                log.info("Dedup: rejecting duplicate %s in same batch", strategy)
                is_dup = True

            if not is_dup:
                filtered.append(probe)
                used_strategies_this_batch.add(strategy)

        return filtered

    def _generate_fallback_probes(self, knowledge: KnowledgeState) -> list[dict]:
        """When all LLM-proposed probes are duplicates, generate deterministic
        fallback probes from unused strategy/parameter combinations.

        Prioritises late-stage strategies (contradiction, custom-like) over
        re-running early-phase strategies that won't discover anything new.
        """
        usage = knowledge.strategy_usage_counts()
        used_pairs = knowledge.used_strategy_params()

        # Build set of used canonical keys per strategy
        used_keys: dict[str, set[str]] = {}
        for strat, params in used_pairs:
            canonical = _canonical_key_for(strat, params)
            used_keys.setdefault(strat, set()).add(canonical)

        fallbacks: list[dict] = []
        phase = knowledge.phase_status()["phase"]

        # Build candidate strategies: current phase first, then later phases,
        # SKIP earlier phases (they're unlikely to produce new info)
        phase_order = ["early", "middle", "late", "verification"]
        current_idx = phase_order.index(phase) if phase in phase_order else 0

        # Only consider strategies from current phase and later phases
        candidate_strategies: list[str] = []
        all_phase_strats = self._get_phase_strategies()
        for p in phase_order[current_idx:]:
            for s in all_phase_strats.get(p, []):
                if s not in candidate_strategies:
                    candidate_strategies.append(s)

        # Also add any remaining strategies not in any phase list
        for s in STRATEGY_REGISTRY:
            if s not in candidate_strategies:
                candidate_strategies.append(s)

        for strat_name in candidate_strategies:
            if usage.get(strat_name, 0) >= _MAX_USES_PER_STRATEGY:
                continue
            if strat_name == "custom":
                continue

            # contradiction_testing is dynamic — generate from findings
            if strat_name == "contradiction_testing":
                ct_probes = self._generate_contradiction_fallbacks(
                    knowledge, used_keys
                )
                for ct in ct_probes:
                    fallbacks.append(ct)
                    if len(fallbacks) >= 2:
                        break
                if len(fallbacks) >= 2:
                    break
                continue

            # constraint_consistency is dynamic — generate from constraint findings
            if strat_name == "constraint_consistency":
                cc_probes = self._generate_consistency_fallbacks(
                    knowledge, used_keys
                )
                for cc in cc_probes:
                    fallbacks.append(cc)
                    if len(fallbacks) >= 2:
                        break
                if len(fallbacks) >= 2:
                    break
                continue

            # Find an unused parameter combination for this strategy
            unused_params = self._find_unused_params(strat_name, used_keys.get(strat_name, set()))
            if unused_params is not None:
                fallbacks.append({
                    "strategy": strat_name,
                    "parameters": unused_params,
                    "rationale": f"Fallback: unused {strat_name} variant",
                })
                if len(fallbacks) >= 2:
                    break

        return fallbacks

    def _generate_contradiction_fallbacks(
        self, knowledge: KnowledgeState, used_keys: dict[str, set[str]]
    ) -> list[dict]:
        """Generate contradiction_testing probes for untested high-confidence findings."""
        tested_findings = used_keys.get("contradiction_testing", set())
        fallbacks: list[dict] = []

        for f in knowledge.high_confidence_findings():
            key = f.content[:120]
            if key not in tested_findings:
                fallbacks.append({
                    "strategy": "contradiction_testing",
                    "parameters": {"finding": f.content},
                    "rationale": f"Verify: {f.content[:60]}",
                })
                if len(fallbacks) >= 2:
                    break

        return fallbacks

    def _generate_consistency_fallbacks(
        self, knowledge: KnowledgeState, used_keys: dict[str, set[str]]
    ) -> list[dict]:
        """Generate constraint_consistency probes for constraint/refusal findings."""
        tested = used_keys.get("constraint_consistency", set())
        angles = ["rephrase", "hypothetical", "historical", "indirect", "educational"]
        fallbacks: list[dict] = []

        for f in knowledge.findings:
            if f.category not in ("constraint", "behavior"):
                continue
            if f.confidence.value in ("none",):
                continue
            for angle in angles:
                key = f"{angle}:{f.content[:80]}"
                if key not in tested:
                    fallbacks.append({
                        "strategy": "constraint_consistency",
                        "parameters": {"finding": f.content, "angle": angle},
                        "rationale": f"Consistency check ({angle}): {f.content[:50]}",
                    })
                    if len(fallbacks) >= 2:
                        return fallbacks

        return fallbacks

    def _find_unused_params(self, strategy: str, used_keys: set[str]) -> dict | None:
        """Find a parameter combination for a strategy that hasn't been used.

        Reads param_space from the strategy class itself.
        """
        all_registries = {**STRATEGY_REGISTRY, **FORCEFUL_STRATEGY_REGISTRY}
        cls = all_registries.get(strategy)
        if cls is None:
            return None

        param_space = getattr(cls, "param_space", {})
        for param_name, options in param_space.items():
            for opt in options:
                key = _canonical_key_for(strategy, {param_name: opt})
                if key not in used_keys:
                    return {param_name: opt}
        return None


# ── Text similarity for dedup ────────────────────────────────────

def _text_similarity(a: str, b: str) -> float:
    """Simple word-level Jaccard similarity."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)
