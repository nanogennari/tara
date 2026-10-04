// App shell: global store, tab/view manager, keyboard shortcuts, Alpine component registration.
import { fmtDateTime, get, icons, post, promptDialog, qtyFormat, toastError, toast } from "./util.js";
import { renderTree } from "./tree.js";
import { TableView } from "./table-view.js";
import { FolderView, TrashView, SearchView } from "./views.js";
import { paletteComponent } from "./palette.js";
import { aiWizardComponent } from "./ai-wizard.js";
import { viewerComponent } from "./viewer.js";
import { importComponent } from "./import.js";
import { chatComponent } from "./chat.js";
import { guidedComponent } from "./guided.js";

const LS = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
};

const views = new Map(); // tab key -> view instance (kept alive while the tab is open)

function createStore(boot) {
  return {
    user: boot.user,
    staleDays: boot.staleDays,
    folders: [],
    tables: [],
    loaded: false,
    expanded: new Set(LS.get("tree.expanded", [])),
    showArchived: LS.get("tree.showArchived", true),
    sel: { folders: new Set(), tables: new Set(), anchor: null },
    selCount: 0,
    tabs: LS.get("tabs.open", []),
    activeKey: LS.get("tabs.active", null),
    sidebarOpen: false,

    // ---------------------------------------------------- tree data
    async loadTree() {
      try {
        const data = await get("/api/tree?include_archived=1");
        this.folders = data.folders;
        this.tables = data.tables;
        this.loaded = true;
        this.pruneTabs();
        this.redrawTree();
        this.refreshTabTitles();
      } catch (e) { toastError(e); }
    },
    redrawTree() { renderTree(document.getElementById("tree"), this); },
    folder(id) { return this.folders.find((f) => f.id === id); },
    table(id) { return this.tables.find((t) => t.id === id); },
    folderPath(fid) {
      const out = [];
      let f = this.folder(fid);
      while (f) { out.unshift(f); f = this.folder(f.parent_id); }
      return out;
    },
    toggleExpanded(fid, force) {
      const open = force ?? !this.expanded.has(fid);
      open ? this.expanded.add(fid) : this.expanded.delete(fid);
      LS.set("tree.expanded", [...this.expanded]);
      this.redrawTree();
    },
    reveal(fid) {
      this.folderPath(fid).forEach((f) => this.expanded.add(f.id));
      LS.set("tree.expanded", [...this.expanded]);
      this.redrawTree();
    },
    setShowArchived(v) { this.showArchived = v; LS.set("tree.showArchived", v); this.redrawTree(); },
    clearSelection() { this.sel.folders.clear(); this.sel.tables.clear(); this.sel.anchor = null; this.selCount = 0; this.redrawTree(); },
    updateSelCount() {
      this.selCount = this.sel.folders.size + this.sel.tables.size;
      window.dispatchEvent(new CustomEvent("screen:changed"));
    },

    // ---------------------------------------------------- creation
    async newFolder(parentId = null) {
      const name = await promptDialog({ title: "New folder", label: "Folder name", confirm: "Create" });
      if (!name) return;
      try {
        await post("/api/folders", { name, parent_id: parentId });
        if (parentId) this.expanded.add(parentId);
        await this.loadTree();
      } catch (e) { toastError(e); }
    },
    async newTable(folderId = null) {
      const name = await promptDialog({ title: "New table", label: "Table name (e.g. “Box 08: Games”)", confirm: "Create" });
      if (!name) return;
      try {
        const t = await post("/api/tables", { name, folder_id: folderId });
        if (folderId) this.reveal(folderId);
        await this.loadTree();
        this.openTable(t.id);
      } catch (e) { toastError(e); }
    },

    // ---------------------------------------------------- tabs
    openTab(tab, opts = {}) {
      const existing = this.tabs.find((t) => t.key === tab.key);
      if (!existing) this.tabs.push(tab);
      else Object.assign(existing, tab);
      this.activate(tab.key, opts);
      this.sidebarOpen = false;
    },
    openTable(id, opts = {}) {
      const t = this.table(id);
      this.openTab({ key: `table:${id}`, type: "table", id, title: t?.name || "Table" }, opts);
      if (t?.folder_id) this.reveal(t.folder_id);
    },
    openFolder(id) {
      const f = this.folder(id);
      this.openTab({ key: `folder:${id}`, type: "folder", id, title: f?.name || "Folder" });
      this.reveal(id);
    },
    openTrash() { this.openTab({ key: "trash", type: "trash", title: "Trash" }); },
    openSearch(q, scope) { this.openTab({ key: "search", type: "search", title: `Search: ${q}`, q, scope }, { refresh: true }); },
    activate(key, opts = {}) {
      this.activeKey = key;
      this.persistTabs();
      this.showView(key, opts);
      this.redrawTree();
    },
    closeTab(key) {
      const i = this.tabs.findIndex((t) => t.key === key);
      if (i < 0) return;
      this.tabs.splice(i, 1);
      const v = views.get(key);
      (v?.close || v?.destroy)?.call(v);
      v?.el.remove();
      views.delete(key);
      if (this.activeKey === key) {
        const next = this.tabs[i] || this.tabs[i - 1];
        this.activeKey = next?.key || null;
        if (next) this.showView(next.key);
      }
      this.persistTabs();
      this.redrawTree();
    },
    persistTabs() {
      LS.set("tabs.open", this.tabs.filter((t) => t.type !== "search"));
      LS.set("tabs.active", this.activeKey);
    },
    reorderTabs(keys) {
      this.tabs = keys.map((k) => this.tabs.find((t) => t.key === k)).filter(Boolean);
      this.persistTabs();
    },
    pruneTabs() {
      // Drop tabs whose table/folder no longer exists (deleted elsewhere)
      for (const t of [...this.tabs]) {
        if ((t.type === "table" && !this.table(t.id)) || (t.type === "folder" && !this.folder(t.id))) this.closeTab(t.key);
      }
    },
    refreshTabTitles() {
      for (const tab of this.tabs) {
        if (tab.type === "table") tab.title = this.table(tab.id)?.name || tab.title;
        if (tab.type === "folder") tab.title = this.folder(tab.id)?.name || tab.title;
      }
    },
    tabInfo(tab) {
      if (tab.type !== "table") return { archived: false, title: tab.title };
      const t = this.table(tab.id);
      return {
        archived: t && !t.effective_active,
        title: t ? `${t.name}\nUpdated ${fmtDateTime(t.content_updated_at)}${t.content_updated_by ? " by " + t.content_updated_by : ""}` : tab.title,
      };
    },
    tabIcon(tab) {
      return { table: "table-2", folder: "folder", trash: "trash-2", search: "search" }[tab.type];
    },
    showView(key, opts = {}) {
      const host = document.getElementById("views");
      const tab = this.tabs.find((t) => t.key === key);
      if (!tab || !host) return;
      let v = views.get(key);
      if (!v) {
        const el = document.createElement("div");
        el.className = "view";
        host.appendChild(el);
        const Cls = { table: TableView, folder: FolderView, trash: TrashView, search: SearchView }[tab.type];
        v = new Cls(el, tab, this);
        views.set(key, v);
        v.load(opts);
      } else if (opts.refresh) {
        Object.assign(v.tab, tab);
        v.load(opts);
      } else if (opts.rowId) {
        v.focusRow?.(opts.rowId);
      } else if (opts.highlight?.length) {
        v.highlightRows?.(opts.highlight);
      }
      for (const [k, other] of views) other.el.hidden = k !== key;
      v.onShow?.();
      window.dispatchEvent(new CustomEvent("screen:changed"));
    },
    view(key) { return views.get(key); },
    /** What the user sees, for the AI assistant. */
    screenContext() {
      const tab = this.tabs.find((t) => t.key === this.activeKey);
      const ctx = { active: tab ? { type: tab.type, id: tab.id, name: tab.title } : null };
      const v = tab && views.get(tab.key);
      if (tab?.type === "table" && v?.data) {
        ctx.active = { type: "table", id: v.id, name: v.data.name, path: v.data.path.join(" / "),
          item_count: v.data.items.length, archived: !v.data.effective_active };
        ctx.filter = v.filterText || "";
        ctx.selected_rows = (v.grid?.getSelectedData() || []).slice(0, 40)
          .map((r) => ({ id: r.id, description: r.description, quantity: qtyFormat(r.quantity) }));
      } else if (tab?.type === "folder") {
        ctx.active.path = this.folderPath(this.folder(tab.id)?.parent_id).map((f) => f.name).join(" / ");
      }
      ctx.selected_nodes = [
        ...[...this.sel.folders].map((id) => ({ type: "folder", id, name: this.folder(id)?.name })),
        ...[...this.sel.tables].map((id) => ({ type: "table", id, name: this.table(id)?.name })),
      ];
      return ctx;
    },
    describeContext() {
      const c = this.screenContext();
      if (!c.active) return "Nothing open";
      let s = c.active.name || c.active.type;
      if (c.selected_rows?.length) s += ` · ${c.selected_rows.length} row${c.selected_rows.length > 1 ? "s" : ""} selected`;
      if (c.selected_nodes.length) s += ` · ${c.selected_nodes.length} selected in sidebar`;
      return s;
    },
    /** Navigate to an item by id (used by chat links). */
    async openItem(id) {
      try {
        const it = await get(`/api/items/${id}`);
        this.openTable(it.table_id, { rowId: it.id });
      } catch (e) { toastError(e); }
    },
    /** Notify open views that server data changed (after bulk actions, imports...) */
    refreshViews(pred = () => true) {
      for (const [key, v] of views) if (pred(key, v)) v.load({ quiet: true });
    },
  };
}

