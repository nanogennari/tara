// Table view: header (path, title, summary, last-updated), toolbar, bulk bar and the Tabulator grid.
import { ageDays, api, confirmDialog, contextMenu, debounce, del, esc, fmtDateTime, get, icon, icons,
  patch, post, promptDialog, put, qtyFormat, relTime, tablePicker, toast, toastError } from "./util.js";
import { applyGridVars, gridDefault, saveTablePrefs, screenSize, setGridDefault, tablePrefs } from "./prefs.js";
import { TYPE_LABELS, columnDialog } from "./columns.js";

const SOON_DAYS = 60;

// ---- Row density & text size presets (values saved per user, per table, per screen size)
const ROW_PRESETS = [["Compact", 28], ["Normal", 36], ["Comfortable", 48], ["Large photos", 80]];
const FONT_PRESETS = [["Small", 12], ["Normal", 13], ["Large", 15], ["Extra large", 17]];
// Columns are sized to their content ("fitData"); long text wraps beyond these maximums.
const MAX_WIDTHS = { description: 460, observation: 420, longtext: 360, other: 280 };
const MAX_WIDTHS_PHONE = { description: 240, observation: 220, longtext: 220, other: 180 };
const isMobile = () => matchMedia("(max-width: 600px)").matches;

export class TableView {
  constructor(el, tab, store) {
    this.el = el;
    this.tab = tab;
    this.s = store;
    this.id = tab.id;
    this.data = null;
    this.grid = null;
    this.filterText = "";
    this.refreshTree = debounce(() => this.s.loadTree(), 700);
    el.classList.add("table-view");
    // Re-measure rows when row height / text size changes (any table's View panel)
    this.onGridView = () => { this.applyView(); this.grid?.redraw(true); };
    window.addEventListener("grid:view", this.onGridView);
    // Rebuild with phone column widths when crossing the mobile breakpoint
    this.mq = matchMedia("(max-width: 600px)");
    this.onBreakpoint = () => this.data && this.render();
    this.mq.addEventListener("change", this.onBreakpoint);
  }

  // ------------------------------------------------------------ loading
  async load({ quiet = false, rowId, highlight, select } = {}) {
    if (!quiet && !this.data) this.el.innerHTML = `<div class="empty-state"><span class="spinner"></span></div>`;
    try {
      this.data = await get(`/api/tables/${this.id}`);
    } catch (e) {
      if (e.status === 404) {
        this.el.innerHTML = `<div class="empty-state">${icon("table-2")}<p>This table no longer exists (it may be in the trash).</p></div>`;
        icons(this.el);
        return;
      }
      return toastError(e);
    }
    this.tab.title = this.data.name;
    this.render();
    if (rowId) (select ? this.highlightRows([rowId]) : this.focusRow(rowId));
    if (highlight?.length) this.highlightRows(highlight);
  }

