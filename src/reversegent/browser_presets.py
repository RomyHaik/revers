"""Site presets — CSS selectors and behavior config for known chat UIs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SitePreset:
    """CSS selectors and behaviour config for a specific chat UI."""

    name: str
    url_pattern: str  # regex matched against the browser URL

    # Core selectors
    input_selector: str  # textarea or contenteditable div
    send_selector: str | None  # send button (None → use Enter key)
    response_selector: str  # all assistant message elements (last is read)

    # New conversation
    new_chat_url: str | None = None  # URL for a fresh conversation
    new_chat_selector: str | None = None  # button to click for new chat
    reset_between_probes: bool = True  # False → stay in one thread (embedded widgets)

    # Streaming detection
    streaming_indicator_selector: str | None = None  # present while streaming
    stop_button_selector: str | None = None  # "Stop generating" button

    # Input method
    use_enter_to_send: bool = True  # press Enter (vs click send button)
    input_is_contenteditable: bool = False  # div[contenteditable] inputs

    # Timing
    post_send_delay_ms: int = 1000  # wait after sending before polling
    poll_interval_ms: int = 400  # how often to check response
    max_response_wait_s: int = 120  # max seconds to wait
    response_quiet_ms: int = 2000  # no-indicator backstop: quiet this long → done


# ── Built-in presets ──────────────────────────────────────────────
#
# Reversegent ships with NO site-specific presets — it detects any chat UI
# automatically (see autodetect.py: searches frames, opens collapsed widgets,
# self-calibrates message selectors from a marker). If you ever need to pin a
# specific site, add a SitePreset entry here, or pass --browser-input-selector /
# --browser-send-selector / --browser-response-selector on the command line.

PRESETS: dict[str, SitePreset] = {}
