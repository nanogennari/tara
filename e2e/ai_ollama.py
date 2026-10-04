"""Real AI end-to-end through the UI: configure Ollama, upload photos, analyze, refine a row, commit.

  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/w -w /w \
    mcr.microsoft.com/playwright/python:v1.55.0-noble \
    sh -c "pip -q install --user playwright==1.55.0 && python e2e/ai_ollama.py http://127.0.0.1:5055 http://OLLAMA:11434 qwen3.6:latest [photo_dir]"
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE, OLLAMA, MODEL = sys.argv[1], sys.argv[2], sys.argv[3]
OUT = Path("e2e/screens")
OUT.mkdir(parents=True, exist_ok=True)
# Real photos to analyze: pass a folder of JPEGs as 4th argument (defaults to photos in .devdata/uploads)
src = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(".devdata/uploads")
photos = sorted(p for p in src.rglob("*.jpg") if p.name.count(".") == 1)[:3]
errors = []

with sync_playwright() as p:
    page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/login")
    page.fill("input[name=username]", "nano")
    page.fill("input[name=password]", "password123")
    page.click("button[type=submit]")
    page.wait_for_selector(".shell")
    csrf = page.get_attribute('meta[name="csrf-token"]', "content")
    h = {"X-CSRFToken": csrf}
    page.request.put(BASE + "/api/admin/settings", headers=h, data={
        "values": {"ai.provider": "ollama"},
        "providers": {"ollama": {"base_url": OLLAMA, "model": MODEL, "timeout": 600, "max_tokens": 8000}}})
    t = page.request.post(BASE + "/api/tables", headers=h, data={"name": "AI wizard test", "summary": "Office supplies"}).json()
    page.reload()
    page.click(f'.node.table[data-id="{t["id"]}"]')
    page.click('[data-act="ai"]')
    page.set_input_files('.dropzone input[multiple]', [str(x) for x in photos])
    page.fill('[x-data="aiWizard"] textarea', "Photos of camp supplies. Keep descriptions short.")
    page.screenshot(path=str(OUT / "ai-1-upload.png"))
    t0 = time.time()
    page.click("text=Analyze photos")
    page.wait_for_selector(".review tbody tr", timeout=600_000)
    print(f"proposal in {time.time() - t0:.0f}s:", page.locator(".review tbody tr").count(), "rows")
    page.screenshot(path=str(OUT / "ai-2-review.png"))

    first = page.locator(".review tbody tr").first
    before = first.locator("textarea").first.input_value()
    first.locator('[title="Ask AI to change this row"]').click()
    first.locator("[data-refine]").fill("Write this description in Brazilian Portuguese")
    t0 = time.time()
    first.locator("[data-refine]").press("Enter")
    page.wait_for_selector(".history .badge, [x-data=\"aiWizard\"] .alert-error:visible", timeout=300_000)
    after = page.locator(".review tbody tr").first.locator("textarea").first.input_value()
    print(f"refine in {time.time() - t0:.0f}s: {before!r} -> {after!r}")
    page.screenshot(path=str(OUT / "ai-3-refined.png"))

    n = page.locator(".review tbody tr").count()
    page.click('[x-data="aiWizard"] .modal-foot .btn-primary')
    page.wait_for_selector(".toast >> text=Added")
    page.wait_for_timeout(1000)
    rows = page.locator(".tabulator-row").count()
    print("rows in table:", rows, "of", n)
    expect(page.locator(".ai-badge").first).to_be_visible()
    page.screenshot(path=str(OUT / "ai-4-committed.png"))

    # ---- Ask AI with screen context: select two rows of the medicine box and ask about them
    page.click('.node.folder:has-text("Storage")')
    page.click('.node.table:has-text("Medicine")')
    page.wait_for_selector(".view:not([hidden]) .tabulator-row")
    rows = page.locator(".view:not([hidden]) .tabulator-row")
    rows.nth(0).locator(".tabulator-row-header").first.click()
    rows.nth(1).locator(".tabulator-row-header").first.click()
    page.click(".topbar >> text=Ask AI")
    page.fill(".chat-input textarea", "Are the selected items expired? Answer briefly.")
    t0 = time.time()
    page.press(".chat-input textarea", "Enter")
    page.wait_for_timeout(300)
    page.wait_for_selector(".chat-input textarea:not([disabled])", timeout=300_000)
    assert "2 rows selected" in page.locator(".chat-ctx").inner_text()
    print(f"chat (selection) in {time.time() - t0:.0f}s:", page.locator(".msg.assistant .bubble").last.inner_text()[:300])
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "ai-5-chat.png"))

    page.fill(".chat-input textarea", "Where are the laptop chargers? Open that table.")
    t0 = time.time()
    page.press(".chat-input textarea", "Enter")
    page.wait_for_timeout(300)
    page.wait_for_selector(".chat-input textarea:not([disabled])", timeout=300_000)
    print(f"chat (navigate) in {time.time() - t0:.0f}s:", page.locator(".msg.assistant .bubble").last.inner_text()[:300])
    page.wait_for_timeout(800)
    active = page.locator(".tab.active .label").inner_text()
    print("active tab after chat:", active)
    page.screenshot(path=str(OUT / "ai-6-chat-navigated.png"))
    page.click(".chat-head [aria-label=Close]")

    # ---- Guided add with photos
    page.click(".topbar >> text=Guided add")
    page.wait_for_selector(".g-capture")
    page.set_input_files('.g-capture input[multiple]', [str(x) for x in photos[:2]])
    t0 = time.time()
    page.click("text=Identify items in 2 photos")
    page.wait_for_selector(".g-item", timeout=300_000)
    print(f"guided proposal in {time.time() - t0:.0f}s:", page.locator(".g-item").count(), "items")
    page.screenshot(path=str(OUT / "ai-7-guided-confirm.png"))
    page.click(".guided >> text=& continue")
    page.wait_for_selector(".g-added")
    print("guided added:", page.locator(".g-added").count())
    page.screenshot(path=str(OUT / "ai-8-guided-loop.png"))
    page.click(".guided-head >> text=Exit")

print("ERRORS:", errors if errors else "none")
