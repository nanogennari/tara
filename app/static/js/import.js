// XLSX import dialog: analyze sheets, let the user pick, then import into a folder.
import { api, icons, toast } from "./util.js";
import { TYPE_LABELS } from "./columns.js";

export const TARGETS = { description: "Description", quantity: "Quantity", observation: "Observation",
  photos: "Photos", custom: "New column", skip: "Don't import" };

export function importComponent() {
  return {
    open: false,
    folderId: null,
    file: null,
    sheets: [],
    picked: [],
    busy: false,
    dragging: false,
    error: "",
    get s() { return Alpine.store("app"); },
    get folderName() { return this.folderId ? (this.s.folder(this.folderId)?.name || "folder") : "Top level"; },

    init() {
      window.addEventListener("import:open", (e) => {
        Object.assign(this, { open: true, folderId: e.detail?.folderId ?? null, file: null, sheets: [], picked: [], error: "", busy: false });
        this.$nextTick(() => icons(this.$root));
      });
    },
    async useFile(file) {
      if (!file) return;
      if (!/\.xls[xm]$/i.test(file.name)) { this.error = "Only .xlsx spreadsheets can be imported."; return; }
      this.file = file;
      this.busy = true;
      this.error = "";
      const fd = new FormData();
      fd.append("file", this.file);
      try {
        this.sheets = (await api("POST", "/api/import/xlsx/analyze", fd)).map((sh) => ({ ...sh, editing: false }));
        this.picked = this.sheets.filter((s) => s.include).map((s) => s.sheet);
      } catch (err) { this.error = err.message; this.sheets = []; }
      finally { this.busy = false; this.$nextTick(() => icons(this.$root)); }
    },
    mapping(sh) {
      return (sh.columns || []).map((c) => c.maps_to === "custom" ? `${c.header} → new ${TYPE_LABELS[c.type].toLowerCase()} column`
        : c.maps_to === "skip" ? `${c.header} → skipped` : `${c.header} → ${TARGETS[c.maps_to].toLowerCase()}`).join(" · ");
    },
    targets: TARGETS,
    types: TYPE_LABELS,
    /** Map a column; a built-in target can only be used once, so its previous column becomes a new column. */
    setTarget(sh, col, target) {
      if (!["custom", "skip"].includes(target)) {
        for (const c of sh.columns) if (c !== col && c.maps_to === target) c.maps_to = "custom";
      }
      col.maps_to = target;
      if (!this.picked.includes(sh.sheet) && sh.header_row) this.picked.push(sh.sheet);
    },
    toggleMapping(sh) {
      sh.editing = !sh.editing;
      this.$nextTick(() => icons(this.$root));
    },
    async run() {
      if (!this.file || !this.picked.length) return;
      this.busy = true;
      const fd = new FormData();
      fd.append("file", this.file);
      if (this.folderId) fd.append("folder_id", this.folderId);
      this.picked.forEach((s) => fd.append("sheets", s));
      fd.append("mappings", JSON.stringify(Object.fromEntries(this.sheets.filter((sh) => sh.columns && this.picked.includes(sh.sheet))
        .map((sh) => [sh.sheet, sh.columns.map(({ index, maps_to, type }) => ({ index, maps_to, type }))]))));
      try {
        const res = await api("POST", "/api/import/xlsx", fd);
        toast(`Imported ${res.tables.length} tables, ${res.items} items, ${res.photos} photos`);
        this.open = false;
        if (this.folderId) this.s.reveal(this.folderId);
        await this.s.loadTree();
        this.s.refreshViews((k) => k === `folder:${this.folderId}`);
        if (res.tables.length === 1) this.s.openTable(res.tables[0].id);
        else if (this.folderId) this.s.openFolder(this.folderId);
      } catch (err) { this.error = err.message; }
      finally { this.busy = false; }
    },
  };
}
