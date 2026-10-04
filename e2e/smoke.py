"""Browser smoke test: drives the real UI, collects console errors and screenshots.

Run against a fresh server (first-run setup is performed):
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/w -w /w mcr.microsoft.com/playwright/python:v1.55.0-noble \
      sh -c "pip -q install playwright==1.55.0 && python e2e/smoke.py http://127.0.0.1:5055"
"""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5055"
OUT = Path("e2e/screens")
OUT.mkdir(parents=True, exist_ok=True)
XLSX = Path(".devdata/sample_inventory.xlsx")  # written by e2e/devserver.sh
errors = []


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("screenshot", name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    # ---- first-run setup (or login if already set up)
    page.goto(BASE + "/")
    if "/setup" in page.url:
        page.fill("input[name=username]", "nano")
        page.fill("input[name=display_name]", "Nano")
        page.fill("input[name=password]", "password123")
        page.fill("input[name=password2]", "password123")
        page.click("button[type=submit]")
    elif "/login" in page.url:
        page.fill("input[name=username]", "nano")
        page.fill("input[name=password]", "password123")
        page.click("button[type=submit]")
    page.wait_for_selector(".shell")
    shot(page, "01-empty")

    def check_icons(where):
        big = page.evaluate("""() => [...document.querySelectorAll('.btn svg, .chip svg')]
            .filter(s => s.getBoundingClientRect().width > 18).map(s => s.closest('.btn,.chip').textContent.trim().slice(0, 30))""")
        if big:
            errors.append(f"layout: oversized button icons on {where}: {big[:5]}")
    check_icons("empty state")

    # ---- import via the UI dialog into a new folder
    tree = page.locator("#tree")
    if not tree.locator(".node").count():
        csrf = page.get_attribute('meta[name="csrf-token"]', "content")
        r = page.request.post(BASE + "/api/folders", data={"name": "Storage"}, headers={"X-CSRFToken": csrf})
        fid = r.json()["id"]
        page.reload()
        page.wait_for_selector(".node.folder")
        page.click(f'.node.folder[data-id="{fid}"] [data-more]')
        page.click("text=Import XLSX here")
        page.set_input_files('[x-data="importer"] input[type=file]', str(XLSX))
        page.wait_for_selector("text=Box 01: Medicine Box")
        shot(page, "02-import-dialog")
        page.click('[x-data="importer"] .btn-primary')
        page.wait_for_selector("text=Imported", timeout=60000)

    page.wait_for_selector(".node.table")
    shot(page, "03-folder-view")

    # ---- open a table
    page.click('.node.table:has-text("Medicine")')
    page.wait_for_selector(".tabulator-row")
    expect(page.locator(".updated-chip")).to_contain_text("Updated")
    shot(page, "04-table")

    # ---- inline edit a cell
    cell = page.locator('.tabulator-row').first.locator('.tabulator-cell[tabulator-field="observation"]')
    cell.click()
    page.keyboard.type("Checked by smoke test")
    page.keyboard.press("Tab")
    page.wait_for_timeout(800)
    expect(page.locator(".updated-chip")).to_contain_text("just now")

    # ---- quantity editor
    q = page.locator('.tabulator-row').nth(1).locator('.tabulator-cell[tabulator-field="quantity"]')
    q.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("~88 tabs")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    expect(q).to_contain_text("~88 tabs")

    # ---- photo viewer
    page.locator(".thumbs img").first.click()
    page.wait_for_selector(".viewer-stage img")
    page.click('[aria-label="Zoom in"]')
    page.wait_for_timeout(300)
    shot(page, "05-viewer")
    # The delete confirmation must appear on top of the full-screen viewer
    page.click('.viewer [aria-label="Delete photo"]')
    page.wait_for_selector(".modal-back.dialog")
    on_top = page.evaluate("""() => { const b = document.querySelector('.modal-back.dialog [data-a="cancel"]').getBoundingClientRect();
        return !!document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2).closest('.modal-back.dialog'); }""")
    if not on_top:
        errors.append("layout: confirm dialog is hidden behind the photo viewer")
    shot(page, "05b-viewer-confirm")
    page.click('.modal-back.dialog [data-a="cancel"]')
    page.keyboard.press("Escape")

    # ---- row selection + bulk bar + delete/undo
    page.locator(".tabulator-row").nth(2).locator(".tabulator-row-header input, .tabulator-row-header").first.click()
    page.locator(".tabulator-row").nth(3).locator(".tabulator-row-header input, .tabulator-row-header").first.click()
    page.wait_for_selector(".row-bulk:not([hidden])")
    shot(page, "06-bulk")
    page.click('.row-bulk [data-b="delete"]')
    page.click('.modal [data-a="ok"]')
    page.wait_for_selector(".toast >> text=Undo")
    page.click(".toast >> text=Undo")
    page.wait_for_timeout(800)

    # ---- search palette
    page.keyboard.press("Control+k")
    page.fill(".palette-input input", "painkiller")
    page.wait_for_selector(".result")
    shot(page, "07-search")
    page.click(".palette .chip:has-text('Everywhere')")
    page.wait_for_selector(".scope-tree .scope-node")
    shot(page, "08-search-scope")
    page.keyboard.press("Escape")

    # ---- AI wizard (upload step only)
    page.click('.view:not([hidden]) [data-act="ai"]')
    page.wait_for_selector(".dropzone")
    check_icons("AI wizard")
    shot(page, "09-ai-wizard")
    page.keyboard.press("Escape")

    # ---- archive a table from the tree context menu
    page.click('.node.table:has-text("Laptops")', button="right")
    page.click("text=Archive (inactive)")
    page.wait_for_selector(".node.table.archived")
    shot(page, "10-archived")

    # ---- trash view
    page.click("text=Trash")
    page.wait_for_selector("h1:has-text('Trash')")

    # ---- "Added" column via the Columns menu
    page.click('.node.table:has-text("Office")')
    page.wait_for_selector(".tabulator-row")
    page.click('.view:not([hidden]) [data-act="columns"]')
    page.click("text=Added (who / when)")
    page.wait_for_timeout(400)
    shot(page, "10b-added-column")

    # ---- guided add: browse -> pick table -> type an item in -> back
    page.click(".topbar >> text=Guided add")
    page.wait_for_selector(".guided .g-capture")  # started on the open table
    page.click(".g-target >> text=Change")
    page.wait_for_selector(".guided .g-card.table")
    page.click(".guided .crumbs >> text=Top")
    page.wait_for_selector(".guided .g-card:has-text('Storage')")
    shot(page, "16-guided-where")
    page.click(".guided .g-card:has-text('Storage')")
    page.click(".guided .g-card.table:has-text('Office')")
    page.wait_for_selector(".g-capture")
    shot(page, "17-guided-capture")
    page.click("text=Type it in")
    page.fill("[data-manual-desc]", "Stapler (smoke test)")
    page.fill('.guided input[placeholder="1"]', "2")
    shot(page, "18-guided-manual")
    page.click("text=Add & take photos")
    page.wait_for_selector(".g-added:has-text('Stapler')")
    page.click(".guided-head >> text=Exit")
    page.wait_for_selector(".view:not([hidden]) .tabulator-row:has-text('Stapler')")

    # ---- Ask AI panel (no provider configured in the smoke test: just the UI)
    page.click(".topbar >> text=Ask AI")
    page.wait_for_selector(".chat-panel.open .chip")
    page.wait_for_timeout(400)  # slide-in transition
    shot(page, "19-chat-panel")
    page.click(".chat-head [aria-label=Close]")

    # ---- dark mode + mobile
    page.click(".avatar")
    page.click("text=Dark")
    page.click('.node.table:has-text("Zometool")')
    page.wait_for_selector(".tabulator-row")
    shot(page, "11-dark")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(500)
    shot(page, "12-mobile")
    hscroll = page.evaluate("""() => { const h = document.querySelector('.view:not([hidden]) .tabulator-tableholder');
        return h ? h.scrollWidth - h.clientWidth : -1; }""")
    if hscroll <= 0:
        errors.append(f"layout: table doesn't scroll horizontally on mobile ({hscroll})")
    page.evaluate("document.querySelector('.view:not([hidden]) .tabulator-tableholder').scrollLeft = 10000")
    page.wait_for_timeout(300)
    shot(page, "12b-mobile-scrolled")
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    if overflow > 1:
        errors.append(f"layout: page is {overflow}px wider than the mobile viewport")
    page.click(".menu-btn")
    page.wait_for_timeout(300)
    shot(page, "13-mobile-sidebar")

    # ---- admin
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(BASE + "/admin/#ai")
    page.wait_for_selector(".provider-card")
    shot(page, "14-admin-ai")
    # Save buttons must be enabled when idle, and saving must work
    page.goto(BASE + "/admin/#general")
    save = page.locator("text=Save appearance")
    expect(save).to_be_enabled()
    page.fill("input[x-model=\"values['app.tagline']\"]", "Smoke-tested tagline")
    save.click()
    page.wait_for_selector(".toast >> text=Settings saved")
    page.goto(BASE + "/admin/#ai")
    expect(page.locator("text=Save AI settings")).to_be_enabled()
    page.goto(BASE + "/admin/#search")
    page.wait_for_timeout(1500)
    shot(page, "15-admin-search")
    page.goto(BASE + "/admin/#users")
    page.wait_for_selector("text=Invite links")
    page.click("text=Generate link")
    page.wait_for_selector("code:has-text('/join/')")
    link = page.locator("code:has-text('/join/')").first.inner_text()
    shot(page, "20-admin-invites")
    page.goto(BASE + "/admin/#usage")
    page.wait_for_selector("text=Per user")
    shot(page, "21-admin-usage")
    # invite link opens the join page for a logged-out visitor
    anon = browser.new_context().new_page()
    anon.goto(link)
    anon.wait_for_selector("text=You've been invited")
    anon.screenshot(path=str(OUT / "22-join.png"))
    browser.close()

print("\n".join(errors) if errors else "NO CONSOLE ERRORS")
sys.exit(1 if errors else 0)