// ------------------------------------------------------------ boot

document.addEventListener("alpine:init", () => {
  const boot = JSON.parse(document.getElementById("boot").textContent);
  window.APP_TZ = boot.timezone;
  const store = createStore(boot);
  Alpine.store("app", store);
  window.appStore = Alpine.store("app");

  Alpine.data("palette", paletteComponent);
  Alpine.data("aiWizard", aiWizardComponent);
  Alpine.data("viewer", viewerComponent);
  Alpine.data("importer", importComponent);
  Alpine.data("chat", chatComponent);
  Alpine.data("guided", guidedComponent);

  Alpine.data("shell", () => ({
    userMenu: false,
    theme: localStorage.getItem("theme") || document.documentElement.dataset.theme,
    get s() { return Alpine.store("app"); },
    async init() {
      await this.s.loadTree();
      if (this.s.activeKey && this.s.tabs.some((t) => t.key === this.s.activeKey)) this.s.showView(this.s.activeKey);
      else if (this.s.tabs.length) this.s.activate(this.s.tabs[0].key);
      this.initTabsSortable();
      this.initResizer();
      this.$watch("s.tabs", () => this.$nextTick(() => icons(document.querySelector(".tabs"))));
      icons();
      document.addEventListener("keydown", (e) => this.onKey(e));
    },
    onKey(e) {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); window.dispatchEvent(new CustomEvent("palette:open")); }
      else if (mod && e.key.toLowerCase() === "j") { e.preventDefault(); window.dispatchEvent(new CustomEvent("chat:toggle")); }
      else if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
        e.preventDefault(); window.dispatchEvent(new CustomEvent("palette:open"));
      }
    },
    setTheme(t) {
      this.theme = t;
      document.documentElement.dataset.theme = t;
      localStorage.setItem("theme", t);
    },
    initTabsSortable() {
      const el = document.querySelector(".tabs .tab-list");
      if (!el || !window.Sortable) return;
      Sortable.create(el, {
        animation: 150, direction: "horizontal", draggable: ".tab",
        onEnd: () => this.s.reorderTabs([...el.querySelectorAll(".tab")].map((t) => t.dataset.key)),
      });
    },
    initResizer() {
      const handle = document.querySelector(".resizer");
      if (!handle) return;
      const saved = LS.get("sidebar.w", null);
      if (saved) document.documentElement.style.setProperty("--sidebar-w", saved + "px");
      handle.addEventListener("pointerdown", (e) => {
        handle.classList.add("active");
        handle.setPointerCapture(e.pointerId);
        const move = (ev) => {
          const w = Math.max(200, Math.min(560, ev.clientX));
          document.documentElement.style.setProperty("--sidebar-w", w + "px");
          LS.set("sidebar.w", w);
        };
        const up = () => { handle.classList.remove("active"); handle.removeEventListener("pointermove", move); };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", up, { once: true });
      });
    },
    /** Start guided add in the current folder (or on the open table). */
    startGuided() {
      const tab = this.s.tabs.find((t) => t.key === this.s.activeKey);
      const detail = tab?.type === "folder" ? { folderId: tab.id }
        : tab?.type === "table" && this.s.table(tab.id)?.effective_active ? { tableId: tab.id }
        : { folderId: tab?.type === "table" ? this.s.table(tab.id)?.folder_id ?? null : null };
      window.dispatchEvent(new CustomEvent("guided:open", { detail }));
    },
    newTableHere() {
      const tab = this.s.tabs.find((t) => t.key === this.s.activeKey);
      let folderId = null;
      if (tab?.type === "folder") folderId = tab.id;
      else if (tab?.type === "table") folderId = this.s.table(tab.id)?.folder_id ?? null;
      this.s.newTable(folderId);
    },
    initials() {
      return (this.s.user.name || "?").split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
    },
  }));
});

export { toast };
