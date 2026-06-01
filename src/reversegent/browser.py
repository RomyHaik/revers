"""BrowserClient — Playwright-based browser automation for chat web UIs."""

from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
import time
from dataclasses import replace
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from openai.types.chat import ChatCompletionMessage

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        sync_playwright,
    )
except ImportError:
    raise ImportError(
        "The 'browser' target requires playwright. "
        "Install it with: pip install 'reversegent[browser]' && playwright install chromium"
    )

from reversegent import autodetect
from reversegent.browser_presets import PRESETS, SitePreset
from reversegent.prompts import SELECTOR_DETECTOR_SYSTEM

if TYPE_CHECKING:
    from reversegent.clients import ClientFactory
    from reversegent.config import ReversegentConfig

log = logging.getLogger(__name__)


def _is_mac() -> bool:
    return platform.system() == "Darwin"


# JavaScript injected into the page to track response streaming precisely.
# A MutationObserver records the timestamp of the last DOM change *inside the
# assistant-message subtree*. This lets us detect "streaming has gone quiet"
# far more accurately than polling text equality on a fixed interval — it
# catches sub-poll changes and measures a true quiet gap, so a model that
# pauses mid-stream (before a code block, while "thinking") is not mistaken
# for a finished response.
_OBSERVER_JS = """
(selector) => {
    const state = window.__rg_state || (window.__rg_state = {});
    if (state.observer) { try { state.observer.disconnect(); } catch (e) {} }
    state.selector = selector;
    state.lastMutation = Date.now();
    const matches = (el) => el && el.matches && el.matches(selector);
    const touchesResponse = (node) => {
        let el = node;
        if (el && el.nodeType === 3 /* TEXT_NODE */) el = el.parentElement;
        while (el) {
            if (matches(el)) return true;
            el = el.parentElement;
        }
        return false;
    };
    const obs = new MutationObserver((records) => {
        for (const r of records) {
            if (touchesResponse(r.target)) { state.lastMutation = Date.now(); return; }
            for (const n of r.addedNodes) {
                if (touchesResponse(n)) { state.lastMutation = Date.now(); return; }
            }
        }
    });
    obs.observe(document.body, {subtree: true, childList: true, characterData: true});
    state.observer = obs;
}
"""

# Reads the current streaming state computed by the observer above.
_STATE_JS = """
(selector) => {
    const els = Array.from(document.querySelectorAll(selector));
    const last = els.length ? els[els.length - 1] : null;
    const state = window.__rg_state || {};
    return {
        count: els.length,
        text: last ? (last.innerText || '').trim() : '',
        quietMs: state.lastMutation ? (Date.now() - state.lastMutation) : 1e9,
    };
}
"""


