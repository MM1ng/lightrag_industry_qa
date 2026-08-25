from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(r"D:/industrial_energy_agent/.tmp_ui_refresh_after.png")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto("http://localhost:8501", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".st-key-empty-state button", timeout=60000)
    page.wait_for_timeout(3000)
    info = page.evaluate(
        """() => {
      const app = getComputedStyle(document.querySelector('[data-testid="stApp"]'));
      const shell = getComputedStyle(document.querySelector('.st-key-qa-shell'));
      const status = getComputedStyle(document.querySelector('.st-key-status-bar'));
      const btn = getComputedStyle(document.querySelector('.st-key-empty-state button'));
      return {
        appBg: app.backgroundColor,
        shellMax: shell.maxWidth,
        statusBg: status.backgroundColor,
        statusMax: status.maxWidth,
        btnJustify: btn.justifyContent,
        btnTextAlign: btn.textAlign,
        btnRadius: btn.borderRadius,
        styleHasImportant: Array.from(document.querySelectorAll('style')).some(
          s => (s.textContent || '').includes('justify-content: flex-start !important')
        ),
      };
    }"""
    )
    page.screenshot(path=str(out), full_page=False)
    print(info)
    print("size", out.stat().st_size)