  /** Select + flash a set of rows (used by the AI assistant). */
  highlightRows(ids) {
    const run = () => {
      this.grid.deselectRow();
      const rows = ids.map((id) => this.grid.getRow(id)).filter(Boolean);
      if (!rows.length) return;
      rows.forEach((r) => r.select());
      this.focusRow(rows[0].getData().id);
      rows.slice(1).forEach((r) => { const el = r.getElement(); el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); });
    };
    this.grid?.initialized ? run() : this.grid?.on("tableBuilt", run);
  }

  get writable() { return !!this.data?.writable; }

  // ---- personal view settings for this table (current screen size)
  get tp() { return tablePrefs(this.id); }
  view() {
    const d = gridDefault(), tp = this.tp;
    return { rowH: tp.rowH ?? d.rowH, font: tp.font ?? d.font, custom: tp.rowH != null || tp.font != null };
  }
  applyView() { applyGridVars(this.el, this.view()); }
  thumbsShown() { const h = this.view().rowH; return h <= 40 ? 3 : h <= 56 ? 2 : 1; }
  isHidden(c) {
    if (c.key === "description") return false;
    const hidden = this.tp.hidden;
    return hidden ? hidden.includes(c.key) : !!c.hidden;
  }
  setHidden(key, hide) {
    const cur = new Set(this.tp.hidden ?? this.data.columns.filter((c) => c.hidden).map((c) => c.key));
    hide ? cur.add(key) : cur.delete(key);
    saveTablePrefs(this.id, { hidden: [...cur] });
    const col = this.data.columns.find((c) => c.key === key);
    const field = col?.type === "builtin" ? key : `custom.${key}`;
    hide ? this.grid.hideColumn(field) : this.grid.showColumn(field);
  }

  render() {
    const d = this.data;
    const scrollTop = this.grid?.rowManager?.element?.scrollTop;
    this.destroy();
    this.applyView();
    const meta = this.s.table(this.id) || d;
    const updatedAt = meta.content_updated_at || d.content_updated_at;
    const updatedBy = meta.content_updated_by || d.content_updated_by;
    const stale = ageDays(updatedAt) > this.s.staleDays;
    const crumbs = d.path.map((name, i) => {
      const f = this.s.folderPath(d.folder_id)[i];
      return `<a href="#" data-folder="${f?.id ?? ""}">${esc(name)}</a>${icon("chevron-right")}`;
    }).join("");

    this.el.innerHTML = `
      <header class="tv-head">
        <nav class="crumbs" aria-label="Location">${icon("folder")}${crumbs || '<span>Top level</span>' + icon("chevron-right")}<span>${esc(d.name)}</span></nav>
        <div class="title-row">
          <h1 data-edit="name" title="${this.writable ? "Click to rename" : ""}">${esc(d.name)}</h1>
          <span class="updated-chip ${stale ? "stale" : ""}" title="Last update: ${esc(fmtDateTime(updatedAt))}${updatedBy ? " by " + esc(updatedBy) : ""}${stale ? "\nNot updated for more than " + this.s.staleDays + " days" : ""}">
            ${icon(stale ? "clock-alert" : "clock")}<span>Updated ${esc(relTime(updatedAt))}${updatedBy ? " · " + esc(updatedBy) : ""}</span>
          </span>
          <span class="badge" data-count>${d.item_count} items</span>
          ${!d.effective_active ? `<span class="badge badge-warn">${icon("archive")} Archived</span>` : ""}
        </div>
        <p class="summary ${d.summary ? "" : "empty"}" data-edit="summary">${esc(d.summary || (this.writable ? "Add a summary…" : ""))}</p>
      </header>
      ${!d.effective_active ? `
        <div class="archived-banner">${icon("archive")}
          <span class="grow">Archived inventory — read-only and hidden from search. Last updated ${esc(fmtDateTime(updatedAt))}.</span>
          ${this.s.user.can_edit && d.active ? `<span class="small">Archived through its folder.</span>` : ""}
          ${this.s.user.can_edit && !d.active ? `<button class="btn btn-sm" data-act="reactivate">${icon("archive-restore")} Reactivate</button>` : ""}
        </div>` : ""}
      <div class="toolbar">
        ${this.writable ? `
          <button class="btn btn-primary" data-act="add-row">${icon("plus")}<span class="label-sm">Add row</span></button>
          <button class="btn btn-ai" data-act="ai">${icon("sparkles")}<span class="label-sm">Add with AI</span></button>
          <span class="sep hide-sm"></span>` : ""}
        <input class="filter" type="search" placeholder="Filter rows…" value="${esc(this.filterText)}" aria-label="Filter rows">
        <span class="grow"></span>
        <button class="btn" data-act="view" title="Row height and text size">${icon("rows-3")}<span class="label-sm">View</span></button>
        <button class="btn" data-act="columns">${icon("columns-3")}<span class="label-sm">Columns</span></button>
        <button class="btn" data-act="export">${icon("download")}<span class="label-sm">Export</span></button>
        <button class="btn btn-icon" data-act="more" aria-label="More table actions">${icon("more-horizontal")}</button>
      </div>
      <div class="row-bulk" hidden></div>
      <div class="grid-wrap"><div class="grid"></div><div class="grid-empty" hidden></div></div>`;
    icons(this.el);
    this.wireHeader();
    this.buildGrid(scrollTop);
  }

  destroy() {
    if (this.grid) { try { this.grid.destroy(); } catch { /* already gone */ } }
    this.grid = null;
  }

  close() {
    this.mq.removeEventListener("change", this.onBreakpoint);
    window.removeEventListener("grid:view", this.onGridView);
    this.destroy();
  }

  onShow() { this.grid?.redraw(); }

  // ------------------------------------------------------------ header & toolbar
  wireHeader() {
    const $ = (sel) => this.el.querySelector(sel);
    this.el.querySelectorAll("[data-folder]").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      if (a.dataset.folder) this.s.openFolder(Number(a.dataset.folder));
    }));
    if (this.writable) {
      $('[data-edit="name"]').addEventListener("click", (e) => this.inlineEdit(e.currentTarget, "name"));
      $('[data-edit="summary"]').addEventListener("click", (e) => this.inlineEdit(e.currentTarget, "summary"));
    }
    $(".filter").addEventListener("input", debounce((e) => { this.filterText = e.target.value; this.applyFilter(); }, 150));
    this.el.querySelector(".toolbar").addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const r = b.getBoundingClientRect();
      ({
        "add-row": () => this.addRow(),
        ai: () => window.dispatchEvent(new CustomEvent("ai:open", { detail: { tableId: this.id } })),
        columns: () => this.columnsMenu(r.left, r.bottom + 4),
        view: () => viewPanel(b, this),
        export: () => contextMenu(r.left, r.bottom + 4, [
          { label: "Excel (.xlsx) with photos", icon: "file-spreadsheet", action: () => { location.href = `/api/export/xlsx?tables=${this.id}`; } },
          { label: "CSV", icon: "file-text", action: () => { location.href = `/api/tables/${this.id}/export.csv`; } },
          { label: "All photos (ZIP)", icon: "images", action: () => import("./tree.js").then((m) => m.downloadPhotos(`tables=${this.id}`)) },
        ]),
        more: () => this.moreMenu(r.right - 200, r.bottom + 4),
      })[b.dataset.act]?.();
    });
    this.el.querySelector('[data-act="reactivate"]')?.addEventListener("click", () => this.setActive(true));
  }

  inlineEdit(node, field) {
    if (node.querySelector("input,textarea")) return;
    const old = this.data[field] || "";
    const input = document.createElement(field === "summary" ? "textarea" : "input");
    input.className = "inline-input";
    input.value = old;
    input.style.width = field === "summary" ? "100%" : Math.max(200, node.offsetWidth + 40) + "px";
    if (field === "summary") input.rows = 2;
    node.replaceChildren(input);
    input.focus();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const val = input.value.trim();
      if (save && val !== old && (field !== "name" || val)) {
        try {
          await patch(`/api/tables/${this.id}`, { [field]: val });
          this.data[field] = val;
          this.tab.title = field === "name" ? val : this.tab.title;
          this.refreshTree();
        } catch (e) { toastError(e); }
      }
      this.load({ quiet: true });
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (field === "name" || e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
      if (e.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
  }

  moreMenu(x, y) {
    const ed = this.s.user.can_edit;
    const d = this.data;
    contextMenu(x, y, [
      { label: "Copy link to this table", icon: "link", action: () => this.s.copyLink(`/t/${this.id}`) },
      this.writable && { label: "AI context & summary…", icon: "bot", action: () => this.editContext() },
      this.writable && { label: "Import rows from XLSX…", icon: "file-spreadsheet", action: () => window.dispatchEvent(new CustomEvent("import:open", { detail: { folderId: d.folder_id } })) },
      { label: "Search in this table", icon: "search", action: () => window.dispatchEvent(new CustomEvent("palette:open", { detail: { tables: [this.id] } })) },
      { label: "Reload", icon: "refresh-cw", action: () => this.load() },
      ed && "-",
      ed && d.active && { label: "Archive (inactive)", icon: "archive", action: () => this.setActive(false) },
      ed && !d.active && { label: "Reactivate", icon: "archive-restore", action: () => this.setActive(true) },
      ed && { label: "Delete table…", icon: "trash-2", danger: true, action: () => this.deleteTable() },
    ]);
  }

  async editContext() {
    const ctx = await promptDialog({
      title: "AI context", multiline: true, value: this.data.context || "", confirm: "Save",
      label: "Extra context the AI receives when adding items to this table (location, what belongs here, naming rules…)",
      placeholder: "e.g. Location: storage room, shelf 2. Medicine for the camp first-aid kit. Always record expiry dates.",
    });
    if (ctx === null) return;
    try { await patch(`/api/tables/${this.id}`, { context: ctx }); this.data.context = ctx; toast("Saved"); }
    catch (e) { toastError(e); }
  }

  async setActive(active) {
    try {
      await post("/api/nodes/bulk", { folders: [], tables: [this.id], action: active ? "reactivate" : "archive" });
      await this.s.loadTree();
      this.load({ quiet: true });
      toast(active ? "Table reactivated" : "Table archived — read-only and hidden from search");
    } catch (e) { toastError(e); }
  }

  async deleteTable() {
    const { bulk } = await import("./tree.js");
    await bulk(this.s, "delete", { folders: [], tables: [this.id] });
  }

  // ------------------------------------------------------------ columns
  columnDefs() {
    const w = this.writable;
    const cols = [];
    const mobile = isMobile();
    let prio = 3;
    const tp = this.tp;
    const columns = [...this.data.columns];
    if (tp.order) {
      const pos = (k) => { const i = tp.order.indexOf(k); return i < 0 ? 1e6 : i; };
      columns.sort((a, b) => pos(a.key) - pos(b.key));
    }
    for (const c of columns) {
      // Default widths flex to fill the screen; widths the user dragged are kept as-is.
      const maxes = mobile ? MAX_WIDTHS_PHONE : MAX_WIDTHS;
      const maxWidth = c.key === "photos" ? undefined
        : maxes[c.key] ?? (c.type === "longtext" ? maxes.longtext : maxes.other);
      const base = {
        title: c.label || c.key, field: c.key, visible: !this.isHidden(c),
        // Widths the user dragged win; otherwise as wide as the content, up to a maximum (then wrap)
        ...(tp.widths?.[c.key] ? { width: tp.widths[c.key] } : { maxWidth }),
        minWidth: c.key === "description" ? 140 : 60, headerMenu: () => this.headerMenu(c), headerSort: c.key !== "photos",
        responsive: c.key === "description" ? 0 : c.key === "quantity" ? 1 : c.key === "photos" ? 2 : prio++,
      };
      if (c.key === "description") {
        cols.push({ ...base, editor: w && "textarea", formatter: (cell) => {
          const r = cell.getRow().getData();
          return esc(cell.getValue() || "") + (r.ai_generated ? '<span class="ai-badge" title="Added by AI — edit to confirm">AI</span>' : "");
        }, editorParams: { verticalNavigation: "table", shiftEnterSubmit: true } });
      } else if (c.key === "quantity") {
        cols.push({ ...base, editor: w && qtyEditor, cssClass: "cell-qty", formatter: (cell) => {
          const q = cell.getValue();
          const s = qtyFormat(q);
          return q?.estimated ? `<span class="est" title="Estimated">${esc(s)}</span>` : esc(s);
        }, sorter: (a, b) => (a?.value ?? -Infinity) - (b?.value ?? -Infinity) });
      } else if (c.key === "observation") {
        cols.push({ ...base, editor: w && "textarea", editorParams: { shiftEnterSubmit: true } });
      } else if (c.key === "photos") {
        cols.push({ ...base, formatter: (cell, _p, onRendered) => this.photoCell(cell, onRendered), cssClass: "cell-photos" });
      } else {
        cols.push({ ...base, field: `custom.${c.key}`, ...customColumn(c, w) });
      }
    }
    cols.push({
      title: "Added", field: "created_at", visible: !!tp.showCreated, width: tp.widths?.created_at || 200, headerSort: true, responsive: 98,
      formatter: (cell) => {
        const r = cell.getRow().getData();
        return `<span class="updated-cell" title="Added ${esc(fmtDateTime(r.created_at))}${r.created_by ? " by " + esc(r.created_by) : ""}">${esc(fmtDateTime(r.created_at))}${r.created_by ? " · " + esc(r.created_by) : ""}</span>`;
      },
    });
    cols.push({
      title: "Updated", field: "updated_at", visible: !!tp.showUpdated, width: tp.widths?.updated_at || 150, headerSort: true, responsive: 99,
      formatter: (cell) => {
        const r = cell.getRow().getData();
        return `<span class="updated-cell" title="${esc(fmtDateTime(r.updated_at))}">${esc(relTime(r.updated_at))}${r.updated_by ? " · " + esc(r.updated_by) : ""}</span>`;
      },
    });
    return cols;
  }

  headerMenu(c) {
    if (!this.writable) return c.key === "description" ? [] : [{ label: "Hide column", action: () => this.setHidden(c.key, true) }];
    const builtin = c.type === "builtin";
    return [
      { label: "Rename…", action: () => this.renameColumn(c) },
      !builtin && { label: `Type: ${TYPE_LABELS[c.type]} — change…`, action: () => this.columnDialog(c) },
      c.key !== "description" && { label: "Hide column", action: () => this.setHidden(c.key, true) },
      { separator: true },
      { label: "Add column…", action: () => this.columnDialog() },
      !builtin && { separator: true },
      !builtin && { label: "<span style='color:var(--danger)'>Delete column…</span>", action: () => this.deleteColumn(c) },
    ].filter(Boolean);
  }

  columnsMenu(x, y) {
    const tp = this.tp;
    const items = this.data.columns.map((c) => ({
      label: `${this.isHidden(c) ? "☐" : "☑"}  ${c.label || c.key}`, disabled: c.key === "description",
      action: () => this.setHidden(c.key, !this.isHidden(c)),
    }));
    const toggle = (pref, field) => () => {
      const on = !this.tp[pref];
      saveTablePrefs(this.id, { [pref]: on || null });
      on ? this.grid.showColumn(field) : this.grid.hideColumn(field);
    };
    items.push({ label: `${tp.showCreated ? "☑" : "☐"}  Added (who / when)`, action: toggle("showCreated", "created_at") });
    items.push({ label: `${tp.showUpdated ? "☑" : "☐"}  Updated (who / when)`, action: toggle("showUpdated", "updated_at") });
    if (tp.hidden || tp.widths || tp.order) {
      items.push("-", { label: "Reset my columns (shown, widths, order)", icon: "rotate-ccw", action: () => {
        saveTablePrefs(this.id, { hidden: null, widths: null, order: null });
        this.render();
      } });
    }
    if (this.writable) items.push("-", { label: "Add column…", icon: "plus", action: () => this.columnDialog() });
    contextMenu(x, y, items);
  }

  async renameColumn(c) {
    const label = await promptDialog({ title: "Rename column", label: "Column name", value: c.label });
    if (label && label !== c.label) this.updateColumn(c.key, { label });
  }

  async updateColumn(key, data) {
    try {
      await patch(`/api/tables/${this.id}/columns/${encodeURIComponent(key)}`, data);
      this.load({ quiet: true });
    } catch (e) { toastError(e); }
  }

  async deleteColumn(c) {
    const ok = await confirmDialog({ title: `Delete column “${c.label}”?`, danger: true, confirm: "Delete column",
      message: "The column and its values in every row of this table will be removed. This can't be undone." });
    if (!ok) return;
    try { await del(`/api/tables/${this.id}/columns/${encodeURIComponent(c.key)}`); this.load({ quiet: true }); }
    catch (e) { toastError(e); }
  }

  async columnDialog(existing) {
    if (await columnDialog(this.id, existing)) this.load({ quiet: true });
  }

  // ------------------------------------------------------------ grid
  buildGrid(scrollTop) {
    const host = this.el.querySelector(".grid");
    const w = this.writable;
    const mobile = isMobile();
    this.grid = new Tabulator(host, {
      data: this.data.items,
      index: "id",
      height: "100%",
      layout: "fitData",
      responsiveLayout: false,
      placeholder: "",
      movableRows: w && !mobile,
      movableColumns: w && !mobile,
      selectableRows: "highlight",
      selectableRowsRangeMode: "click",
      // One pinned column: drag grip (reorder) + selection checkbox
      rowHeader: {
        titleFormatter: "rowSelection", headerSort: false, resizable: false, frozen: true,
        width: w && !mobile ? 52 : 36, hozAlign: "center", headerHozAlign: "center",
        rowHandle: w && !mobile, cssClass: "row-head",
        formatter: (cell) => this.rowHeadCell(cell, w && !mobile),
      },
      columns: [
        ...this.columnDefs(),
      ],
      editTriggerEvent: "dblclick",
      columnDefaults: { tooltip: false, vertAlign: "top" },
      rowFormatter: (row) => { row.getElement().dataset.id = row.getData().id; },
    });
    const g = this.grid;
    g.on("tableBuilt", () => {
      this.applyFilter();
      this.updateEmpty();
      if (scrollTop) g.rowManager.element.scrollTop = scrollTop;
    });
    g.on("cellClick", (e, cell) => {
      // Single click edits text cells (spreadsheet feel) once a row isn't being selected
      if (!w || e.shiftKey || e.ctrlKey || e.metaKey) return;
      const def = cell.getColumn().getDefinition();
      if (def.editor && !cell.getElement().classList.contains("tabulator-editing")) cell.edit(true);
    });
    g.on("cellEdited", (cell) => this.saveCell(cell));
    // Row menu: right-click anywhere on a row (any cell, photos, checkbox column)
    host.addEventListener("contextmenu", (e) => {
      const rowEl = e.target.closest(".tabulator-row");
      if (!rowEl) return;
      // Keep the browser's menu while typing in a cell editor (copy/paste/spellcheck)
      if (e.target.closest(".tabulator-editing") && e.target.matches("input, textarea")) return;
      const row = this.grid.getRow(Number(rowEl.dataset.id));
      if (!row) return;
      e.preventDefault();
      this.rowMenu(row, e.clientX, e.clientY);
    });
    g.on("rowMoved", () => this.saveOrder());
    g.on("columnResized", debounce((col) => {
      const key = colKey(col.getField());
      if (key) saveTablePrefs(this.id, { widths: { ...(this.tp.widths || {}), [key]: Math.round(col.getWidth()) } });
    }, 400));
    g.on("columnMoved", () => {
      const keys = this.grid.getColumns().map((c) => colKey(c.getField())).filter((k) => k && this.data.columns.some((c) => c.key === k));
      saveTablePrefs(this.id, { order: keys });
    });
    g.on("rowSelectionChanged", (_d, rows) => this.renderBulk(rows));
    const sync = (row, on) => { const cb = row.getElement().querySelector(".row-check"); if (cb) cb.checked = on; };
    g.on("rowSelected", (row) => sync(row, true));
    g.on("rowDeselected", (row) => sync(row, false));

    this.el.addEventListener("keydown", (e) => {
      if (!w || !["Delete", "Backspace"].includes(e.key)) return;
      if (e.target.closest(".tabulator-editing, input, textarea, select")) return;
      const sel = this.grid?.getSelectedRows() || [];
      if (sel.length) { e.preventDefault(); this.bulkAction("delete"); }
    });
  }

  rowHeadCell(cell, movable) {
    const row = cell.getRow();
    const wrap = document.createElement("div");
    wrap.className = "row-head-inner";
    if (movable) wrap.insertAdjacentHTML("beforeend", '<span class="grip" title="Drag to reorder" aria-hidden="true"></span>');
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "row-check";
    cb.checked = row.isSelected();
    cb.setAttribute("aria-label", "Select row");
    cb.addEventListener("mousedown", (e) => e.stopPropagation()); // never start a drag from the checkbox
    cb.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault(); // state is driven by Tabulator's selection (synced in rowSelected/rowDeselected)
      // ...but the browser reverts a prevented click's checkbox state after this handler, undoing that sync
      setTimeout(() => { cb.checked = row.isSelected(); });
      const rows = this.grid.getRows("active");
      if (e.shiftKey && this.lastChecked) {
        const a = rows.indexOf(this.lastChecked), b = rows.indexOf(row);
        if (a >= 0 && b >= 0) {
          const select = !row.isSelected();
          rows.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((r) => (select ? r.select() : r.deselect()));
          this.lastChecked = row;
          return;
        }
      }
      row.toggleSelect();
      this.lastChecked = row;
    });
    wrap.appendChild(cb);
    return wrap;
  }

  rowMenu(row, x, y) {
    const w = this.writable;
    const it = row.getData();
    // Acting on a row that is part of a multi-selection applies to the whole selection
    const sel = this.grid.getSelectedRows();
    const targets = row.isSelected() && sel.length > 1 ? sel : [row];
    const n = targets.length;
    const many = n > 1 ? ` (${n} rows)` : "";
    const allEst = targets.every((r) => r.getData().quantity?.estimated);
    const run = (action) => { this.grid.deselectRow(); targets.forEach((r) => r.select()); this.bulkAction(action); };
    contextMenu(x, y, [
      n === 1 && { label: "Copy link to this item", icon: "link", action: () => this.s.copyLink(`/i/${it.id}`, "Item link") },
      n === 1 && it.photos.length && { label: "View photos", icon: "image", action: () => this.openViewer(it.id, 0) },
      w && n === 1 && { label: "Add photos…", icon: "image-plus", action: () => this.pickPhotos(it.id) },
      w && n === 1 && { label: "Insert row below", icon: "plus", action: () => this.addRow(it.id) },
      w && "-",
      w && (allEst
        ? { label: `Mark as exact${many}`, icon: "equal", action: () => run("clear_estimated") }
        : { label: `Mark as estimate (~)${many}`, icon: "equal-approximately", action: () => run("set_estimated") }),
      w && { label: `Move to table…${many}`, icon: "arrow-right-left", action: () => run("move") },
      w && "-",
      w && { label: `Delete${n > 1 ? ` ${n} rows` : " row"}`, icon: "trash-2", danger: true, action: () => run("delete") },
    ]);
  }

  applyFilter() {
    if (!this.grid) return;
    const q = this.filterText.trim().toLowerCase();
    if (!q) return this.grid.clearFilter();
    this.grid.setFilter((row) => {
      const hay = [row.description, row.observation, qtyFormat(row.quantity), ...Object.values(row.custom || {})].join(" ").toLowerCase();
      return q.split(/\s+/).every((t) => hay.includes(t));
    });
  }

  updateEmpty() {
    const box = this.el.querySelector(".grid-empty");
    if (!box) return;
    const empty = !this.data.items.length;
    box.hidden = !empty;
    if (empty) {
      box.innerHTML = `<div class="empty-state">${icon("package-open")}<p>No items yet.</p>${this.writable ? `
        <div class="row"><button class="btn" data-e="row">${icon("plus")} Add a row</button>
        <button class="btn btn-ai" data-e="ai">${icon("sparkles")} Add with AI from photos</button></div>` : ""}</div>`;
      icons(box);
      box.querySelector('[data-e="row"]')?.addEventListener("click", () => this.addRow());
      box.querySelector('[data-e="ai"]')?.addEventListener("click", () => window.dispatchEvent(new CustomEvent("ai:open", { detail: { tableId: this.id } })));
    }
    const c = this.el.querySelector("[data-count]");
    if (c) c.textContent = `${this.data.items.length} items`;
  }

  async saveCell(cell) {
    const field = cell.getField();
    const row = cell.getRow();
    let body;
    if (field.startsWith("custom.")) body = { custom: { [field.slice(7)]: cell.getValue() } };
    else body = { [field]: cell.getValue() };
    try {
      const item = await patch(`/api/items/${row.getData().id}`, body);
      await row.update(item);
      row.reformat();
      const i = this.data.items.findIndex((x) => x.id === item.id);
      if (i >= 0) this.data.items[i] = item;
      if (field.startsWith("custom.") && cell.getValue() && item.custom[field.slice(7)] == null) toast("That value isn't valid for this column", { error: true });
      this.refreshTree();
      this.bumpChip();
    } catch (e) {
      toastError(e);
      cell.restoreOldValue();
    }
  }

  bumpChip() {
    const chip = this.el.querySelector(".updated-chip");
    if (!chip) return;
    chip.classList.remove("stale");
    chip.querySelector("span").textContent = `Updated just now · ${this.s.user.name}`;
    chip.title = `Last update: ${fmtDateTime(new Date().toISOString())} by ${this.s.user.name}`;
  }

  async saveOrder() {
    const ids = this.grid.getRows().map((r) => r.getData().id);
    try { await put(`/api/tables/${this.id}/items/order`, { ids }); this.refreshTree(); this.bumpChip(); }
    catch (e) { toastError(e); this.load({ quiet: true }); }
  }

  async addRow(afterId) {
    try {
      const item = await post(`/api/tables/${this.id}/items`, { description: "", after_id: afterId });
      this.data.items.push(item);
      const row = afterId ? await this.grid.addRow(item, false, afterId) : await this.grid.addRow(item);
      this.updateEmpty();
      this.refreshTree();
      this.bumpChip();
      await this.grid.scrollToRow(row, "nearest", false);
      row.getCell("description")?.edit(true);
    } catch (e) { toastError(e); }
  }

  focusRow(id) {
    if (!this.grid) return;
    const run = () => {
      const row = this.grid.getRow(id);
      if (!row) return;
      this.grid.scrollToRow(row, "center", false).then(() => {
        const el = row.getElement();
        el.classList.remove("flash");
        void el.offsetWidth;
        el.classList.add("flash");
      });
    };
    this.grid.initialized ? run() : this.grid.on("tableBuilt", run);
  }

  // ------------------------------------------------------------ photos
  photoCell(cell, onRendered) {
    const item = cell.getRow().getData();
    const wrap = document.createElement("div");
    wrap.className = "thumbs";
    const max = this.thumbsShown();
    const shown = item.photos.slice(0, max);
    wrap.innerHTML = shown.map((p, i) => `<img src="${p.thumb}" alt="Photo ${i + 1}" loading="lazy" data-i="${i}">`).join("")
      + (item.photos.length > max ? `<span class="more-n">+${item.photos.length - max}</span>` : "")
      + (this.writable ? `<button class="add-photo" title="Add photos (or drop files here)" aria-label="Add photos">${icon("image-plus")}</button>` : "");
    wrap.addEventListener("click", (e) => {
      e.stopPropagation();
      const img = e.target.closest("img");
      if (img) return this.openViewer(item.id, Number(img.dataset.i));
      if (e.target.closest(".more-n")) return this.openViewer(item.id, max);
      if (e.target.closest(".add-photo")) this.pickPhotos(item.id);
    });
    onRendered(() => {
      icons(wrap);
      if (!this.writable) return;
      const cellEl = cell.getElement();
      cellEl.ondragover = (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); cellEl.classList.add("drop-hover"); } };
      cellEl.ondragleave = () => cellEl.classList.remove("drop-hover");
      cellEl.ondrop = (e) => {
        e.preventDefault();
        cellEl.classList.remove("drop-hover");
        const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
        if (files.length) this.uploadPhotos(item.id, files);
      };
    });
    return wrap;
  }

  pickPhotos(itemId) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = () => input.files.length && this.uploadPhotos(itemId, [...input.files]);
    input.click();
  }

  async uploadPhotos(itemId, files) {
    const { resizeImage } = await import("./util.js");
    const fd = new FormData();
    for (const f of files) fd.append("files", await resizeImage(f, 2400, 0.9));
    try {
      toast(`Uploading ${files.length} photo${files.length > 1 ? "s" : ""}…`, { timeout: 1500 });
      await api("POST", `/api/items/${itemId}/photos`, fd);
      await this.reloadRow(itemId);
      this.refreshTree();
      this.bumpChip();
    } catch (e) { toastError(e); }
  }

  async reloadRow(itemId) {
    const data = await get(`/api/tables/${this.id}`);
    this.data = { ...this.data, ...data };
    const item = data.items.find((i) => i.id === itemId);
    if (item) { const row = this.grid.getRow(itemId); await row?.update(item); row?.reformat(); }
  }

  openViewer(itemId, index) {
    const item = this.data.items.find((i) => i.id === itemId) || this.grid.getRow(itemId)?.getData();
    if (!item?.photos.length) return;
    window.dispatchEvent(new CustomEvent("viewer:open", { detail: {
      photos: item.photos, index, title: item.description, writable: this.writable,
      onChange: () => this.reloadRow(itemId).then(() => { this.refreshTree(); this.bumpChip(); }),
    } }));
  }

  // ------------------------------------------------------------ selection / bulk
  renderBulk(rows) {
    window.dispatchEvent(new CustomEvent("screen:changed"));
    const bar = this.el.querySelector(".row-bulk");
    if (!bar) return;
    bar.hidden = !rows.length;
    if (!rows.length) return;
    bar.innerHTML = `<span>${rows.length} selected</span>
      ${this.writable ? `
      <button class="btn btn-sm btn-danger" data-b="delete">${icon("trash-2")} Delete</button>
      <button class="btn btn-sm" data-b="move">${icon("arrow-right-left")} Move to table…</button>
      <button class="btn btn-sm" data-b="set_estimated">${icon("equal-approximately")} Mark estimated</button>
      <button class="btn btn-sm" data-b="clear_estimated">Mark exact</button>
      ${rows.some((r) => r.getData().ai_generated) ? `<button class="btn btn-sm" data-b="clear_ai_flag">${icon("check")} Confirm AI rows</button>` : ""}` : ""}
      <span class="grow"></span>
      <button class="btn btn-sm btn-ghost" data-b="clear">${icon("x")} Clear</button>`;
    icons(bar);
    bar.onclick = (e) => {
      const b = e.target.closest("[data-b]");
      if (!b) return;
      if (b.dataset.b === "clear") return this.grid.deselectRow();
      this.bulkAction(b.dataset.b);
    };
  }

  async bulkAction(action) {
    const ids = this.grid.getSelectedData().map((r) => r.id);
    if (!ids.length) return;
    try {
      if (action === "delete") {
        const photos = this.grid.getSelectedData().reduce((n, r) => n + r.photos.length, 0);
        const ok = ids.length === 1 || await confirmDialog({ title: `Delete ${ids.length} rows?`, danger: true, confirm: "Delete",
          message: `${ids.length} rows${photos ? ` and ${photos} photos` : ""} will be moved to the trash.` });
        if (!ok) return;
        const res = await post("/api/items/bulk", { ids, action: "delete" });
        toast(`Deleted ${res.deleted} row${res.deleted > 1 ? "s" : ""}`, { action: "Undo", timeout: 10000, onAction: async () => {
          try { await post(`/api/trash/${res.batch}/restore`); this.load({ quiet: true }); this.refreshTree(); }
          catch (e) { toastError(e); }
        } });
      } else if (action === "move") {
        const dest = await tablePicker({ title: `Move ${ids.length} rows`, tables: this.s.tables, folders: this.s.folders, exclude: this.id });
        if (!dest) return;
        await post("/api/items/bulk", { ids, action: "move", table_id: dest });
        toast(`Moved ${ids.length} rows`, { action: "Open table", onAction: () => this.s.openTable(dest) });
        this.s.refreshViews((k) => k === `table:${dest}`);
      } else {
        await post("/api/items/bulk", { ids, action });
      }
      await this.load({ quiet: true });
      this.refreshTree();
    } catch (e) { toastError(e); }
  }
}

