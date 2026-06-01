"""Connect to the CDP Chrome and report what's on the page: whether the probe
handshake message is visible, and candidate chat-UI selectors."""
from __future__ import annotations
import sys
from playwright.sync_api import sync_playwright

NEEDLE = sys.argv[1] if len(sys.argv) > 1 else "test_111"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    ctx = browser.contexts[0]
    pages = ctx.pages
    print(f"{len(pages)} page(s):")
    for pg in pages:
        print("  -", pg.url[:100])
    page = pages[-1]
    print("\nUsing:", page.url)
    print("Title:", page.title())

    body = (page.inner_text("body") or "")
    print(f"\n'{NEEDLE}' present in page text: {NEEDLE in body}")

    # Candidate inputs
    info = page.evaluate("""(needle) => {
        const vis = el => { const r = el.getBoundingClientRect(); return r.width>0 && r.height>0; };
        const grab = sel => Array.from(document.querySelectorAll(sel)).filter(vis).map(el => ({
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            cls: (el.className && el.className.toString().slice(0,60)) || null,
            ph: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || null,
            aria: el.getAttribute('aria-label') || null,
            test: el.getAttribute('data-testid') || null,
        }));
        const inputs = [...grab('textarea'), ...grab('[contenteditable="true"]'), ...grab('[role="textbox"]')];
        const buttons = grab('button').filter(b => /send|submit|arrow/i.test(JSON.stringify(b)));
        // Find element containing the needle and its nearby blocks
        let needleEls = [];
        if (needle) {
            const all = document.querySelectorAll('div,p,span,article,section,li');
            for (const el of all) {
                if (el.children.length === 0 && el.innerText && el.innerText.includes(needle)) {
                    needleEls.push({tag: el.tagName.toLowerCase(), cls:(el.className||'').toString().slice(0,80)});
                    if (needleEls.length>3) break;
                }
            }
        }
        return {inputs: inputs.slice(0,8), buttons: buttons.slice(0,8), needleEls};
    }""", NEEDLE)

    import json
    print("\nInput candidates:");  print(json.dumps(info["inputs"], indent=2))
    print("\nSend-button candidates:"); print(json.dumps(info["buttons"], indent=2))
    print(f"\nElements containing '{NEEDLE}':"); print(json.dumps(info["needleEls"], indent=2))
    browser.close()
