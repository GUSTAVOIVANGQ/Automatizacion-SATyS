import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.pages[0]
    print(page.title())
    
    html = page.evaluate('document.body.outerHTML')
    with open('output/debug_178113.html', 'w', encoding='utf-8') as f:
        f.write(html)