// ------------------------------------------------------------ View panel (density / text size)

let openPanel = null;
function viewPanel(anchor, tv) {
  if (openPanel) { openPanel.remove(); openPanel = null; return; }
  const p = document.createElement("div");
  p.className = "view-panel";
  p.setAttribute("role", "dialog");
  p.setAttribute("aria-label", "Table view settings");
  const sizeLabel = screenSize() === "small" ? "phone layout" : "large screens";
  const apply = (patch) => { saveTablePrefs(tv.id, patch); tv.applyView(); tv.grid?.redraw(true); };
  const render = () => {
    const v = tv.view();
    p.innerHTML = `
      <h3>Row height</h3>
      <div class="seg">${ROW_PRESETS.map(([l, x]) => `<button data-row="${x}" class="${v.rowH === x ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
      <div class="row small"><input type="range" min="26" max="160" step="2" value="${v.rowH}" aria-label="Row height">
        <span class="mono" style="width:44px;text-align:right">${v.rowH}px</span></div>
      <h3>Text size</h3>
      <div class="seg">${FONT_PRESETS.map(([l, x]) => `<button data-font="${x}" class="${v.font === x ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
      <div class="small muted">${v.custom ? `Custom for this table (${sizeLabel}).` : `Using your default for ${sizeLabel}.`}</div>
      <div class="row wrap" style="gap:6px">
        <button class="btn btn-sm" data-act="global" title="Use this row height and text size for every table on ${sizeLabel}">Apply to all tables</button>
        ${v.custom ? '<button class="btn btn-sm btn-ghost" data-act="reset">Reset to my default</button>' : ""}
      </div>`;
    p.querySelector("input[type=range]").addEventListener("input", (e) => {
      apply({ rowH: Number(e.target.value) });
      p.querySelector(".mono").textContent = `${e.target.value}px`;
      p.querySelectorAll("[data-row]").forEach((b) => b.classList.toggle("on", b.dataset.row === e.target.value));
    });
  };
  p.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.row) apply({ rowH: Number(b.dataset.row) });
    if (b.dataset.font) apply({ font: Number(b.dataset.font) });
    if (b.dataset.act === "reset") apply({ rowH: null, font: null });
    if (b.dataset.act === "global") {
      const { rowH, font } = tv.view();
      try { await setGridDefault({ rowH, font }, true); toast(`Row height and text size applied to all tables (${sizeLabel})`); }
      catch (err) { toastError(err); }
    }
    render();
  });
  render();
  document.body.appendChild(p);
  const r = anchor.getBoundingClientRect();
  p.style.top = `${r.bottom + 6}px`;
  p.style.left = `${Math.max(8, Math.min(r.right - 300, innerWidth - 308))}px`;
  openPanel = p;
  const close = (e) => {
    if (e.type === "keydown" ? e.key !== "Escape" : (p.contains(e.target) || anchor.contains(e.target))) return;
    p.remove(); openPanel = null;
    document.removeEventListener("mousedown", close, true);
    document.removeEventListener("keydown", close, true);
  };
  setTimeout(() => { document.addEventListener("mousedown", close, true); document.addEventListener("keydown", close, true); });
}

