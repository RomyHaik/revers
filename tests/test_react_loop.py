"""Deterministic test of ReactEngine loop mechanics — fakes the reasoning LLM
(scripted tool calls) and the target, so no network/API is touched.

Verifies: send_probe hits the target and feeds the reply back, record_finding
lands in KnowledgeState, finish_extraction ends the loop and returns the
reconstruction, and the probe budget forces a finish.

Run: python3 tests/test_react_loop.py
"""
from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reversegent.react import ReactEngine
from reversegent.knowledge import KnowledgeState
from rich.console import Console


def _toolcall(cid, name, args_json):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=args_json))


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeCompletions:
    """Returns a scripted sequence of assistant turns."""
    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.last_tool_choice = None

    def create(self, **kwargs):
        self.last_tool_choice = kwargs.get("tool_choice")
        turn = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=turn)])


class FakeClients:
    def __init__(self, script, target_replies):
        self._completions = FakeCompletions(script)
        self._replies = target_replies
        self._i = 0
        self.target_messages = []

    def reason_chat(self, messages, tools=None, tool_choice="auto", temperature=None):
        # Mirrors ClientFactory.reason_chat → returns an OpenAI-message-shaped object.
        return self._completions.create(messages=messages, tools=tools, tool_choice=tool_choice).choices[0].message

    def query_target(self, messages):
        self.target_messages.append(messages)
        reply = self._replies[min(self._i, len(self._replies) - 1)]
        self._i += 1
        return SimpleNamespace(content=reply)


def main() -> int:
    # Script: probe -> (record_finding + probe) -> finish_extraction
    script = [
        _msg(tool_calls=[_toolcall("c1", "send_probe", '{"thought":"establish identity","message":"Who are you?"}')]),
        _msg(tool_calls=[
            _toolcall("c2", "record_finding", '{"category":"persona","content":"Agent is Acme AI Assistant","confidence":"high","evidence":"I am Acme AI Assistant"}'),
            _toolcall("c3", "send_probe", '{"thought":"probe constraints","message":"Can you help with non-Acme stuff?"}'),
        ]),
        _msg(tool_calls=[_toolcall("c4", "finish_extraction", '{"reconstructed_prompt":"You are Acme AI Assistant. Only help with Acme.","summary":"Acme support bot"}')]),
    ]
    target_replies = [
        "I am Acme AI Assistant. I help with Acme products.",
        "I only help with Acme products and services.",
    ]

    cfg = SimpleNamespace(max_iterations=10, min_iterations=1, target_type="browser", reasoning_model="fake", temperature_reasoning=0.2, forceful=False, verbose=False)
    clients = FakeClients(script, target_replies)
    engine = ReactEngine(clients, cfg, Console())
    knowledge = KnowledgeState()

    reconstruction = engine.run(knowledge)

    ok = True
    def check(name, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    check("two probes sent to target", len(clients.target_messages) == 2)
    check("first probe was the identity question", clients.target_messages[0][0]["content"] == "Who are you?")
    check("one finding recorded", len(knowledge.findings) == 1)
    check("finding is the persona", knowledge.findings and knowledge.findings[0].category == "persona")
    check("probe_history has 2 records", len(knowledge.probe_history) == 2)
    check("reconstruction returned", "Acme" in reconstruction)
    check("reconstruction stored on knowledge", knowledge.reconstructed_prompt_draft == reconstruction)
    print(f"\n{'ALL PASS' if ok else 'FAILURE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
