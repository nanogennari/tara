// Folder overview, Trash and full search results views.
import { ageDays, confirmDialog, contextMenu, debounce, del, esc, fmtDateTime, get, icon, icons, post, relTime, toast, toastError } from "./util.js";
import { bulk, downloadPhotos } from "./tree.js";
import { applyGridVars, gridDefault } from "./prefs.js";

// ------------------------------------------------------------ folder view
export class FolderView {
  constructor(el, tab, store) {
    this.el = el; this.tab = tab; this.s = store; this.id = tab.id;
    this.sort = { key: "name", dir: 1 };
  }

  async load() {
    try { this.data = await get(`/api/folders/${this.id}`); }
    catch (e) {
      this.el.innerHTML = `<div class="empty-state">${icon("folder-x")}<p>${esc(e.message)}</p></div>`;
      return icons(this.el);
    }
    this.tab.title = this.data.folder.name;
    this.render();
  }

  render() {
    const { folder, folders, tables } = this.data;
    const ed = this.s.user.can_edit;
    const k = this.sort.key, dir = this.sort.dir;
    const sorted = [...tables].sort((a, b) => {
      const va = k === "updated" ? a.content_updated_at : k === "items" ? a.item_count : k === "status" ? a.effective_active : a.name.toLowerCase();
      const vb = k === "updated" ? b.content_updated_at : k === "items" ? b.item_count : k === "status" ? b.effective_active : b.name.toLowerCase();
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
    const arrow = (key) => (k === key ? (dir > 0 ? " ↑" : " ↓") : "");
    const crumbs = folder.path.slice(0, -1).map((n, i) => {
      const f = this.s.folderPath(folder.id)[i];
      return `<a href="#" data-folder="${f?.id}">${esc(n)}</a>${icon("chevron-right")}`;
    }).join("");
    const stale = this.s.staleDays;

    this.el.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div class="grow stack" style="gap:4px">
            <nav class="crumbs">${icon("folder")}${crumbs}<span>${esc(folder.name)}</span></nav>
            <div class="row wrap"><h1>${esc(folder.name)}</h1>
              ${!folder.effective_active ? `<span class="badge badge-warn">${icon("archive")} Archived</span>` : ""}</div>
          </div>
          <div class="row wrap">
            ${ed && folder.effective_active ? `<button class="btn btn-ai" data-a="guided">${icon("scan-line")} Guided add</button>` : ""}
            ${ed ? `<button class="btn btn-primary" data-a="new-table">${icon("plus")} New table</button>
            <button class="btn" data-a="new-folder">${icon("folder-plus")} Subfolder</button>
            <button class="btn" data-a="import">${icon("file-spreadsheet")} Import XLSX</button>` : ""}
            <button class="btn" data-a="all">${icon("list")} See all items</button>
            <button class="btn" data-a="search">${icon("search")} Search here</button>
            <button class="btn" data-a="export">${icon("download")} Export</button>
            <button class="btn" data-a="photos">${icon("images")} Photos (ZIP)</button>
            ${ed ? `<button class="btn" data-a="archive">${icon(folder.active ? "archive" : "archive-restore")} ${folder.active ? "Archive" : "Reactivate"}</button>` : ""}
          </div>
        </div>
        ${folders.length ? `<div class="cards">${folders.map((f) => `
          <div class="card click ${f.effective_active ? "" : "archived"}" data-open-folder="${f.id}">
            <div class="row">${icon(f.effective_active ? "folder" : "archive")}<strong class="truncate grow">${esc(f.name)}</strong></div>
            <div class="small muted" style="margin-top:6px">${f.content_updated_at ? "Updated " + esc(relTime(f.content_updated_at)) : "No tables yet"}</div>
          </div>`).join("")}</div>` : ""}
        ${tables.length ? `
        <div class="list"><table>
          <thead><tr>
            <th data-sort="name">Table${arrow("name")}</th>
            <th data-sort="items" class="hide-sm">Items${arrow("items")}</th>
            <th data-sort="updated">Last updated${arrow("updated")}</th>
            <th class="hide-sm">By</th>
            <th data-sort="status" class="hide-sm">Status${arrow("status")}</th>
          </tr></thead>
          <tbody>${sorted.map((t) => `
            <tr class="click" data-open-table="${t.id}">
              <td><div class="row">${icon(t.effective_active ? "table-2" : "archive")}<div><strong>${esc(t.name)}</strong>${t.summary ? `<div class="small muted">${esc(t.summary)}</div>` : ""}</div></div></td>
              <td class="hide-sm">${t.item_count}</td>
              <td title="${esc(fmtDateTime(t.content_updated_at))}"><span class="${ageDays(t.content_updated_at) > stale ? "badge badge-warn" : ""}">${esc(relTime(t.content_updated_at))}</span></td>
              <td class="hide-sm muted">${esc(t.content_updated_by || "")}</td>
              <td class="hide-sm">${t.effective_active ? '<span class="badge badge-ok">Active</span>' : '<span class="badge badge-warn">Archived</span>'}</td>
            </tr>`).join("")}</tbody>
        </table></div>` : ""}
        ${!tables.length && !folders.length ? `<div class="empty-state">${icon("folder-open")}<p>This folder is empty.</p></div>` : ""}
      </div>`;
    icons(this.el);
    this.el.onclick = (e) => {
      const t = e.target.closest("[data-open-table]");
      if (t) return this.s.openTable(Number(t.dataset.openTable));
      const f = e.target.closest("[data-open-folder]") || e.target.closest("[data-folder]");
      if (f) { e.preventDefault(); return this.s.openFolder(Number(f.dataset.openFolder || f.dataset.folder)); }
      const th = e.target.closest("[data-sort]");
      if (th) {
        this.sort = { key: th.dataset.sort, dir: this.sort.key === th.dataset.sort ? -this.sort.dir : (th.dataset.sort === "updated" ? -1 : 1) };
        return this.render();
      }
      const a = e.target.closest("[data-a]")?.dataset.a;
      if (!a) return;
      ({
        "new-table": () => this.s.newTable(this.id),
        guided: () => window.dispatchEvent(new CustomEvent("guided:open", { detail: { folderId: this.id } })),
        "new-folder": () => this.s.newFolder(this.id).then(() => this.load()),
        import: () => window.dispatchEvent(new CustomEvent("import:open", { detail: { folderId: this.id } })),
        search: () => window.dispatchEvent(new CustomEvent("palette:open", { detail: { folders: [this.id] } })),
        all: () => this.s.openFolderItems(this.id),
        export: () => { location.href = `/api/export/xlsx?folder_id=${this.id}`; },
        photos: () => downloadPhotos(`folder_id=${this.id}`),
        archive: () => bulk(this.s, folder.active ? "archive" : "reactivate", { folders: [this.id], tables: [] }).then(() => this.load()),
      })[a]?.();
    };
  }
}

// ------------------------------------------------------------ all items in a folder (read-only)
export class FolderItemsView {
  constructor(el, tab, store) {
    this.el = el; this.tab = tab; this.s = store; this.id = tab.id;
    this.includeArchived = false;
    this.filterText = "";
    this.grid = null;
    this.onGridView = () => { applyGridVars(this.el, gridDefault()); this.grid?.redraw(true); };
    window.addEventListener("grid:view", this.onGridView);
  }

  async load() {
    if (!this.data) this.el.innerHTML = `<div class="empty-state"><span class="spinner"></span></div>`;
    try { this.data = await get(`/api/folders/${this.id}/items${this.includeArchived ? "?include_archived=1" : ""}`); }
    catch (e) {
      this.el.innerHTML = `<div class="empty-state">${icon("folder-x")}<p>${esc(e.message)}</p></div>`;
      return icons(this.el);
    }
    this.tab.title = `All in ${this.data.folder.name}`;
    this.render();
  }

  onShow() { this.grid?.redraw(); }
  close() { window.removeEventListener("grid:view", this.onGridView); this.grid?.destroy(); }

  render() {
    const d = this.data;
    this.grid?.destroy();
    applyGridVars(this.el, gridDefault());
    const crumbs = d.folder.path.slice(0, -1).map((n, i) => {
      const f = this.s.folderPath(d.folder.id)[i];
      return `<a href="#" data-folder="${f?.id}">${esc(n)}</a>${icon("chevron-right")}`;
    }).join("");
    this.el.innerHTML = `
      <header class="tv-head">
        <nav class="crumbs">${icon("folder")}${crumbs}<a href="#" data-folder="${d.folder.id}">${esc(d.folder.name)}</a>${icon("chevron-right")}<span>All items</span></nav>
        <div class="title-row"><h1>All items in ${esc(d.folder.name)}</h1>
          <span class="badge">${d.items.length} items · ${d.tables} tables</span></div>
        <p class="summary empty" style="cursor:default">Everything in this folder and its subfolders. Click a row to open it in its table.</p>
      </header>
      <div class="toolbar">
        <input class="filter" type="search" placeholder="Filter items…" value="${esc(this.filterText)}" aria-label="Filter items">
        <label class="check small"><input type="checkbox" data-arch ${this.includeArchived ? "checked" : ""}> Include archived tables</label>
        <span class="grow"></span>
        <a class="btn" href="/api/export/xlsx?folder_id=${d.folder.id}">${icon("download")}<span class="label-sm">Export</span></a>
        <button class="btn" data-photos>${icon("images")}<span class="label-sm">Photos (ZIP)</span></button>
      </div>
      <div class="grid-wrap"><div class="grid"></div></div>`;
    icons(this.el);
    this.el.querySelectorAll("[data-folder]").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault(); this.s.openFolder(Number(a.dataset.folder));
    }));
    this.el.querySelector("[data-arch]").addEventListener("change", (e) => { this.includeArchived = e.target.checked; this.load(); });
    this.el.querySelector("[data-photos]").addEventListener("click", () =>
      downloadPhotos(`folder_id=${this.id}&include_archived=${this.includeArchived ? 1 : 0}`));
    this.el.querySelector(".filter").addEventListener("input", debounce((e) => { this.filterText = e.target.value; this.applyFilter(); }, 150));

    const mobile = matchMedia("(max-width: 600px)").matches;
    const max = (desk, phone) => (mobile ? phone : desk);
    this.grid = new Tabulator(this.el.querySelector(".grid"), {
      data: d.items, index: "id", height: "100%", layout: "fitData", placeholder: "No items in this folder.",
      columnDefaults: { vertAlign: "top", headerSortTristate: true },
      initialSort: [{ column: "path", dir: "asc" }],
      rowFormatter: (row) => { row.getElement().dataset.id = row.getData().id; row.getElement().classList.toggle("archived-row", row.getData().archived); },
      columns: [
        { title: "Path", field: "path", maxWidth: max(320, 180), formatter: (c) => `<a href="/t/${c.getRow().getData().table_id}" class="path-link">${esc(c.getValue())}</a>` },
        { title: "Item", field: "description", maxWidth: max(420, 220), formatter: (c) => esc(c.getValue()) },
        { title: "Qty", field: "quantity_display", sorter: (a, b, ra, rb) => (ra.getData().quantity?.value ?? -1) - (rb.getData().quantity?.value ?? -1),
          formatter: (c) => (c.getRow().getData().quantity?.estimated ? `<span class="est">${esc(c.getValue())}</span>` : esc(c.getValue())), cssClass: "cell-qty" },
        { title: "Obs", field: "observation", maxWidth: max(360, 200), formatter: (c) => esc(c.getValue()) },
        { title: "Fields", field: "fields", maxWidth: max(320, 200), formatter: (c) => esc(c.getValue()) },
        { title: "Photo", field: "photos", headerSort: false, cssClass: "cell-photos", formatter: (c) => {
          const it = c.getRow().getData();
          return `<div class="thumbs">${it.photos.slice(0, 2).map((p, i) => `<img src="${p.thumb}" alt="" loading="lazy" data-i="${i}">`).join("")}${it.photos.length > 2 ? `<span class="more-n">+${it.photos.length - 2}</span>` : ""}</div>`;
        } },
        { title: "Added", field: "created_at", formatter: (c) => {
          const r = c.getRow().getData();
          return `<span class="updated-cell">${esc(fmtDateTime(r.created_at))}${r.created_by ? " · " + esc(r.created_by) : ""}</span>`;
        } },
      ],
    });
    this.grid.on("tableBuilt", () => this.applyFilter());
    this.grid.on("rowClick", (e, row) => {
      const it = row.getData();
      const img = e.target.closest(".thumbs img, .thumbs .more-n");
      if (img) {
        e.preventDefault();
        return window.dispatchEvent(new CustomEvent("viewer:open", { detail: { photos: it.photos, index: Number(img.dataset.i || 2), title: it.description } }));
      }
      if (e.ctrlKey || e.metaKey || e.button === 1) return; // let path links open in a new tab
      e.preventDefault();
      this.s.openTable(it.table_id, { rowId: it.id, select: true });
    });
    this.el.querySelector(".grid").addEventListener("contextmenu", (e) => {
      const rowEl = e.target.closest(".tabulator-row");
      const row = rowEl && this.grid.getRow(Number(rowEl.dataset.id));
      if (!row) return;
      e.preventDefault();
      const it = row.getData();
      contextMenu(e.clientX, e.clientY, [
        { label: "Open in its table", icon: "table-2", action: () => this.s.openTable(it.table_id, { rowId: it.id, select: true }) },
        { label: "Copy link to this item", icon: "link", action: () => this.s.copyLink(`/i/${it.id}`, "Item link") },
        it.photos.length && { label: "View photos", icon: "image", action: () => window.dispatchEvent(new CustomEvent("viewer:open", { detail: { photos: it.photos, index: 0, title: it.description } })) },
      ]);
    });
  }

  applyFilter() {
    if (!this.grid) return;
    const q = this.filterText.trim().toLowerCase();
    if (!q) return this.grid.clearFilter();
    this.grid.setFilter((r) => q.split(/\s+/).every((t) => [r.path, r.description, r.observation, r.fields, r.quantity_display].join(" ").toLowerCase().includes(t)));
  }
}

// ------------------------------------------------------------ trash
export class TrashView {
  constructor(el, tab, store) { this.el = el; this.tab = tab; this.s = store; }

  async load() {
    try { this.batches = await get("/api/trash"); }
    catch (e) { return toastError(e); }
    this.render();
  }

  render() {
    const admin = this.s.user.is_admin;
    const b = this.batches;
    this.el.innerHTML = `
      <div class="page">
        <div class="page-head">
          <div class="grow"><h1>Trash</h1><p class="muted">Deleted folders, tables and rows. Items are permanently removed automatically after the retention period.</p></div>
          ${admin && b.length ? `<button class="btn btn-danger" data-a="empty">${icon("trash-2")} Empty trash</button>` : ""}
        </div>
        ${b.length ? b.map((x) => `
          <div class="card stack" style="gap:8px">
            <div class="row wrap">
              <strong class="grow">${describe(x)}</strong>
              <span class="small muted" title="${esc(fmtDateTime(x.deleted_at))}">Deleted ${esc(relTime(x.deleted_at))}${x.deleted_by ? " by " + esc(x.deleted_by) : ""}</span>
            </div>
            <div class="small muted">${[
              ...x.folders.map((f) => `${icon("folder")} ${esc([...f.path, f.name].join(" / "))}`),
              ...x.tables.map((t) => `${icon("table-2")} ${esc([...t.path, t.name].join(" / "))}`),
              ...x.items.slice(0, 8).map((i) => `${icon("box")} ${esc(i.description || "(no description)")} <span class="muted">in ${esc(i.path.join(" / "))}</span>`),
            ].join("<br>")}${x.items.length > 8 ? `<br>…and ${x.items.length - 8} more rows` : ""}</div>
            <div class="row">
              ${this.s.user.can_edit ? `<button class="btn btn-sm" data-restore="${x.batch}">${icon("undo-2")} Restore</button>` : ""}
              ${admin ? `<button class="btn btn-sm btn-danger" data-purge="${x.batch}">${icon("x")} Delete permanently</button>` : ""}
            </div>
          </div>`).join("") : `<div class="empty-state">${icon("trash")}<p>Trash is empty.</p></div>`}
      </div>`;
    icons(this.el);
    this.el.onclick = async (e) => {
      const r = e.target.closest("[data-restore]"), p = e.target.closest("[data-purge]"), a = e.target.closest('[data-a="empty"]');
      try {
        if (r) {
          const res = await post(`/api/trash/${r.dataset.restore}/restore`);
          res.warnings.forEach((w) => toast(w, { timeout: 7000 }));
          toast("Restored");
          await this.s.loadTree();
          this.s.refreshViews((k) => k.startsWith("table:") || k.startsWith("folder:"));
        } else if (p || a) {
          const ok = await confirmDialog({ title: a ? "Empty trash?" : "Delete permanently?", danger: true, confirm: "Delete forever",
            message: "This can't be undone. Photos only used by these rows will be deleted from disk." });
          if (!ok) return;
          await del(a ? "/api/trash" : `/api/trash/${p.dataset.purge}`);
          toast("Permanently deleted");
        } else return;
        this.load();
      } catch (err) { toastError(err); }
    };
  }
}

function describe(x) {
  const c = x.counts;
  const parts = [];
  if (c.folders) parts.push(`${c.folders} folder${c.folders > 1 ? "s" : ""}`);
  if (c.tables) parts.push(`${c.tables} table${c.tables > 1 ? "s" : ""}`);
  if (c.items) parts.push(`${c.items} row${c.items > 1 ? "s" : ""}`);
  return parts.join(", ") || "Empty";
}

// ------------------------------------------------------------ full search results
export class SearchView {
  constructor(el, tab, store) { this.el = el; this.tab = tab; this.s = store; }

  async load() {
    const { q, scope = {} } = this.tab;
    this.el.innerHTML = `<div class="empty-state"><span class="spinner"></span></div>`;
    const qs = new URLSearchParams({ q, limit: 200 });
    if (scope.folders?.length) qs.set("folders", scope.folders.join(","));
    if (scope.exclude_folders?.length) qs.set("exclude_folders", scope.exclude_folders.join(","));
    if (scope.tables?.length) qs.set("tables", scope.tables.join(","));
    if (scope.include_archived) qs.set("include_archived", "1");
    try { this.res = await get(`/api/search?${qs}`); }
    catch (e) { return toastError(e); }
    this.render();
  }

  render() {
    const r = this.res.results;
    const items = r.filter((x) => x.type === "item");
    const tables = r.filter((x) => x.type === "table");
    this.el.innerHTML = `
      <div class="page">
        <div><h1>Results for “${esc(this.res.query)}”</h1>
          <p class="muted">${r.length} matches${this.res.semantic_error ? ` · <span class="badge badge-warn" title="${esc(this.res.semantic_error)}">semantic search unavailable</span>` : ""}</p></div>
        ${tables.length ? `<h2>Tables</h2><div class="cards">${tables.map((t) => `
          <div class="card click ${t.active ? "" : "archived"}" data-table="${t.id}">
            <div class="row">${icon("table-2")}<strong class="truncate">${esc(t.title)}</strong></div>
            <div class="small muted">${esc(t.path.join(" / ") || "Top level")} · ${t.item_count} items · updated ${esc(relTime(t.table_updated_at))}</div>
          </div>`).join("")}</div>` : ""}
        ${items.length ? `<h2>Items</h2><div class="list"><table><thead><tr><th></th><th>Item</th><th>Qty</th><th class="hide-sm">Where</th><th class="hide-sm">Added</th><th class="hide-sm">Table updated</th></tr></thead><tbody>
          ${items.map((i) => `<tr class="click ${i.active ? "" : "archived"}" data-table="${i.table_id}" data-row="${i.id}">
            <td style="width:52px">${i.thumb ? `<img src="${i.thumb}" alt="" style="width:40px;height:40px;object-fit:cover;border-radius:6px">` : ""}</td>
            <td><strong>${esc(i.title)}</strong>${i.observation ? `<div class="small muted">${esc(i.observation.slice(0, 140))}</div>` : ""}
              <div class="small muted md-show">${esc(i.path.join(" / "))}</div></td>
            <td>${esc(i.quantity)}</td>
            <td class="hide-sm small">${esc(i.path.join(" / "))}${i.active ? "" : ' <span class="badge badge-warn">Archived</span>'}</td>
            <td class="hide-sm small muted" title="${esc(fmtDateTime(i.created_at))}">${esc(fmtDateTime(i.created_at, { time: false }))}${i.created_by ? "<br>" + esc(i.created_by) : ""}</td>
            <td class="hide-sm small muted">${esc(relTime(i.table_updated_at))}</td></tr>`).join("")}
        </tbody></table></div>` : ""}
        ${!r.length ? `<div class="empty-state">${icon("search-x")}<p>No matches.</p></div>` : ""}
      </div>`;
    icons(this.el);
    this.el.onclick = (e) => {
      const t = e.target.closest("[data-table]");
      if (t) this.s.openTable(Number(t.dataset.table), t.dataset.row ? { rowId: Number(t.dataset.row) } : {});
    };
  }
}
