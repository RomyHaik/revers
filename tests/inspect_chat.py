"""Map the chat message structure: find the conversation container, enumerate
message bubbles, and identify a stable selector that matches assistant replies
(and ideally excludes the user's own messages)."""
from __future__ import annotations
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    page = browser.contexts[0].pages[-1]
    print("URL:", page.url, "\n")

    data = page.evaluate(r"""() => {
        const txt = el => (el.innerText||'').replace(/\s+/g,' ').trim().slice(0,80);
        // find the deepest element that literally contains test_111
        let userMsg = null;
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length===0 && (el.innerText||'').includes('test_111')) { userMsg = el; break; }
        }
        const chain = [];
        let n = userMsg;
        for (let i=0;i<8 && n;i++){
            const attrs={};
            for(const a of n.attributes||[]) if(['data-testid','role','data-message-author','class','aria-label'].includes(a.name)) attrs[a.name]=a.value.slice(0,70);
            chain.push({tag:n.tagName.toLowerCase(), attrs, text:txt(n)});
            n = n.parentElement;
        }

        // Look for any elements carrying data-testid containing 'message' or 'bubble'
        const byTestid = {};
        for (const el of document.querySelectorAll('[data-testid]')) {
            const t = el.getAttribute('data-testid');
            if (/message|bubble|chat|response|assistant|agent/i.test(t)) {
                byTestid[t] = (byTestid[t]||0)+1;
            }
        }

        // Heuristic: list candidate message containers near the input's scroll area
        const candidates = [];
        for (const sel of ['[data-testid*="message" i]','[data-testid*="bubble" i]','[class*="message" i]','[role="log"] *']) {
            const els = document.querySelectorAll(sel);
            if (els.length) candidates.push({sel, count: els.length, sampleLast: txt(els[els.length-1])});
        }
        return {ancestorChain: chain, messageTestids: byTestid, candidates};
    }""")

    print("Ancestor chain from the test_111 text node upward:")
    print(json.dumps(data["ancestorChain"], indent=2))
    print("\ndata-testid values mentioning message/chat/bubble/etc:")
    print(json.dumps(data["messageTestids"], indent=2))
    print("\nCandidate response selectors:")
    print(json.dumps(data["candidates"], indent=2))
    browser.close()
