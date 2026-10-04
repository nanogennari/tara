"""Settings → AI: load models from a real Ollama server and pick one from the dropdown."""
import sys
from playwright.sync_api import expect, sync_playwright
BASE, OLLAMA = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    page = p.chromium.launch().new_page(viewport={"width": 1280, "height": 900})
    page.goto(BASE + "/")
    if "/setup" in page.url:
        page.fill("input[name=username]", "demo"); page.fill("input[name=password]", "password123")
        page.fill("input[name=password2]", "password123"); page.click("button[type=submit]")
        page.wait_for_selector(".shell")
    elif "/login" in page.url:
        page.fill("input[name=username]", "demo"); page.fill("input[name=password]", "password123")
        page.click("button[type=submit]"); page.wait_for_selector(".shell")
    page.goto(BASE + "/admin/#ai")
    page.click(".provider-card:has-text('Ollama')")
    page.fill("input[x-model='cur.base_url']", OLLAMA)
    print("before:", page.locator("label:has-text('Model') select option").all_inner_texts())
    page.click("button:has-text('Load available models')")
    page.wait_for_function("[...document.querySelectorAll(\"label select option\")].some(o => o.value === 'qwen3.6:latest')", timeout=30000)
    opts = page.locator("label:has-text('Model') select option").all_inner_texts()
    print("after:", opts)
    print("hint:", page.locator("label:has(select) .hint").first.inner_text())
    page.select_option("label:has-text('Model') select", "qwen3.6:latest")
    page.click("text=Save AI settings")
    page.wait_for_selector(".toast >> text=Settings saved")
    page.reload(); page.wait_for_selector(".provider-card")
    print("saved model:", page.evaluate("Alpine.$data(document.querySelector('[x-data=admin]')).cur.model"))
    page.screenshot(path="e2e/screens/admin-models.png")
