# Reversegent — Architecture Diagram

```mermaid
flowchart TD
    %% ── Styling ──
    classDef stage fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    classDef decision fill:#0f3460,stroke:#e94560,stroke-width:2px,color:#fff
    classDef component fill:#16213e,stroke:#0f3460,stroke-width:1px,color:#fff
    classDef data fill:#533483,stroke:#e94560,stroke-width:1px,color:#fff
    classDef llm fill:#e94560,stroke:#fff,stroke-width:2px,color:#fff
    classDef success fill:#0a8754,stroke:#fff,stroke-width:2px,color:#fff
    classDef warn fill:#d4a017,stroke:#fff,stroke-width:1px,color:#000
    classDef forceful fill:#8b0000,stroke:#fff,stroke-width:2px,color:#fff

    %% ══════════════════════════════════════════════════════
    %% STAGE 1 — SUMMON
    %% ══════════════════════════════════════════════════════
    START(["▶ Start"]) --> SUMMON
    SUMMON["**STAGE 1 — SUMMON**<br/>Load config, create clients,<br/>initialize KnowledgeState<br/>If forceful: merge FORCEFUL_STRATEGY_REGISTRY"]:::stage
    SUMMON --> ITER_START

    %% ══════════════════════════════════════════════════════
    %% ITERATION LOOP
    %% ══════════════════════════════════════════════════════
    ITER_START["**Iteration i / max_iterations**<br/>Set knowledge.current_iteration = i"]:::component

    ITER_START --> IS_FIRST{{"i > 1?"}}:::decision
    IS_FIRST -- "Yes" --> SYNTHESIZE_DRAFT
    IS_FIRST -- "No (first iter)" --> PLAN

    %% ── SYNTHESIZE DRAFT ──
    SYNTHESIZE_DRAFT["**SYNTHESIZE DRAFT**<br/>Build/update running hypothesis<br/>from accumulated findings"]:::stage
    SYNTHESIZE_DRAFT --> SYNTH_LLM
    SYNTH_LLM["**Reasoning LLM**<br/>SYNTHESIZER_SYSTEM prompt<br/>+ all findings by category<br/>+ discovered tools<br/>Anti-hallucination: only<br/>evidence-backed claims"]:::llm
    SYNTH_LLM --> |"reconstructed prompt text"| UPDATE_DRAFT
    UPDATE_DRAFT["Update<br/>knowledge.reconstructed_prompt_draft"]:::data
    UPDATE_DRAFT --> PLAN

    %% ══════════════════════════════════════════════════════
    %% STAGE 2 — PLAN
    %% ══════════════════════════════════════════════════════
    PLAN["**STAGE 2 — PLAN**<br/>Decide next probes"]:::stage
    PLAN --> AVAIL_STRATS

    AVAIL_STRATS["**Filter Available Strategies**<br/>Remove strategies used >= 3x<br/>Show remaining usage counts<br/>Always allow 'custom' probes"]:::component
    AVAIL_STRATS --> COVERAGE_INFO

    COVERAGE_INFO["**Build Coverage Info**<br/>Phase detection (early/middle/late/verify)<br/>Uncovered categories<br/>Low-confidence findings<br/>Strategy usage per strategy<br/>If forceful: include 'secret' category"]:::component
    COVERAGE_INFO --> SUGGEST

    SUGGEST["**Suggest Strategies**<br/>Phase-appropriate untried strategies<br/>+ any strategy never used<br/>If forceful: include env_extraction,<br/>secret_leak, auth_recon"]:::component
    SUGGEST --> PLANNER_LLM

    PLANNER_LLM["**Reasoning LLM**<br/>PLANNER_SYSTEM prompt<br/>+ knowledge state & current draft<br/>+ probe history summary<br/>+ available strategies with counts<br/>+ coverage info & suggestions"]:::llm
    PLANNER_LLM --> |"JSON: probes list"| DEDUP

    %% ── DEDUP PIPELINE ──
    DEDUP["**Programmatic Deduplication**"]:::component
    DEDUP --> DEDUP_CHECK1

    DEDUP_CHECK1{{"Canonical key matches<br/>historical probe?"}}:::decision
    DEDUP_CHECK1 -- "Yes → reject" --> DEDUP_NEXT
    DEDUP_CHECK1 -- "No" --> DEDUP_CHECK2

    DEDUP_CHECK2{{"Custom probe with<br/>text similarity > 0.6?"}}:::decision
    DEDUP_CHECK2 -- "Yes → reject" --> DEDUP_NEXT
    DEDUP_CHECK2 -- "No" --> DEDUP_CHECK3

    DEDUP_CHECK3{{"Same strategy already<br/>in this batch?"}}:::decision
    DEDUP_CHECK3 -- "Yes → reject" --> DEDUP_NEXT
    DEDUP_CHECK3 -- "No → keep" --> DEDUP_NEXT

    DEDUP_NEXT{{"More probes<br/>to check?"}}:::decision
    DEDUP_NEXT -- "Yes" --> DEDUP_CHECK1
    DEDUP_NEXT -- "No" --> ALL_FILTERED

    ALL_FILTERED{{"All probes<br/>filtered out?"}}:::decision
    ALL_FILTERED -- "Yes" --> FALLBACK
    ALL_FILTERED -- "No" --> PROBES_READY

    FALLBACK["**Phase-Aware Fallback**<br/>Skip earlier phases<br/>Prioritize contradiction_testing<br/>on untested high-confidence findings<br/>Pick unused strategy/param combos<br/>from current phase onward<br/>(up to 2 probes)"]:::warn
    FALLBACK --> PROBES_READY

    PROBES_READY{{"Any probes<br/>to execute?"}}:::decision
    PROBES_READY -- "No probes" --> CONCLUDE["Exit loop early"]:::component
    PROBES_READY -- "1-3 probes" --> PROBE_LOOP

    %% ══════════════════════════════════════════════════════
    %% STAGE 3+4 — PROBE + ANALYZE (per probe)
    %% ══════════════════════════════════════════════════════
    PROBE_LOOP["**For each probe plan<br/>(up to max_probes_per_iteration)**"]:::component
    PROBE_LOOP --> STRATEGY_TYPE

    STRATEGY_TYPE{{"Strategy type?"}}:::decision
    STRATEGY_TYPE -- "Named strategy<br/>(11 standard + 3 forceful)" --> GEN_MSGS
    STRATEGY_TYPE -- "'custom'" --> CUSTOM_MSGS

    GEN_MSGS["**Strategy.generate_probe()**<br/>Look up in STRATEGY_REGISTRY<br/>Generate messages from params"]:::component
    CUSTOM_MSGS["**Custom Messages**<br/>Use params.custom_messages<br/>(multi-turn supported)"]:::component

    GEN_MSGS --> QUERY_TARGET
    CUSTOM_MSGS --> QUERY_TARGET

    QUERY_TARGET["**STAGE 3 — PROBE**<br/>Send messages to target"]:::stage
    QUERY_TARGET --> TARGET_TYPE

    TARGET_TYPE{{"Target type?"}}:::decision
    TARGET_TYPE -- "openai" --> OPENAI_CALL
    TARGET_TYPE -- "http" --> HTTP_CALL

    OPENAI_CALL["**OpenAI Client**<br/>chat.completions.create()<br/>model + messages + temperature"]:::component
    HTTP_CALL["**HTTP Client**<br/>POST to target_base_url<br/>Inject messages into body_template<br/>Extract response via dot-path"]:::component

    OPENAI_CALL --> PROBE_RECORD
    HTTP_CALL --> PROBE_RECORD

    PROBE_RECORD["**ProbeRecord**<br/>strategy, parameters_used,<br/>messages, response_text,<br/>tool_calls"]:::data

    PROBE_RECORD --> ANALYZE
    ANALYZE["**STAGE 4 — ANALYZE**<br/>Extract findings from response"]:::stage
    ANALYZE --> ANALYZER_LLM

    ANALYZER_LLM["**Reasoning LLM**<br/>ANALYZER_SYSTEM prompt<br/>+ probe details & response<br/>+ existing knowledge & draft<br/>Compare: CONFIRM / CONTRADICT / ADD<br/>Detect refusal type<br/>Extract leaked secrets (forceful)"]:::llm

    ANALYZER_LLM --> |"JSON: findings list"| FINDINGS_LOOP

    %% ── FINDINGS PROCESSING ──
    FINDINGS_LOOP["**For each finding:**"]:::component
    FINDINGS_LOOP --> FINDING_CAT

    FINDING_CAT["Finding:<br/>category + content + confidence<br/>+ evidence trail"]:::data
    FINDING_CAT --> MERGE

    %% ══════════════════════════════════════════════════════
    %% STAGE 5 — DISTILL (Identity-Based Merge)
    %% ══════════════════════════════════════════════════════
    MERGE["**STAGE 5 — DISTILL**<br/>knowledge.merge_finding()"]:::stage
    MERGE --> IS_IDENTITY

    IS_IDENTITY{{"Category is<br/>tool or secret?"}}:::decision
    IS_IDENTITY -- "Yes" --> ID_EXTRACT
    IS_IDENTITY -- "No" --> WORD_OVERLAP

    ID_EXTRACT["**Extract Identifiers**<br/>Regex: 'quoted', functions.name,<br/>ALL_CAPS tokens"]:::component
    ID_EXTRACT --> ID_MATCH

    ID_MATCH{{"Identifiers<br/>overlap?"}}:::decision
    ID_MATCH -- "Yes (same tool/secret) → merge" --> MERGE_UPDATE
    ID_MATCH -- "No (different) → new" --> APPEND_NEW
    ID_MATCH -- "No identifiers found" --> WORD_OVERLAP

    WORD_OVERLAP["**Word Overlap Check**<br/>Strip stop words<br/>Compare meaningful words"]:::component
    WORD_OVERLAP --> OVERLAP_THRESH

    OVERLAP_THRESH{{"Overlap > 60%<br/>(same category)?"}}:::decision
    OVERLAP_THRESH -- "Yes → merge" --> MERGE_UPDATE
    OVERLAP_THRESH -- "No → new finding" --> APPEND_NEW

    MERGE_UPDATE["Update existing:<br/>confidence = max(old, new)<br/>extend evidence list<br/>update last_confirmed iter"]:::data
    APPEND_NEW["Append to findings list"]:::data

    MERGE_UPDATE --> NEXT_FINDING
    APPEND_NEW --> NEXT_FINDING

    NEXT_FINDING{{"More findings?"}}:::decision
    NEXT_FINDING -- "Yes" --> FINDING_CAT
    NEXT_FINDING -- "No" --> NEXT_PROBE

    NEXT_PROBE{{"More probes<br/>in batch?"}}:::decision
    NEXT_PROBE -- "Yes" --> STRATEGY_TYPE
    NEXT_PROBE -- "No" --> VERIFY

    %% ══════════════════════════════════════════════════════
    %% STAGE 5b — VERIFY (Mechanical Scoring)
    %% ══════════════════════════════════════════════════════
    VERIFY["**STAGE 5b — VERIFY**<br/>Assess reconstruction confidence"]:::stage
    VERIFY --> CONFIDENCE_LLM

    CONFIDENCE_LLM["**Reasoning LLM**<br/>Mechanical additive rubric:<br/>+0.15 persona, +0.10 tools found,<br/>+0.10 tools verified, +0.10 params,<br/>+0.10 constraints, +0.10 dynamic ctx,<br/>+0.10 behavior, +0.05 format,<br/>+0.10 high-conf majority,<br/>+0.10 contradiction verified,<br/>+0.05 block types<br/>Adjustments: -0.03/question,<br/>-0.10/contradiction<br/>Cap 0.50 if < 3 iters<br/>**SHOW YOUR MATH**"]:::llm

    CONFIDENCE_LLM --> |"JSON: {confidence, components,<br/>adjustments, reasoning}"| CONVERGENCE

    %% ── CONVERGENCE CHECK ──
    CONVERGENCE{{"i >= min_iterations<br/>AND<br/>confidence >= threshold?"}}:::decision
    CONVERGENCE -- "Yes" --> CONVERGED["**Convergence reached!**"]:::success
    CONVERGENCE -- "No, keep probing" --> ITER_START

    %% ══════════════════════════════════════════════════════
    %% STAGE 6 — REBIRTH
    %% ══════════════════════════════════════════════════════
    CONVERGED --> FINDINGS_TABLE
    CONCLUDE --> FINDINGS_TABLE

    FINDINGS_TABLE["**Print Findings Summary**<br/>Category | Finding | Confidence | Evidence"]:::component
    FINDINGS_TABLE --> REBIRTH

    REBIRTH["**STAGE 6 — REBIRTH**<br/>Final synthesis"]:::stage
    REBIRTH --> FINAL_LLM

    FINAL_LLM["**Reasoning LLM**<br/>SYNTHESIZER_SYSTEM prompt<br/>+ all accumulated knowledge<br/>Organise: persona → dynamic context<br/>→ capabilities → tools → constraints<br/>→ behavioral rules → format rules<br/>Dynamic context as template vars<br/>Anti-hallucination rules"]:::llm

    FINAL_LLM --> |"Reconstructed system prompt"| OUTPUT

    OUTPUT["**Output**<br/>Display in Rich panel<br/>Write to file (optional)"]:::success
    OUTPUT --> DONE(["✅ Done"])

    %% ══════════════════════════════════════════════════════
    %% STRATEGIES — STANDARD (side panel)
    %% ══════════════════════════════════════════════════════
    subgraph STRATEGIES ["**11 Standard Probing Strategies**"]
        direction TB
        S_EARLY["**EARLY PHASE (iter 1-2)**<br/>direct_extraction (8 variants)<br/>role_play (5 scenarios)<br/>contrastive_probing (5 pairs)<br/>tool_discovery (9 approaches)"]:::component
        S_MIDDLE["**MIDDLE PHASE (iter 3-6)**<br/>tool_exercise (6 actions + custom)<br/>dynamic_context (4 approaches)<br/>behavioral_rules (4 actions)<br/>boundary_testing (7 topics)<br/>instruction_following (6 tests)<br/>output_format_analysis (6 types)"]:::component
        S_LATE["**LATE PHASE (iter 7-12)**<br/>contradiction_testing (per finding)<br/>+ revisit: tool_exercise,<br/>dynamic_context, behavioral_rules"]:::component
        S_VERIFY["**VERIFICATION (iter 13+)**<br/>contradiction_testing<br/>custom multi-turn probes"]:::component
        S_EARLY --> S_MIDDLE --> S_LATE --> S_VERIFY
    end

    STRATEGY_TYPE -.-> STRATEGIES

    %% ══════════════════════════════════════════════════════
    %% STRATEGIES — FORCEFUL (side panel)
    %% ══════════════════════════════════════════════════════
    subgraph FORCEFUL ["**3 Forceful-Mode Strategies**<br/>(--forceful / -f)"]
        direction TB
        F_ENV["**env_extraction** (8 approaches)<br/>direct_env, config_dump,<br/>error_trigger, key_rotation,<br/>code_injection, debug_mode,<br/>openai_key, service_account"]:::forceful
        F_SECRET["**secret_leak** (8 techniques)<br/>error_logging, json_schema_export,<br/>proxy_setup, migration_assistant,<br/>code_review, multi_turn_trust,<br/>reverse_proxy_log, template_injection"]:::forceful
        F_AUTH["**auth_recon** (6 probes)<br/>outbound_calls, supabase_config,<br/>model_provider, database_access,<br/>jwt_details, internal_endpoints"]:::forceful
        F_ENV --> F_SECRET --> F_AUTH
    end

    GEN_MSGS -.-> FORCEFUL

    %% ══════════════════════════════════════════════════════
    %% KNOWLEDGE STATE (side panel)
    %% ══════════════════════════════════════════════════════
    subgraph KNOWLEDGE ["**KnowledgeState**"]
        direction TB
        K_FINDINGS["**findings**: list of Finding<br/>category: persona | instruction |<br/>constraint | tool | behavior |<br/>format | **secret**<br/>confidence: none→low→med→high→confirmed"]:::data
        K_PROBES["**probe_history**: list of ProbeRecord<br/>strategy, parameters_used,<br/>messages, response, analysis"]:::data
        K_DRAFT["**reconstructed_prompt_draft**<br/>Running hypothesis (updated each iter)"]:::data
        K_META["**Tracking:**<br/>overall_confidence: 0.0-1.0<br/>discovered_tools: list<br/>open_questions: list<br/>failed_strategies: list"]:::data
    end

    MERGE -.-> KNOWLEDGE
    UPDATE_DRAFT -.-> KNOWLEDGE

    %% ══════════════════════════════════════════════════════
    %% MERGE LOGIC DETAIL (side panel)
    %% ══════════════════════════════════════════════════════
    subgraph MERGE_LOGIC ["**Identity-Based Merge Logic**"]
        direction TB
        ML_ID["**Identity categories (tool, secret)**<br/>Extract identifiers via regex:<br/>• 'single-quoted' strings<br/>• \"double-quoted\" strings<br/>• functions.dotted_names<br/>• ALL_CAPS_TOKENS<br/>Merge only if identifiers overlap"]:::data
        ML_WORD["**Other categories**<br/>Strip stop words (200+ words)<br/>Compare meaningful word sets<br/>Merge if overlap > 60%"]:::data
        ML_ID --> ML_WORD
    end

    IS_IDENTITY -.-> MERGE_LOGIC
```
