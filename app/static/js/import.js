// XLSX import dialog: analyze sheets, let the user pick, then import into a folder.
import { api, icons, toast } from "./util.js";

export function importComponent() {
  return {
    open: false,
    folderId: null,
    file: null,
    sheets: [],
    picked: [],
    busy: false,
    error: "",
    get s() { return Alpine.store("app"); },
    get folderName() { return this.folderId ? (this.s.folder(this.folderId)?.name || "folder") : "Top level"; },

    init() {
      window.addEventListener("import:open", (e) => {
        Object.assign(this, { open: true, folderId: e.detail?.folderId ?? null, file: null, sheets: [], picked: [], error: "", busy: false });
        this.$nextTick(() => icons(this.$root));
      });
    },
    async choose(e) {
      this.file = e.target.files[0];
      if (!this.file) return;
      this.busy = true;
      this.error = "";
      const fd = new FormData();
      fd.append("file", this.file);
      try {
        this.sheets = await api("POST", "/api/import/xlsx/analyze", fd);
        this.picked = this.sheets.filter((s) => s.include).map((s) => s.sheet);
      } catch (err) { this.error = err.message; this.sheets = []; }
      finally { this.busy = false; this.$nextTick(() => icons(this.$root)); }
    },
    mapping(sh) {
      return (sh.columns || []).map((c) => c.maps_to === "custom" ? `${c.header} → new ${c.type} column` : `${c.header} → ${c.maps_to}`).join(" · ");
    },
    async run() {
      if (!this.file || !this.picked.length) return;
      this.busy = true;
      const fd = new FormData();
      fd.append("file", this.file);
      if (this.folderId) fd.append("folder_id", this.folderId);
      this.picked.forEach((s) => fd.append("sheets", s));
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
