from __future__ import annotations
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9222")
    pg = b.contexts[0].pages[-1]
    print("URL:", pg.url)
    out = pg.evaluate("""() => {
        const t = el => (el.innerText || '').replace(/\\s+/g, ' ').trim();
        const q = sel => Array.from(document.querySelectorAll(sel)).map(t);
        const indicators = [];
        for (const el of document.querySelectorAll('[data-testid]')) {
            const id = el.getAttribute('data-testid') || '';
            if (/typ|load|dots|spinner|stream|thinking|progress/i.test(id)) indicators.push(id);
        }
        return {
            agents: q('[data-testid="agent-message-content"]'),
            users: q('[data-testid="user-message-content"]'),
            system: q('[data-testid="system-message-content"]'),
            indicators: [...new Set(indicators)],
            inputPresent: !!document.querySelector('textarea[data-testid="chat-input-field"]'),
        };
    }""")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    b.close()