class BrowserClient:
    """Playwright-based browser automation for chat web UIs.

    Manages a single browser instance that persists across all probes.
    Each probe starts a new conversation to avoid context bleed.
    """

    def __init__(
        self, config: ReversegentConfig, reasoning_client: ClientFactory | None = None
    ) -> None:
        self.config = config
        self._reasoning_client = reasoning_client
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._frame = None  # the frame holding the chat UI (may be an iframe)
        self._preset: SitePreset | None = None
        self._launched = False
        self._is_cdp = False
        self._chrome_process: subprocess.Popen | None = None

    @property
    def _dom(self):
        """DOM context for element queries — the chat frame, else the page.

        Element queries (query_selector/evaluate/wait_for_selector) must run in
        the frame that actually holds the chat (often an iframe). Page-level ops
        (keyboard, wait_for_timeout, goto, reload) still use self._page.
        """
        return self._frame if self._frame is not None else self._page

    # ── Lifecycle ─────────────────────────────────────────────────

    def launch(self) -> None:
        """Launch browser and navigate to target.  Called once before first probe."""
        self._playwright = sync_playwright().start()

        if self.config.browser_cdp_url:
            # CDP connection → attach to an already-running Chrome
            self._launch_cdp()
        elif self.config.browser_profile_path:
            # Persistent context → reuses cookies/auth from an existing profile
            self._launch_persistent()
        else:
            # Fresh browser instance
            self._launch_fresh()

        # For non-CDP, always navigate. For CDP, only if not already on target.
        if not self._is_cdp:
            log.info("Navigating to %s", self.config.target_base_url)
            self._page.goto(self.config.target_base_url, wait_until="domcontentloaded")
        elif self.config.target_base_url and not self._page_matches_target():
            log.info("Navigating to %s", self.config.target_base_url)
            self._page.goto(self.config.target_base_url, wait_until="domcontentloaded")

        # Resolve preset
        self._preset = self._resolve_preset()
        log.info("Using preset: %s", self._preset.name)

        self._launched = True

    def _launch_cdp(self) -> None:
        """Connect to Chrome via CDP, auto-launching if needed."""
        assert self._playwright is not None
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                self.config.browser_cdp_url
            )
        except Exception:
            # Connection failed — try to auto-launch Chrome with CDP
            log.info(
                "Could not connect to %s — launching Chrome automatically",
                self.config.browser_cdp_url,
            )
            self._auto_launch_chrome()
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(
                    self.config.browser_cdp_url
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not connect to Chrome at {self.config.browser_cdp_url}, "
                    f"even after auto-launch.\n"
                    f"Try launching Chrome manually:\n"
                    f"On macOS:\n"
                    f"  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
                    f"--remote-debugging-port=9222 "
                    f"--user-data-dir=/tmp/chrome-debug\n"
                    f"On Linux:\n"
                    f"  google-chrome --remote-debugging-port=9222 "
                    f"--user-data-dir=/tmp/chrome-debug\n"
                    f"Note: --user-data-dir is required — Chrome won't enable "
                    f"debugging without it.\n"
                    f"Original error: {exc}"
                ) from exc

        if not self._browser.contexts:
            raise RuntimeError(
                "Connected to Chrome via CDP but no browser contexts found."
            )

        self._context = self._browser.contexts[0]

        # Auto-launched Chrome with a fresh profile may have no tabs yet.
        if self._context.pages:
            self._page = self._find_target_page()
        else:
            log.info("CDP: no pages in browser, creating a new tab")
            self._page = self._context.new_page()

        self._is_cdp = True
        log.info("CDP: connected to %s, using page: %s", self.config.browser_cdp_url, self._page.url)

    def _auto_launch_chrome(self) -> None:
        """Launch Chrome with remote debugging enabled."""
        parsed = urlparse(self.config.browser_cdp_url)
        port = parsed.port or 9222

        if _is_mac():
            chrome_bin = (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            )
        else:
            chrome_bin = "google-chrome"

        user_data_dir = f"/tmp/chrome-debug-{port}"

        cmd = [
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
        ]
        log.info("Auto-launching Chrome: %s", " ".join(cmd))

        self._chrome_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for Chrome to start accepting CDP connections
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                self._playwright.chromium.connect_over_cdp(
                    self.config.browser_cdp_url
                ).close()
                log.info("Chrome is ready on port %d", port)
                return
            except Exception:
                time.sleep(0.5)

        log.warning("Chrome did not become ready within 15 seconds")

    def _launch_persistent(self) -> None:
        """Launch with a persistent Chrome profile."""
        assert self._playwright is not None
        self._context = self._playwright.chromium.launch_persistent_context(
            self.config.browser_profile_path,
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else self._context.new_page()
        )

    def _launch_fresh(self) -> None:
        """Launch a fresh browser instance."""
        assert self._playwright is not None
        self._browser = self._playwright.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def close(self) -> None:
        """Shut down browser.  Called when the agent run finishes."""
        if self._is_cdp:
            # CDP: disconnect only — do NOT close context (would close user's tabs)
            if self._browser:
                self._browser.close()
                self._browser = None
            self._context = None
        else:
            if self._context:
                self._context.close()
                self._context = None
            if self._browser:
                self._browser.close()
                self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        if self._chrome_process:
            log.info("Terminating auto-launched Chrome (pid %d)", self._chrome_process.pid)
            self._chrome_process.terminate()
            self._chrome_process = None
        self._page = None
        self._launched = False

    # ── CDP page finding ───────────────────────────────────────────

    def _find_target_page(self) -> Page:
        """Find the most appropriate page in a CDP-connected browser."""
        assert self._context is not None
        pages = self._context.pages

        if not pages:
            raise RuntimeError(
                "No pages found in the connected browser. "
                "Make sure Chrome has at least one tab open."
            )

        # 1. Match by target_base_url
        if self.config.target_base_url:
            for page in pages:
                if self.config.target_base_url in page.url:
                    log.info("CDP: found page matching target URL: %s", page.url)
                    return page

        # 2. Match by explicit preset url_pattern
        if self.config.browser_preset:
            preset = PRESETS.get(self.config.browser_preset)
            if preset:
                for page in pages:
                    if re.search(preset.url_pattern, page.url):
                        log.info("CDP: found page matching preset '%s': %s", preset.name, page.url)
                        return page

        # 3. Auto-match against all known presets
        for page in pages:
            for preset in PRESETS.values():
                if re.search(preset.url_pattern, page.url):
                    log.info("CDP: auto-matched page to preset '%s': %s", preset.name, page.url)
                    return page

        # 4. Fall back to last page
        log.info("CDP: no match found, using last page: %s", pages[-1].url)
        return pages[-1]

    def _page_matches_target(self) -> bool:
        """Check if the current page URL already matches the target."""
        assert self._page is not None
        if not self.config.target_base_url:
            return False
        return self.config.target_base_url in self._page.url

    # ── Core query method ─────────────────────────────────────────

    def query(self, messages: list[dict]) -> ChatCompletionMessage:
        """Send a probe and return the response as a ChatCompletionMessage.

        Each call starts a new conversation to isolate probes.
        Only user messages are typed — assistant turns from multi-turn
        strategies are dropped (browser chats don't support injecting them).
        """
        self._ensure_launched()
        assert self._page is not None
        assert self._preset is not None

        self._start_new_conversation()

        user_text = self._extract_user_text(messages)
        log.debug("Typing probe (%d chars)", len(user_text))

        # Count assistant messages BEFORE sending so we can wait for a brand-new
        # one to appear — this is what correlates the response to *this* probe and
        # prevents reading stale text from a prior turn.
        baseline_count = self._count_responses()

        self._type_message(user_text)
        self._send_message()
        response_text = self._wait_for_response(baseline_count)

        return ChatCompletionMessage(role="assistant", content=response_text)

    def _count_responses(self) -> int:
        """Number of assistant-message elements currently on the page/frame."""
        assert self._page is not None and self._preset is not None
        try:
            return self._dom.evaluate(
                "(s) => document.querySelectorAll(s).length",
                self._preset.response_selector,
            )
        except Exception:
            return 0

    # ── Preset resolution ─────────────────────────────────────────

    def _resolve_preset(self) -> SitePreset:
        """Resolve selectors AND the chat frame: manual → preset → agnostic auto.

        Sets self._frame to the frame (possibly an iframe) holding the chat, and
        may switch self._page to the tab that actually contains it.
        """
        assert self._page is not None
        cfg = self.config

        # 0. Manual: operator supplied input+response selectors. Still resolve
        # which frame they live in (could be an iframe).
        if cfg.browser_input_selector and cfg.browser_response_selector:
            ce = any(k in cfg.browser_input_selector.lower()
                     for k in ("contenteditable", "prosemirror", "ql-editor"))
            log.info("Using manual selectors from config")
            self._resolve_frame(cfg.browser_input_selector)
            return SitePreset(
                name="manual", url_pattern="",
                input_selector=cfg.browser_input_selector,
                send_selector=cfg.browser_send_selector or None,
                response_selector=cfg.browser_response_selector,
                input_is_contenteditable=ce,
                use_enter_to_send=not bool(cfg.browser_send_selector),
                reset_between_probes=False,
                max_response_wait_s=cfg.browser_max_response_wait,
            )

        # 1. Named preset — resolve the frame holding its input.
        if cfg.browser_preset:
            preset = PRESETS.get(cfg.browser_preset)
            if preset:
                self._resolve_frame(preset.input_selector)
                return self._apply_overrides(preset)
            log.warning("Unknown preset '%s', falling back to auto-detect", cfg.browser_preset)

        # 2. URL match against known presets.
        for preset in PRESETS.values():
            if preset.url_pattern and re.search(preset.url_pattern, self._page.url):
                log.info("Auto-matched preset '%s' from URL", preset.name)
                self._resolve_frame(preset.input_selector)
                return self._apply_overrides(preset)

        # 3a. Handshake mode (CDP): wait for the operator to navigate + send the
        # readiness marker, then calibrate from it. Avoids failing on a blank tab.
        if cfg.browser_cdp_url and cfg.browser_handshake:
            hs = self._handshake_then_calibrate()
            if hs is not None:
                return hs

        # 3b. AGNOSTIC auto-detection (immediate).
        detected = self._autodetect()
        if detected is not None:
            return detected

        # 4. LLM-powered detection fallback.
        if self._reasoning_client:
            log.info("Heuristic detection failed — trying LLM-powered detection")
            llm_detected = self._llm_detect_selectors()
            if llm_detected is not None:
                return llm_detected

        raise RuntimeError(
            "Could not detect a chat UI (searched all frames, tried opening "
            "widgets, heuristics, and the LLM). Pass --browser-input-selector "
            "and --browser-response-selector to specify them manually."
        )

    # ── Agnostic auto-detection ───────────────────────────────────────

    def _candidate_pages(self) -> list:
        """Pages worth searching, target-URL match first, newest last."""
        pages = [p for p in (self._context.pages if self._context else []) if not p.url.startswith("devtools://")]
        if not pages:
            return [self._page] if self._page else []
        tgt = self.config.target_base_url
        if tgt:
            pages.sort(key=lambda p: (tgt not in p.url, ))
        return pages

    def _resolve_frame(self, input_selector: str) -> None:
        """Find the frame (across all candidate pages) that holds *input_selector*
        and point self._page/self._frame at it."""
        for page in self._candidate_pages():
            for frame in autodetect._all_frames(page):
                try:
                    if frame.query_selector(input_selector):
                        self._page, self._frame = page, frame
                        if frame != page.main_frame:
                            log.info("Chat input is in iframe %s", (frame.url or "")[:60])
                        return
                except Exception:
                    continue
        # leave self._frame as-is (main frame) — selector wait will surface errors

    def _autodetect(self) -> SitePreset | None:
        """Find chat I/O across frames (opening collapsed widgets), then learn
        the response selector by sending a calibration nonce."""
        io = self._find_io_with_open()
        if io is None:
            return None
        self._page = self._page  # unchanged; _find_io_with_open sets _page/_frame
        self._frame = io.frame
        log.info("autodetect: input=%s send=%s ce=%s", io.input_selector, io.send_selector, io.input_is_contenteditable)

        # Provisional preset so _type_message/_send_message can fire the nonce.
        preset = SitePreset(
            name="auto", url_pattern="",
            input_selector=io.input_selector,
            send_selector=io.send_selector,
            response_selector="body",  # placeholder until calibrated
            input_is_contenteditable=io.input_is_contenteditable,
            use_enter_to_send=(io.send_selector is None),
            reset_between_probes=False,
            max_response_wait_s=self.config.browser_max_response_wait,
        )
        self._preset = preset

        response_selector = self._calibrate_response()
        if response_selector:
            log.info("autodetect: calibrated response selector = %s", response_selector)
            preset.response_selector = response_selector
            return self._apply_overrides(preset)

        # Calibration couldn't isolate the reply — fall back to a generic
        # assistant-message selector and let the quiet-gate handle it.
        log.warning("autodetect: nonce calibration failed; using generic response selector")
        preset.response_selector = (
            '[data-message-author-role="assistant"], [class*="assistant" i], '
            '[class*="bot" i], [class*="response" i], [class*="message" i]'
        )
        return self._apply_overrides(preset)

    def _marker_in_frame(self, frame, marker: str) -> bool:
        try:
            return bool(frame.evaluate(
                "m => !!(document.body && document.body.innerText && document.body.innerText.includes(m))",
                marker,
            ))
        except Exception:
            return False

    def _handshake_then_calibrate(self) -> SitePreset | None:
        """Wait for the operator to navigate to the target and send the readiness
        marker, then calibrate selectors from it. The marker doubles as the
        calibration nonce, so no extra message is sent."""
        marker = self.config.browser_handshake_marker
        timeout = self.config.browser_handshake_timeout
        print(
            f"\n[reversegent] Ready. In the browser, open your target chat and send "
            f"the message '{marker}' to begin probing (waiting up to {timeout}s)…",
            flush=True,
        )

        # Baseline: ignore tabs that already contain the marker (restored
        # sessions / a prior run) so we only fire on a fresh send.
        stale = set()
        for page in self._candidate_pages():
            for fr in autodetect._all_frames(page):
                if self._marker_in_frame(fr, marker):
                    stale.add(page.url)
                    break

        deadline = time.time() + timeout
        found_page = found_frame = None
        while time.time() < deadline and found_frame is None:
            for page in self._candidate_pages():
                if page.url in stale:
                    continue
                for fr in autodetect._all_frames(page):
                    if self._marker_in_frame(fr, marker):
                        found_page, found_frame = page, fr
                        break
                if found_frame is not None:
                    break
            if found_frame is None:
                self._page.wait_for_timeout(1500)

        if found_frame is None:
            log.warning("Handshake marker '%s' not seen within %ds", marker, timeout)
            return None

        self._page, self._frame = found_page, found_frame
        print(f"[reversegent] Detected '{marker}' on {found_page.url[:60]} — calibrating selectors…", flush=True)

        # Locate input/send in the marker's frame (fall back to scanning the page).
        io = autodetect.find_input_in_frame(found_frame) or autodetect.find_input(found_page)
        if io is None:
            log.warning("Found marker but no chat input in its frame")
            return None
        self._frame = io.frame

        preset = SitePreset(
            name="handshake", url_pattern="",
            input_selector=io.input_selector,
            send_selector=io.send_selector,
            response_selector="body",
            input_is_contenteditable=io.input_is_contenteditable,
            use_enter_to_send=(io.send_selector is None),
            reset_between_probes=False,
            max_response_wait_s=self.config.browser_max_response_wait,
        )
        self._preset = preset

        # The marker is already in the DOM — calibrate from it without sending.
        response_selector = self._calibrate_response(nonce=marker, send=False)
        if response_selector:
            log.info("handshake: calibrated response selector = %s", response_selector)
            preset.response_selector = response_selector
        else:
            log.warning("handshake: calibration inconclusive; using generic response selector")
            preset.response_selector = (
                '[data-message-author-role="assistant"], [class*="assistant" i], '
                '[class*="bot" i], [class*="agent" i], [class*="response" i], [class*="message" i]'
            )
        print(f"[reversegent] Calibrated. input={preset.input_selector!r} response={preset.response_selector!r}\n", flush=True)
        return self._apply_overrides(preset)

    def _find_io_with_open(self):
        """Locate chat input across pages/frames, opening collapsed widgets."""
        for attempt in range(4):
            for page in self._candidate_pages():
                io = autodetect.find_input(page)
                if io is not None:
                    self._page = page
                    return io
            # No input visible anywhere — try clicking a launcher, then re-scan.
            opened = False
            for page in self._candidate_pages():
                if autodetect.try_open_widget(page):
                    opened = True
            if not opened:
                break
            self._page.wait_for_timeout(1500)
        return None

    def _calibrate_response(self, nonce: str | None = None, send: bool = True) -> str | None:
        """Learn the assistant-bubble selector from where a marker lands vs. the
        reply that follows. Fully domain-agnostic.

        If *send* is True we type our own nonce; if False the marker (*nonce*) is
        already in the DOM (e.g. the operator's handshake message)."""
        assert self._preset is not None
        if nonce is None:
            nonce = f"rgcal{abs(hash(self.config.target_base_url)) % 100000}zx"
        if send:
            try:
                self._start_new_conversation()
                self._type_message(nonce)
                self._send_message()
            except Exception as exc:
                log.warning("calibration send failed: %s", exc)
                return None

        deadline = time.time() + min(self.config.browser_max_response_wait, 45)
        user_sel = None
        while time.time() < deadline:
            try:
                res = self._dom.evaluate(autodetect.CALIBRATE_JS, nonce)
            except Exception:
                res = None
            if res:
                user_sel = res.get("userSel") or user_sel
                if res.get("ready") and res.get("responseSel"):
                    return res["responseSel"]
            self._page.wait_for_timeout(800)
        return None

    def _apply_overrides(self, preset: SitePreset) -> SitePreset:
        """Apply any user-provided CSS selector overrides on top of a preset."""
        overrides: dict = {}
        if self.config.browser_input_selector:
            overrides["input_selector"] = self.config.browser_input_selector
        if self.config.browser_send_selector:
            overrides["send_selector"] = self.config.browser_send_selector
        if self.config.browser_response_selector:
            overrides["response_selector"] = self.config.browser_response_selector
        if self.config.browser_max_response_wait != 120:
            overrides["max_response_wait_s"] = self.config.browser_max_response_wait
        return replace(preset, **overrides) if overrides else preset

    # ── Conversation management ───────────────────────────────────

    def _start_new_conversation(self) -> None:
        """Navigate to a fresh chat to avoid context bleed between probes."""
        assert self._page is not None
        preset = self._preset
        assert preset is not None

        if not preset.reset_between_probes:
            # Embedded / stateful widgets keep one continuous thread — navigating
            # away would close the chat. Just make sure the input is ready.
            try:
                self._dom.wait_for_selector(
                    preset.input_selector, timeout=10_000, state="visible"
                )
            except Exception:
                log.debug("Input not ready (no-reset mode); continuing")
            return

        if preset.new_chat_url:
            self._page.goto(preset.new_chat_url, wait_until="domcontentloaded")
        elif preset.new_chat_selector:
            self._page.click(preset.new_chat_selector)
            self._page.wait_for_load_state("domcontentloaded")
        elif self.config.target_base_url:
            self._page.goto(self.config.target_base_url, wait_until="domcontentloaded")
        else:
            # Fallback: reload the page
            self._page.reload(wait_until="domcontentloaded")

        # Re-resolve the frame (navigation may have replaced it), then wait for
        # the input to be actually ready rather than guessing with a fixed sleep.
        self._resolve_frame(preset.input_selector)
        try:
            self._dom.wait_for_selector(
                preset.input_selector, timeout=10_000, state="visible"
            )
        except Exception:
            log.debug("Input selector not ready after new conversation; continuing")
        self._page.wait_for_timeout(300)

    # ── Message input ─────────────────────────────────────────────

    def _extract_user_text(self, messages: list[dict]) -> str:
        """Extract text to type from the message list.

        Multi-turn strategies with assistant messages are flattened:
        only user messages are kept and concatenated.
        """
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        return "\n\n".join(user_msgs)

    def _type_message(self, text: str) -> None:
        """Type a message into the chat input field."""
        assert self._page is not None
        preset = self._preset
        assert preset is not None

        input_el = self._dom.wait_for_selector(
            preset.input_selector, timeout=10_000
        )
        if input_el is None:
            raise RuntimeError(
                f"Chat input not found with selector: {preset.input_selector}"
            )

        if preset.input_is_contenteditable:
            # contenteditable divs (ProseMirror etc.) don't support fill()
            input_el.click()
            # Select all + delete to clear existing text
            mod = "Meta" if _is_mac() else "Control"
            self._page.keyboard.press(f"{mod}+a")
            self._page.keyboard.press("Backspace")
            # Type line-by-line, using Shift+Enter for newlines so that
            # bare Enter (which submits in most chat UIs) is never sent.
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line:
                    self._page.keyboard.type(line, delay=10)
                if i < len(lines) - 1:
                    self._page.keyboard.press("Shift+Enter")
            # Force ProseMirror/React to recognise the content change so that
            # the send button becomes enabled.
            self._dom.evaluate(
                "el => el.dispatchEvent(new Event('input', {bubbles: true}))",
                input_el,
            )
            self._page.wait_for_timeout(200)
        else:
            input_el.fill(text)

        # Verify the text actually landed. Contenteditable editors (ProseMirror,
        # Quill) silently drop fast keystrokes, which otherwise sends an empty or
        # partial probe. Confirm a representative slice is present before sending.
        if not self._verify_input(input_el, text):
            log.debug("Input verification failed — retyping once")
            input_el.click()
            mod = "Meta" if _is_mac() else "Control"
            self._page.keyboard.press(f"{mod}+a")
            self._page.keyboard.press("Backspace")
            if preset.input_is_contenteditable:
                # insert_text is atomic — avoids the dropped-keystroke problem
                # that per-character typing hits on ProseMirror/Quill.
                self._page.keyboard.insert_text(text)
            else:
                input_el.fill(text)
            self._dom.evaluate(
                "el => el.dispatchEvent(new Event('input', {bubbles: true}))",
                input_el,
            )
            self._page.wait_for_timeout(200)
            if not self._verify_input(input_el, text):
                log.warning("Input text did not fully register after retry")

    def _verify_input(self, input_el, text: str) -> bool:
        """Check that the typed probe is actually present in the input field."""
        assert self._page is not None
        # Compare on a normalized slice — editors may reflow whitespace/newlines.
        sample = "".join(text.split())[:60]
        if not sample:
            return True
        for _ in range(4):
            try:
                current = input_el.input_value()
            except Exception:
                current = input_el.inner_text() or ""
            if sample in "".join(current.split()):
                return True
            self._page.wait_for_timeout(150)
        return False

    def _send_message(self) -> None:
        """Click send or press Enter to submit the message."""
        assert self._page is not None
        preset = self._preset
        assert preset is not None

        if preset.send_selector and not preset.use_enter_to_send:
            # Retry up to 3 times — the send button may need a moment to
            # become enabled after typing (ProseMirror/React state updates).
            for attempt in range(3):
                try:
                    send_btn = self._dom.wait_for_selector(
                        preset.send_selector, timeout=3_000, state="visible"
                    )
                except Exception:
                    send_btn = None

                if send_btn is None:
                    if attempt < 2:
                        log.debug(
                            "Send button not found (attempt %d), retrying…",
                            attempt + 1,
                        )
                        self._page.wait_for_timeout(500)
                        continue
                    log.warning(
                        "Send button not found after retries, "
                        "falling back to Enter key"
                    )
                    self._page.keyboard.press("Enter")
                    return

                # Check whether the button is disabled
                is_disabled = send_btn.get_attribute("disabled") is not None
                aria_disabled = send_btn.get_attribute("aria-disabled")
                if is_disabled or aria_disabled == "true":
                    if attempt < 2:
                        log.debug(
                            "Send button disabled (attempt %d), retrying…",
                            attempt + 1,
                        )
                        self._page.wait_for_timeout(500)
                        continue
                    log.warning(
                        "Send button still disabled after retries, "
                        "trying JS click"
                    )

                # Attempt 1: standard Playwright click
                try:
                    send_btn.click(timeout=2_000)
                    return
                except Exception as exc:
                    log.debug("Standard click failed: %s, trying JS click", exc)

                # Attempt 2: JS click (bypasses overlay / disabled-state issues)
                try:
                    self._dom.evaluate("btn => btn.click()", send_btn)
                    return
                except Exception as exc:
                    log.debug("JS click also failed: %s", exc)

                if attempt < 2:
                    self._page.wait_for_timeout(500)

            # All retries exhausted — last resort
            log.warning("All send button attempts failed, falling back to Enter")
            self._page.keyboard.press("Enter")
        else:
            self._page.keyboard.press("Enter")

    # ── Response extraction ───────────────────────────────────────

    def _wait_for_response(self, baseline_count: int) -> str:
        """Wait for the streaming response to complete, then extract text.

        Completion is gated on three independent signals, all of which must hold:
          1. A *new* assistant message exists (count > baseline) — correlates the
             response to this probe and rules out stale text from a prior turn.
          2. The DOM inside that message has been quiet for `response_quiet_ms`,
             measured by an in-page MutationObserver (true gap, not poll-equality).
          3. The streaming indicator (stop button), if the preset defines one and
             it is actually present, has gone away.

        This eliminates the premature-truncation failure mode: a model that pauses
        mid-stream keeps the indicator up and resets the quiet timer, so we wait.
        """
        assert self._page is not None
        preset = self._preset
        assert preset is not None

        # Install the mutation observer scoped to the assistant-message subtree.
        try:
            self._dom.evaluate(_OBSERVER_JS, preset.response_selector)
        except Exception as exc:
            log.debug("Could not install mutation observer: %s", exc)

        # Initial delay — let streaming start and the new message node appear.
        self._page.wait_for_timeout(preset.post_send_delay_ms)

        start = time.time()
        deadline = start + preset.max_response_wait_s
        # No-indicator backstop window. When a streaming indicator IS available
        # and we actually observed it, the indicator going away is the real
        # "done" signal, so a much shorter settle suffices.
        backstop_quiet_ms = preset.response_quiet_ms
        settle_quiet_ms = 600
        # Escape hatch: if the indicator selector has drifted (always reports
        # "streaming"), don't hang forever — once the DOM has been quiet for well
        # past the backstop window with real text present, accept it anyway.
        hard_quiet_ms = max(backstop_quiet_ms * 2, 4500)
        saw_new = baseline_count == 0  # if page was empty, any message is "new"
        indicator_seen = False
        last_text = ""

        while time.time() < deadline:
            try:
                state = self._dom.evaluate(_STATE_JS, preset.response_selector)
            except Exception:
                state = {"count": 0, "text": "", "quietMs": 0}

            count = state.get("count", 0)
            text = state.get("text", "") or ""
            quiet = state.get("quietMs", 0)
            if count > baseline_count:
                saw_new = True
            if text:
                last_text = text

            streaming = self._is_streaming()
            if streaming:
                indicator_seen = True

            if saw_new and text:
                if quiet >= hard_quiet_ms:
                    return text  # indicator likely stale — accept on long quiet
                if not streaming:
                    # If we saw a trustworthy indicator and it's now gone, a short
                    # settle is enough. Otherwise require the full backstop window.
                    needed = settle_quiet_ms if indicator_seen else backstop_quiet_ms
                    if quiet >= needed:
                        return text

            self._page.wait_for_timeout(preset.poll_interval_ms)

        # Timeout — return whatever we managed to capture.
        log.warning("Response wait timed out after %ds", preset.max_response_wait_s)
        final = last_text or self._extract_last_response()
        if not final:
            raise TimeoutError(
                f"No response received within {preset.max_response_wait_s}s"
            )
        return final

    def _is_streaming(self) -> bool:
        """True if the streaming/stop indicator is currently present and visible."""
        assert self._page is not None and self._preset is not None
        selector = self._preset.streaming_indicator_selector
        if not selector:
            return False
        try:
            el = self._dom.query_selector(selector)
            return el is not None and el.is_visible()
        except Exception:
            return False

    def _extract_last_response(self) -> str:
        """Get the text content of the last assistant message on the page."""
        assert self._page is not None
        preset = self._preset
        assert preset is not None

        elements = self._dom.query_selector_all(preset.response_selector)
        if not elements:
            return ""
        last = elements[-1]
        return (last.inner_text() or "").strip()

    # ── Heuristic auto-detection ──────────────────────────────────

    def _auto_detect_selectors(self) -> SitePreset | None:
        """Heuristically detect chat UI elements on an unknown page.

        Returns None if detection fails (caller should try LLM fallback).
        """
        assert self._page is not None
        page = self._page

        # ── Find chat input ──
        input_selector = None
        input_is_contenteditable = False

        textarea_candidates = [
            'textarea[placeholder*="message" i]',
            'textarea[placeholder*="type" i]',
            'textarea[placeholder*="ask" i]',
            'textarea[placeholder*="chat" i]',
            "textarea",
        ]
        for sel in textarea_candidates:
            el = page.query_selector(sel)
            if el and el.is_visible():
                input_selector = sel
                break

        if not input_selector:
            ce_candidates = [
                'div[contenteditable="true"][role="textbox"]',
                'div[contenteditable="true"][data-placeholder]',
                'div[contenteditable="true"]',
            ]
            for sel in ce_candidates:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    input_selector = sel
                    input_is_contenteditable = True
                    break

        if not input_selector:
            log.info("Heuristic: could not find chat input")
            return None

        # ── Find send button ──
        send_selector = None
        send_candidates = [
            'button[type="submit"]',
            'button[aria-label*="send" i]',
            'button[data-testid*="send" i]',
        ]
        for sel in send_candidates:
            el = page.query_selector(sel)
            if el and el.is_visible():
                send_selector = sel
                break

        # ── Find response container ──
        response_selector = self._detect_message_container()

        log.info(
            "Auto-detected: input=%s, send=%s, response=%s",
            input_selector,
            send_selector,
            response_selector,
        )

        return SitePreset(
            name="auto-detected",
            url_pattern="",
            input_selector=input_selector,
            send_selector=send_selector,
            response_selector=response_selector or "body",
            input_is_contenteditable=input_is_contenteditable,
            use_enter_to_send=(send_selector is None),
            max_response_wait_s=self.config.browser_max_response_wait,
        )

    def _detect_message_container(self) -> str | None:
        """Try to find the CSS selector for assistant messages."""
        assert self._page is not None
        page = self._page

        # Look for elements matching common chat message patterns
        candidates = [
            '[data-message-author-role="assistant"]',
            '[class*="assistant"]',
            '[class*="bot-message"]',
            '[class*="response"]',
            '[class*="ai-message"]',
            '[class*="reply"]',
            '[role="log"] > div',
        ]
        for sel in candidates:
            els = page.query_selector_all(sel)
            if els:
                return sel

        return None

    # ── LLM-powered selector detection ────────────────────────────

    def _llm_detect_selectors(self) -> SitePreset | None:
        """Use the reasoning LLM to identify chat UI selectors from the page DOM."""
        if not self._reasoning_client:
            return None
        assert self._page is not None

        # Build a compact snapshot of the page for the LLM
        snapshot = self._build_page_snapshot()
        if not snapshot:
            log.warning("LLM detection: could not build page snapshot")
            return None

        user_prompt = (
            f"Page URL: {self._page.url}\n\n"
            f"Page snapshot:\n{snapshot}"
        )

        try:
            raw = self._reasoning_client.reason_json(
                system=SELECTOR_DETECTOR_SYSTEM,
                user=user_prompt,
            )
            data = json.loads(raw)
        except Exception as exc:
            log.warning("LLM selector detection failed: %s", exc)
            return None

        input_selector = data.get("input_selector", "")
        send_selector = data.get("send_selector")
        response_selector = data.get("response_selector", "")
        input_is_contenteditable = data.get("input_is_contenteditable", False)
        use_enter_to_send = data.get("use_enter_to_send", True)

        if not input_selector or not response_selector:
            log.warning("LLM returned incomplete selectors: %s", data)
            return None

        # Validate that selectors actually match elements on the page
        if not self._page.query_selector(input_selector):
            log.warning("LLM input_selector '%s' matched no elements", input_selector)
            return None
        if response_selector and not self._page.query_selector(response_selector):
            log.warning("LLM response_selector '%s' matched no elements — using anyway", response_selector)

        log.info(
            "LLM-detected: input=%s, send=%s, response=%s",
            input_selector,
            send_selector,
            response_selector,
        )

        preset = SitePreset(
            name="llm-detected",
            url_pattern="",
            input_selector=input_selector,
            send_selector=send_selector,
            response_selector=response_selector or "body",
            input_is_contenteditable=input_is_contenteditable,
            use_enter_to_send=use_enter_to_send,
            max_response_wait_s=self.config.browser_max_response_wait,
        )
        return self._apply_overrides(preset)

    def _build_page_snapshot(self) -> str | None:
        """Build a compact representation of the page for LLM analysis."""
        assert self._page is not None

        try:
            # Get accessibility tree — compact and role-based
            ax_tree = self._page.accessibility.snapshot()
        except Exception:
            ax_tree = None

        # Also grab interactive elements with their HTML attributes
        try:
            interactive_html = self._page.evaluate("""() => {
                const selectors = [
                    'textarea', 'input[type="text"]', 'input:not([type])',
                    '[contenteditable="true"]', 'button', '[role="textbox"]',
                    '[role="button"]', '[class*="message"]', '[class*="response"]',
                    '[class*="chat"]', '[class*="assistant"]', '[class*="reply"]',
                    '[data-message-author-role]', '[aria-label]'
                ];
                const seen = new Set();
                const results = [];
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (seen.has(el)) continue;
                        seen.add(el);
                        const tag = el.tagName.toLowerCase();
                        const attrs = {};
                        for (const attr of el.attributes) {
                            if (['class', 'id', 'role', 'aria-label', 'placeholder',
                                 'data-testid', 'contenteditable', 'type', 'name',
                                 'data-message-author-role', 'data-placeholder'].includes(attr.name)) {
                                attrs[attr.name] = attr.value;
                            }
                        }
                        const rect = el.getBoundingClientRect();
                        const visible = rect.width > 0 && rect.height > 0;
                        results.push({tag, attrs, visible, text: el.innerText?.slice(0, 80) || ''});
                    }
                    if (results.length > 200) break;
                }
                return results;
            }""")
        except Exception:
            interactive_html = None

        parts = []
        if ax_tree:
            parts.append(f"Accessibility tree:\n{json.dumps(ax_tree, indent=2, default=str)[:8000]}")
        if interactive_html:
            parts.append(f"Interactive elements:\n{json.dumps(interactive_html, indent=2)[:8000]}")

        return "\n\n".join(parts) if parts else None

    # ── Helpers ────────────────────────────────────────────────────

    def _ensure_launched(self) -> None:
        if not self._launched:
            raise RuntimeError(
                "BrowserClient not launched. Call launch() first."
            )
