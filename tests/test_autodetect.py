"""End-to-end agnostic-detection test against a HARD case (no presets, no
manual selectors): a collapsed chat widget whose UI lives in an iframe with
arbitrary class names. Proves the shippable pipeline:

  open collapsed widget  →  find input inside the iframe  →  self-calibrate the
  response selector via a nonce  →  send a probe  →  read the reply.

Run: python3 tests/test_autodetect.py   (needs a server on tests/, auto-started)
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

URL = "http://localhost:8799/host.html"


def main() -> int:
    import reversegent.browser as bm

    # Force the fresh-launch path to run headless.
    def _headless_fresh(self):
        self._browser = self._playwright.chromium.launch(headless=True, channel="chrome")
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
    bm.BrowserClient._launch_fresh = _headless_fresh  # type: ignore[assignment]

    from reversegent.browser import BrowserClient
    from reversegent.config import ReversegentConfig

    cfg = ReversegentConfig(
        target_type="browser",
        target_base_url=URL,            # fresh launch navigates here
        reasoning_api_key="not-used",   # no preset, no manual selectors → pure autodetect
        browser_max_response_wait=30,
        verbose=True,
    )
    client = BrowserClient(cfg, reasoning_client=None)

    ok = True
    def check(name, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    print(f"Launching headless → {URL} (no presets, no selectors)")
    client.launch()
    try:
        preset = client._preset
        print(f"\nDetected: input={preset.input_selector!r} send={preset.send_selector!r} response={preset.response_selector!r}")
        print(f"Frame is iframe: {client._frame is not None and client._frame != client._page.main_frame}")

        check("found an input selector", bool(preset.input_selector))
        check("input resolved inside the iframe", client._frame is not None and client._frame != client._page.main_frame)
        check("calibrated a bot-specific response selector", "msg-bot" in preset.response_selector)

        resp = client.query([{"role": "user", "content": "Who are you?"}])
        text = resp.content or ""
        print(f"\nReply captured: {text!r}")
        check("probe got DemoBot reply", "DemoBot" in text)
        check("reply echoes the question", "Who are you" in text)
    finally:
        client.close()

    print(f"\n{'ALL PASS' if ok else 'FAILURE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
