"""Regenerate docs/screenshot.png from the sample workbook (run against a fresh e2e/devserver.sh)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5055"
Path("docs").mkdir(exist_ok=True)

with sync_playwright() as p:
    page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=2)
    page.goto(BASE + "/")
    if "/setup" in page.url:
        page.fill("input[name=username]", "demo")
        page.fill("input[name=display_name]", "Alex")
        page.fill("input[name=password]", "password123")
        page.fill("input[name=password2]", "password123")
        page.click("button[type=submit]")
    page.wait_for_selector(".shell")
    csrf = page.get_attribute('meta[name="csrf-token"]', "content")
    h = {"X-CSRFToken": csrf}
    f = page.request.post(BASE + "/api/folders", data={"name": "Camp 2026"}, headers=h).json()
    page.request.post(BASE + "/api/folders", data={"name": "Camp 2025"}, headers=h)
    page.request.post(BASE + "/api/import/xlsx", headers=h, multipart={
        "file": {"name": "sample.xlsx", "mimeType": "application/octet-stream",
                 "buffer": Path(".devdata/sample_inventory.xlsx").read_bytes()},
        "folder_id": str(f["id"])})
    page.reload()
    page.click('.node.folder:has-text("Camp 2026")')
    page.click('.node.table:has-text("Medicine")')
    page.wait_for_selector(".view:not([hidden]) .tabulator-row")
    rows = page.locator(".view:not([hidden]) .tabulator-row")
    rows.nth(0).locator(".tabulator-row-header").first.click()
    rows.nth(1).locator(".tabulator-row-header").first.click()
    page.wait_for_timeout(600)
    page.screenshot(path="docs/screenshot.png")
    print("saved docs/screenshot.png")
