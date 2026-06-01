"""Domain-agnostic chat-UI detection.

Ships inside reversegent so ANY user can point the browser target at an
arbitrary chat site with no presets and no manual selectors. The pipeline:

  1. Search every frame (including iframes) of every tab for a chat input.
  2. If none is visible, click likely "launcher" buttons to open the widget,
     then re-scan (a new iframe often appears at this point).
  3. Locate the send control (or fall back to Enter).
  4. Self-calibrate the message selectors: the caller sends a unique nonce; we
     find it in the DOM to learn the USER-bubble selector, then watch for the
     next new bubble to learn the ASSISTANT-bubble (response) selector.

Everything is heuristic + structural, so it generalises across custom and
embedded chat widgets without any site-specific configuration. The reasoning
LLM is a last-resort fallback handled by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Input candidates, most-specific first. (selector, is_contenteditable)
_INPUT_SELECTORS: list[tuple[str, bool]] = [
    ('textarea[placeholder*="message" i]', False),
    ('textarea[placeholder*="type" i]', False),
    ('textarea[placeholder*="ask" i]', False),
    ('textarea[placeholder*="chat" i]', False),
    ('textarea[aria-label*="message" i]', False),
    ("textarea[data-testid]", False),
    ("textarea", False),
    ('div.ProseMirror[contenteditable="true"]', True),
    ('.ql-editor[contenteditable="true"]', True),
    ('div[contenteditable="true"][role="textbox"]', True),
    ('div[contenteditable="true"][data-placeholder]', True),
    ('div[contenteditable="true"]', True),
    ('[role="textbox"]', True),
    ('input[type="text"][placeholder*="message" i]', False),
    ('input[type="text"][placeholder*="chat" i]', False),
]

_SEND_SELECTORS = [
    'button[data-testid*="send" i]',
    'button[aria-label*="send" i]',
    'button[aria-label*="送信"]',
    'button[title*="send" i]',
    'button[type="submit"]',
    'button[class*="send" i]',
]

# Launcher buttons that open a collapsed chat widget.
_LAUNCHER_SELECTORS = [
    'button[aria-label*="chat" i]',
    'button[aria-label*="message" i]',
    'button[aria-label*="help" i]',
    'button[aria-label*="support" i]',
    'button[aria-label*="assistant" i]',
    'button[aria-label*="open" i]',
    'button[title*="chat" i]',
    '[class*="launcher" i]',
    '[id*="launcher" i]',
    '[class*="chat-button" i]',
    '[class*="chat-widget" i]',
    '[class*="chat-bubble" i]',
    '[data-testid*="launch" i]',
    '[aria-label*="live chat" i]',
    'button[class*="chat" i]',
    'div[role="button"][class*="chat" i]',
]


@dataclass
class IODetection:
    frame: object  # Playwright Frame
    input_selector: str
    input_is_contenteditable: bool
    send_selector: str | None


def _all_frames(page):
    """Main frame first, then sub-frames."""
    try:
        return [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    except Exception:
        return [page]


def _visible(frame, selector: str) -> bool:
    try:
        el = frame.query_selector(selector)
        return bool(el and el.is_visible())
    except Exception:
        return False


def find_input_in_frame(frame) -> IODetection | None:
    """Find a visible chat input within a single (already known) frame."""
    for selector, is_ce in _INPUT_SELECTORS:
        if _visible(frame, selector):
            send = _find_send(frame)
            log.info(
                "autodetect: input '%s' in frame %s (send=%s)",
                selector, (frame.url or "main")[:50], send,
            )
            return IODetection(frame, selector, is_ce, send)
    return None


def find_input(page) -> IODetection | None:
    """Find a visible chat input across all frames of a page."""
    for frame in _all_frames(page):
        io = find_input_in_frame(frame)
        if io is not None:
            return io
    return None


def _find_send(frame) -> str | None:
    for selector in _SEND_SELECTORS:
        if _visible(frame, selector):
            return selector
    return None


def try_open_widget(page) -> bool:
    """Click the most likely launcher to reveal a collapsed chat widget.

    Returns True if something was clicked (caller should wait and re-scan)."""
    for frame in _all_frames(page):
        for selector in _LAUNCHER_SELECTORS:
            try:
                el = frame.query_selector(selector)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    log.info("autodetect: clicked launcher '%s'", selector)
                    return True
            except Exception:
                continue
    return False


# ── Nonce calibration ─────────────────────────────────────────────────
# Sent by the caller via the input; this JS then learns the user- and
# assistant-bubble selectors structurally from where the nonce landed.

CALIBRATE_JS = r"""
(nonce) => {
    const MSG_RE = /message|bubble|msg|chat.?gpt.?message|cx-messenger|response|reply|turn/i;
    const ROLE_RE = /inbound|outbound|user|self|me\b|human|sent|bot|assistant|agent|server|ai\b|business|model|reply|response|left|right/i;

    const distinctiveSelector = (el) => {
        if (!el) return null;
        const tid = el.getAttribute && el.getAttribute('data-testid');
        if (tid) return `[data-testid="${tid}"]`;
        const role = el.getAttribute && el.getAttribute('data-message-author-role');
        if (role) return `[data-message-author-role="${role}"]`;
        const classes = (el.className || '').toString().trim().split(/\s+/).filter(Boolean);
        const meaningful = classes.filter(c => MSG_RE.test(c) || ROLE_RE.test(c));
        if (meaningful.length) return '.' + meaningful.join('.');
        if (classes.length) return '.' + classes[0];
        return el.tagName.toLowerCase();
    };

    // Find the leaf element holding the nonce text.
    let leaf = null;
    for (const el of document.querySelectorAll('body *')) {
        if (el.children.length === 0 && (el.textContent || '').includes(nonce)) { leaf = el; break; }
    }
    if (!leaf) return {ready: false, reason: 'nonce-not-found'};

    // Climb to the user's message container.
    let userBubble = leaf, hops = 0;
    while (userBubble && hops < 6) {
        if (MSG_RE.test((userBubble.className || '').toString()) ||
            (userBubble.getAttribute && (userBubble.getAttribute('data-testid') || '').match(MSG_RE)))
            break;
        userBubble = userBubble.parentElement; hops++;
    }
    if (!userBubble) userBubble = leaf.parentElement || leaf;
    const userSel = distinctiveSelector(userBubble);

    // Find an assistant bubble: a message-like container, with text, that does
    // NOT contain the nonce and comes AFTER the user bubble in document order.
    const containers = [...document.querySelectorAll('[class],[data-testid]')].filter(el =>
        MSG_RE.test((el.className || '').toString()) ||
        ((el.getAttribute('data-testid') || '')).match(MSG_RE) ||
        el.hasAttribute('data-message-author-role'));
    let resp = null;
    for (const el of containers) {
        const t = (el.innerText || '').trim();
        if (!t || t.includes(nonce)) continue;
        if (userBubble && (el === userBubble || userBubble.contains(el) || el.contains(userBubble))) continue;
        if (userBubble && (userBubble.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) {
            resp = el; // keep last following → latest reply
        }
    }
    if (!resp) return {ready: false, userSel, reason: 'no-reply-yet'};

    const respSel = distinctiveSelector(resp);
    // Avoid a response selector identical to the user selector.
    if (respSel === userSel) return {ready: false, userSel, reason: 'selectors-collide'};
    return {ready: true, userSel, responseSel: respSel, sample: (resp.innerText || '').slice(0, 80)};
}
"""
