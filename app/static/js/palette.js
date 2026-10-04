// Global search palette (Ctrl/⌘+K) with multi-folder scope (include subtrees, exclude children).
import { debounce, get, icons, relTime } from "./util.js";

export function paletteComponent() {
  return {
    open: false,
    q: "",
    loading: false,
    results: [],
    semanticError: null,
    cursor: 0,
    includeArchived: false,
    scope: { folders: [], exclude: [], tables: [] },
    scopeOpen: false,
    reqId: 0,

    get s() { return Alpine.store("app"); },

    init() {
      window.addEventListener("palette:open", (e) => this.show(e.detail || {}));
      this.run = debounce(() => this.search(), 180);
      this.$watch("results", () => this.$nextTick(() => icons(this.$root)));
      this.$watch("scopeOpen", () => this.$nextTick(() => icons(this.$root)));
    },

    show(detail = {}) {
      if (detail.folders || detail.tables) {
        this.scope = { folders: detail.folders || [], exclude: [], tables: detail.tables || [] };
      }
      this.open = true;
      this.scopeOpen = false;
      this.$nextTick(() => { this.$refs.input.focus(); this.$refs.input.select(); icons(this.$root); });
      if (this.q) this.search();
    },
    close() { this.open = false; },

    get scoped() { return this.scope.folders.length || this.scope.tables.length; },
    get scopeLabel() {
      if (!this.scoped) return "Everywhere";
      const names = [
        ...this.scope.folders.map((id) => this.s.folder(id)?.name).filter(Boolean),
        ...this.scope.tables.map((id) => this.s.table(id)?.name).filter(Boolean),
      ];
      const ex = this.scope.exclude.length ? ` (−${this.scope.exclude.length})` : "";
      return (names.length > 2 ? `${names.slice(0, 2).join(", ")} +${names.length - 2}` : names.join(", ")) + ex;
    },
    clearScope() { this.scope = { folders: [], exclude: [], tables: [] }; this.search(); },
    scopeToCurrent() {
      const tab = this.s.tabs.find((t) => t.key === this.s.activeKey);
      if (tab?.type === "table") this.scope = { folders: [], exclude: [], tables: [tab.id] };
      else if (tab?.type === "folder") this.scope = { folders: [tab.id], exclude: [], tables: [] };
      this.search();
    },
    get canScopeToCurrent() {
      const tab = this.s.tabs.find((t) => t.key === this.s.activeKey);
      return tab && (tab.type === "table" || tab.type === "folder");
    },

    // ---- scope tree (flattened for rendering)
    get scopeRows() {
      const out = [];
      const by = {};
      this.s.folders.forEach((f) => (by[f.parent_id ?? "root"] ||= []).push(f));
      const walk = (pid, depth) => (by[pid] || []).forEach((f) => { out.push({ f, depth }); walk(f.id, depth + 1); });
      walk("root", 0);
      return out;
    },
    /** 'on' (explicitly included), 'inherited' (ancestor included), 'excluded', or 'off' */
    folderState(id) {
      if (this.scope.exclude.includes(id)) return "excluded";
      if (this.scope.folders.includes(id)) return "on";
      const chain = this.s.folderPath(this.s.folder(id)?.parent_id).map((f) => f.id);
      for (let i = chain.length - 1; i >= 0; i--) {
        if (this.scope.exclude.includes(chain[i])) return "off";
        if (this.scope.folders.includes(chain[i])) return "inherited";
      }
      return "off";
    },
    toggleFolder(id) {
      const st = this.folderState(id);
      const sc = this.scope;
      if (st === "on") sc.folders = sc.folders.filter((x) => x !== id);
      else if (st === "inherited") sc.exclude = [...sc.exclude, id];
      else if (st === "excluded") sc.exclude = sc.exclude.filter((x) => x !== id);
      else sc.folders = [...sc.folders, id];
      // Including a folder makes explicit includes beneath it redundant
      if (st === "off") {
        sc.folders = sc.folders.filter((x) => x === id || !this.s.folderPath(x).some((f) => f.id === id && f.id !== x));
      }
      this.search();
    },

    // ---- search
    async search() {
      const q = this.q.trim();
      if (!q) { this.results = []; return; }
      const id = ++this.reqId;
      this.loading = true;
      const qs = new URLSearchParams({ q, limit: 30 });
      if (this.scope.folders.length) qs.set("folders", this.scope.folders.join(","));
      if (this.scope.exclude.length) qs.set("exclude_folders", this.scope.exclude.join(","));
      if (this.scope.tables.length) qs.set("tables", this.scope.tables.join(","));
      if (this.includeArchived) qs.set("include_archived", "1");
      try {
        const res = await get(`/api/search?${qs}`);
        if (id !== this.reqId) return;
        const order = { item: 0, table: 1, folder: 2 };
        this.results = res.results.sort((a, b) => order[a.type] - order[b.type]);
        this.semanticError = res.semantic_error;
        this.cursor = 0;
      } catch { /* toast would be noisy while typing */ }
      finally { if (id === this.reqId) this.loading = false; }
    },
    groupStart(i) { return i === 0 || this.results[i - 1].type !== this.results[i].type; },
    groupLabel(t) { return { item: "Items", table: "Tables", folder: "Folders" }[t]; },
    rel: relTime,

    move(d) {
      if (!this.results.length) return;
      this.cursor = (this.cursor + d + this.results.length) % this.results.length;
      this.$nextTick(() => this.$root.querySelector(".result.on")?.scrollIntoView({ block: "nearest" }));
    },
    pick(r) {
      r ||= this.results[this.cursor];
      if (!r) return;
      this.close();
      if (r.type === "item") this.s.openTable(r.table_id, { rowId: r.id });
      else if (r.type === "table") this.s.openTable(r.id);
      else this.s.openFolder(r.id);
    },
    seeAll() {
      if (!this.q.trim()) return;
      this.close();
      this.s.openSearch(this.q.trim(), { folders: this.scope.folders, exclude_folders: this.scope.exclude, tables: this.scope.tables, include_archived: this.includeArchived });
    },
  };
}
