"""Reversegent handshake — wait for the operator to navigate to a target chat
and send the marker message, then auto-detect the chat UI selectors and print a
ready-to-run command.

Usage:
    python3 scripts/handshake.py [marker] [--cdp URL] [--timeout SECONDS]

Defaults: marker="test_111", cdp=http://localhost:9222, timeout=600.

Navigate to any chat UI in the CDP Chrome window, sign in, and send the marker
message. This script detects it (across tabs and iframes), identifies the
input / send / response selectors, and tells you exactly how to launch
reversegent against it.
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

MARKER = "test_111"
CDP = "http://localhost:9222"
TIMEOUT = 600

# Parse simple args
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--cdp":
        CDP = args[i + 1]; i += 2
    elif args[i] == "--timeout":
        TIMEOUT = int(args[i + 1]); i += 2
    elif not args[i].startswith("--"):
        MARKER = args[i]; i += 1
    else:
        i += 1

DETECT_JS = r"""
(marker) => {
    const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const attrs = el => {
        const o = {};
        for (const a of el.attributes || []) {
            if (['data-testid','id','class','role','aria-label','placeholder','data-placeholder','contenteditable','type','data-message-author-role'].includes(a.name))
                o[a.name] = (a.value || '').slice(0, 80);
        }
        return o;
    };
    const sel1 = el => {
        if (!el) return null;
        const t = el.getAttribute && el.getAttribute('data-testid'); if (t) return `[data-testid="${t}"]`;
        if (el.id) return `#${el.id}`;
        const tag = el.tagName.toLowerCase();
        const ph = el.getAttribute && el.getAttribute('placeholder'); if (ph) return `${tag}[placeholder="${ph}"]`;
        const al = el.getAttribute && el.getAttribute('aria-label'); if (al) return `${tag}[aria-label="${al}"]`;
        const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean);
        if (cls.length) return `${tag}.${cls[0]}`;
        return tag;
    };

    const present = document.body && document.body.innerText && document.body.innerText.includes(marker);

    // INPUT
    let input = null, inputCE = false;
    for (const s of ['textarea[data-testid]','textarea[placeholder*="message" i]','textarea[placeholder*="type" i]','textarea[placeholder*="ask" i]','textarea[placeholder*="chat" i]','textarea']) {
        const el = document.querySelector(s); if (el && vis(el)) { input = el; break; }
    }
    if (!input) for (const s of ['div.ProseMirror[contenteditable="true"]','.ql-editor[contenteditable="true"]','div[contenteditable="true"][role="textbox"]','div[contenteditable="true"]']) {
        const el = document.querySelector(s); if (el && vis(el)) { input = el; inputCE = true; break; }
    }

    // SEND
    let send = null;
    for (const s of ['button[data-testid*="send" i]','button[aria-label*="send" i]','button[type="submit"]','button[data-testid*="submit" i]']) {
        const el = document.querySelector(s); if (el && vis(el)) { send = el; break; }
    }

    // RESPONSE container — prefer explicit assistant/agent markers
    let resp = null;
    for (const s of ['[data-testid="agent-message-content"]','[data-message-author-role="assistant"]','[data-testid*="agent-message" i]','[data-testid*="assistant" i]','[class*="assistant" i]','[class*="agent-message" i]','[class*="bot-message" i]','[class*="model-response" i]','[class*="response" i]']) {
        const els = document.querySelectorAll(s); if (els.length) { resp = s; break; }
    }

    return {
        present,
        input: input ? {selector: sel1(input), contenteditable: inputCE, attrs: attrs(input)} : null,
        send: send ? {selector: sel1(send), attrs: attrs(send)} : null,
        response: resp,
        url: location.href,
        title: document.title,
    };
}
"""


def scan(page, marker):
    """Scan a page and its frames; return detection dict if marker present."""
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    for fr in frames:
        try:
            data = fr.evaluate(DETECT_JS, marker)
        except Exception:
            continue
        if data and data.get("present"):
            data["frame_url"] = fr.url
            return data
    return None


def main():
    print(f"Handshake: waiting for marker '{MARKER}' in any chat tab on {CDP}")
    print("→ Navigate to your target chat, sign in, and send the marker message.\n")
    deadline = time.time() + TIMEOUT
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)

        # Baseline: tabs that ALREADY contain the marker (restored sessions, a
        # prior target) are stale — ignore them so we only fire on a fresh send.
        stale: set[str] = set()
        ctx0 = browser.contexts[0] if browser.contexts else None
        for pg in (ctx0.pages if ctx0 else []):
            if pg.url.startswith("devtools://"):
                continue
            try:
                if scan(pg, MARKER):
                    stale.add(pg.url)
            except Exception:
                pass
        if stale:
            print(f"  (ignoring {len(stale)} tab(s) that already show the marker — navigate to your NEW target)")

        last = ""
        while time.time() < deadline:
            ctx = browser.contexts[0] if browser.contexts else None
            pages = [pg for pg in (ctx.pages if ctx else []) if not pg.url.startswith("devtools://")]
            for pg in pages:
                if pg.url in stale:
                    continue
                try:
                    hit = scan(pg, MARKER)
                except Exception:
                    hit = None
                if hit:
                    report(hit)
                    browser.close()
                    return 0
            status = f"  …waiting ({len(pages)} tab(s): {', '.join(pg.url[:40] for pg in pages)})"
            if status != last:
                print(status); last = status
            time.sleep(3)
    print("Timed out — marker not seen.")
    return 1


def report(hit):
    print("\n" + "=" * 66)
    print(f"✅ MARKER DETECTED — ready to probe")
    print("=" * 66)
    print(f"URL    : {hit.get('url')}")
    print(f"Title  : {hit.get('title')}")
    if hit.get("frame_url") and hit["frame_url"] != hit.get("url"):
        print(f"Frame  : {hit['frame_url']}  (chat is inside an iframe)")
    inp, snd, resp = hit.get("input"), hit.get("send"), hit.get("response")
    print(f"\nDetected selectors:")
    print(f"  input    : {inp['selector'] if inp else 'NOT FOUND'}"
          + (f"  (contenteditable)" if inp and inp['contenteditable'] else ""))
    print(f"  send     : {snd['selector'] if snd else 'NOT FOUND (will use Enter)'}")
    print(f"  response : {resp or 'NOT FOUND'}")

    print("\nRun reversegent against it:")
    base = "https://" + (hit.get("url", "").split("/")[2] if "://" in hit.get("url", "") else "")
    cmd = [
        "python3 -m reversegent --target-type browser",
        "  --browser-cdp-url http://localhost:9222",
        f"  --target-url {hit.get('url')}",
    ]
    if inp:
        cmd.append(f"  --browser-input-selector '{inp['selector']}'")
    if snd:
        cmd.append(f"  --browser-send-selector '{snd['selector']}'")
    if resp:
        cmd.append(f"  --browser-response-selector '{resp}'")
    cmd.append("  -v -o result.txt")
    print(" \\\n".join(cmd))
    print("\n(If selectors look wrong, the live page may need a real reply first.)")


if __name__ == "__main__":
    sys.exit(main())
