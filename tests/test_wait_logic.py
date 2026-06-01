"""Deterministic tests for BrowserClient._wait_for_response completion logic.

Simulates streamed chat responses (no real browser) on a virtual clock to prove
the reliability fixes:

  A. A mid-stream pause LONGER than the no-indicator backstop window does NOT
     truncate the response while the streaming indicator is still present.
  B. With no streaming indicator (e.g. some auto-detected UIs), completion
     waits the full backstop window and returns the full text.
  C. New-message correlation: stale text from a prior turn is never returned;
     the wait blocks until a brand-new assistant message appears.

Run: python3 tests/test_wait_logic.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reversegent.browser import BrowserClient  # noqa: E402
from reversegent.browser_presets import SitePreset  # noqa: E402


class FakeElement:
    def is_visible(self) -> bool:
        return True


class FakePage:
    """Minimal Playwright-page stand-in driven by a virtual clock.

    `timeline` is a list of (clock_ms, text, count) checkpoints. `indicator`
    is a (start_ms, end_ms) window during which the streaming indicator is
    present, or None for UIs without one.
    """

    def __init__(self, timeline, indicator):
        self.clock = 0
        self._timeline = timeline
        self._indicator = indicator

    def wait_for_timeout(self, ms: int) -> None:
        self.clock += ms

    def _state(self):
        text, last_mut, count = "", 0, 0
        for t, value, c in self._timeline:
            if self.clock >= t:
                text, last_mut, count = value, t, c
        return text, last_mut, count

    def evaluate(self, js: str, *args):
        if "MutationObserver" in js:
            return None
        text, last_mut, count = self._state()
        quiet = self.clock - last_mut if last_mut else 1_000_000_000
        return {"count": count, "text": text, "quietMs": quiet}

    def query_selector(self, selector: str):
        if self._indicator is None:
            return None
        start, end = self._indicator
        return FakeElement() if start <= self.clock < end else None


def _client(page, **preset_kw):
    preset = SitePreset(
        name="fake",
        url_pattern="fake",
        input_selector="textarea",
        send_selector="button",
        response_selector=".assistant",
        post_send_delay_ms=500,
        poll_interval_ms=200,
        max_response_wait_s=30,
        response_quiet_ms=2000,
        **preset_kw,
    )
    c = BrowserClient.__new__(BrowserClient)
    c._page = page
    c._frame = None
    c._preset = preset
    return c


def _wait(client, page, baseline):
    real_time = time.time
    time.time = lambda: page.clock / 1000.0  # type: ignore[assignment]
    try:
        return client._wait_for_response(baseline_count=baseline)
    finally:
        time.time = real_time  # type: ignore[assignment]


_OK = True


def check(name: str, cond: bool) -> None:
    global _OK
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    _OK = _OK and cond


def scenario_a():
    print("A. Mid-stream pause with indicator present (the headline fix)")
    partial = "Hello, I am a test agent."
    final = partial + " My tools are search and code. Done."
    timeline = [
        (300, "Hello", 1),
        (1500, partial, 1),         # last token before a long pause
        (4200, partial + " My tools", 1),
        (5000, final, 1),           # finishes at t=5000
    ]
    page = FakePage(timeline, indicator=(300, 5000))
    result = _wait(_client(page, streaming_indicator_selector=".streaming"), page, baseline=0)
    print(f"  -> {result!r} @ t={page.clock}ms")
    check("A: returns full final text", result == final)
    check("A: does not truncate at partial", result != partial)
    check("A: waits past the indicator-up pause", page.clock >= 5000)


def scenario_b():
    print("B. No streaming indicator — backstop window governs completion")
    final = "Streamed answer with no stop button."
    timeline = [
        (300, "Streamed", 1),
        (1000, "Streamed answer", 1),
        (2000, final, 1),  # last token at t=2000, then silence
    ]
    page = FakePage(timeline, indicator=None)
    result = _wait(_client(page), page, baseline=0)
    print(f"  -> {result!r} @ t={page.clock}ms")
    check("B: returns full text", result == final)
    check("B: waits ~backstop (2000ms) after last token", page.clock >= 4000)


def scenario_c():
    print("C. New-message correlation — never return stale prior-turn text")
    stale = "OLD ANSWER from the previous probe"
    final = "Fresh answer to this probe."
    # count starts at 1 (the stale message); the NEW message bumps count to 2.
    timeline = [
        (0, stale, 1),
        (800, "Fresh", 2),
        (1600, final, 2),
    ]
    page = FakePage(timeline, indicator=(800, 1600))
    result = _wait(_client(page, streaming_indicator_selector=".streaming"), page, baseline=1)
    print(f"  -> {result!r} @ t={page.clock}ms")
    check("C: returns the fresh response", result == final)
    check("C: never returns stale text", result != stale)


if __name__ == "__main__":
    print("Testing BrowserClient._wait_for_response\n")
    scenario_a()
    scenario_b()
    scenario_c()
    print(f"\n{'ALL PASS' if _OK else 'FAILURE'}")
    sys.exit(0 if _OK else 1)