// ------------------------------------------------------------ column helpers

function colKey(field) {
  if (!field) return null;
  return field.startsWith("custom.") ? field.slice(7) : field;
}

function customColumn(c, writable) {
  const t = c.type;
  if (t === "number") return { editor: writable && "number", hozAlign: "right", sorter: "number" };
  if (t === "checkbox") return { editor: writable && "tickCross", formatter: "tickCross", formatterParams: { allowEmpty: true }, hozAlign: "center", width: c.width || 90 };
  if (t === "select") return { editor: writable && "list", editorParams: { values: ["", ...(c.options || [])], autocomplete: true, allowEmpty: true, listOnEmpty: true, freetext: false } };
  if (t === "longtext") return { editor: writable && "textarea" };
  if (t === "url") return { editor: writable && "input", formatter: (cell) => {
    const v = cell.getValue();
    if (!v) return "";
    const href = /^https?:\/\//i.test(v) ? v : "https://" + v;
    return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${esc(v)}</a>`;
  } };
  if (t === "date") return {
    editor: writable && "input", editorParams: { elementAttributes: { placeholder: "YYYY-MM-DD or YYYY-MM" } },
    formatter: (cell) => {
      const v = cell.getValue();
      if (!v) return "";
      const d = new Date(v.length === 7 ? v + "-28" : v);
      const days = (d - Date.now()) / 86400000;
      cell.getElement().classList.remove("cell-date", "expired", "soon");
      if (!isNaN(days)) {
        cell.getElement().classList.add("cell-date");
        if (days < 0) { cell.getElement().classList.add("expired"); return `<span title="Expired">${esc(v)} ⚠</span>`; }
        if (days < SOON_DAYS) cell.getElement().classList.add("soon");
      }
      return esc(v);
    },
  };
  return { editor: writable && "input" };
}

/** Quantity editor: one quick input ("~75", "3 rolls", "1.2 kg", "1 + 1"), parsed by the server. */
function qtyEditor(cell, onRendered, success, cancel) {
  const input = document.createElement("input");
  const old = qtyFormat(cell.getValue());
  input.value = old;
  input.placeholder = "e.g. 12, ~75, 3 rolls, 1.2 kg";
  input.title = "Prefix with ~ for an estimate. Units: pcs, g, kg, mL, L, cm, m, packs, boxes, rolls, bottles…";
  input.style.cssText = "width:100%;height:100%;box-sizing:border-box";
  onRendered(() => { input.focus(); input.select(); });
  const commit = () => (input.value.trim() === old ? cancel() : success(input.value.trim()));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(); }
    if (e.key === "Escape") cancel();
  });
  input.addEventListener("blur", commit);
  return input;
}
