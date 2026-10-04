// Guided add mode: a focused loop for cataloguing box after box without ever seeing the grid.
//   where   -> browse folders, pick or create a table
//   capture -> take photos (+ note) for AI, or type an item in
//   confirm -> review the AI's proposal (accept / edit / ask AI to change) -> add -> back to capture
import { api, get, icons, post, promptDialog, qtyFormat, resizeImage, runJob, toast, toastError } from "./util.js";
import { toApi, toRow } from "./ai-wizard.js";

const MAX_PHOTOS = 20;

export function guidedComponent() {
  return {
    open: false,
    step: "where",     // where | capture | manual | working | confirm
    folderId: null,
    table: null,       // full table (columns) once chosen
    files: [],
    dragging: false,
    maxPhotos: MAX_PHOTOS,
    limitNotice: "",
    notes: "",
    proposal: null,
    rows: [],
    instruction: "",
    workingLabel: "",
    elapsed: 0,
    timer: null,
    abort: null,
    error: "",
    retryFn: null,
    added: [],         // items added this session (newest first)
    manual: null,

    get s() { return Alpine.store("app"); },
    get here() { return this.folderId ? this.s.folder(this.folderId) : null; },
    get crumbs() { return this.folderId ? this.s.folderPath(this.folderId) : []; },
    get subfolders() { return this.s.folders.filter((f) => (f.parent_id ?? null) === this.folderId && f.effective_active); },
    get tablesHere() { return this.s.tables.filter((t) => (t.folder_id ?? null) === this.folderId && t.effective_active); },
    get customCols() { return (this.table?.columns || []).filter((c) => c.type !== "builtin" && !c.hidden); },
    get tablePath() { return this.table ? [...this.table.path, this.table.name].join(" / ") : ""; },
    get selectedCount() { return this.rows.filter((r) => r.include).length; },

    init() {
      window.addEventListener("guided:open", (e) => this.start(e.detail || {}));
      // Each step starts at the top (e.g. the "Applying your change…" spinner after a long result list)
      this.$watch("step", () => this.$nextTick(() => {
        icons(this.$root);
        const body = this.$root.querySelector(".guided-body");
        if (body) body.scrollTop = 0;
      }));
      this.$watch("folderId", () => this.$nextTick(() => icons(this.$root)));
      this.$watch("rows", () => this.$nextTick(() => icons(this.$root)));
    },

    start({ folderId = null, tableId = null } = {}) {
      Object.assign(this, { open: true, folderId, table: null, added: [], error: "" });
      this.resetCapture();
      if (tableId) this.pickTable(tableId);
      else this.step = "where";
      this.$nextTick(() => icons(this.$root));
    },
    async exit() {
      if (this.step === "confirm" && this.rows.length) {
        const { confirmDialog } = await import("./util.js");
        if (!(await confirmDialog({ title: "Leave guided add?", confirm: "Discard draft", danger: true,
          message: `${this.rows.length} proposed items haven't been added.` }))) return;
        this.discardDraft();
      }
      this.abort?.abort();
      this.open = false;
      if (this.added.length) {
        this.s.loadTree();
        this.s.refreshViews((k) => k.startsWith("table:"));
        toast(`Added ${this.added.length} item${this.added.length > 1 ? "s" : ""} in guided mode`);
      }
    },

    // ---------------------------------------------------- where
    enter(fid) { this.folderId = fid; },
    up() { this.folderId = this.here?.parent_id ?? null; },
    async newFolder() {
      const name = await promptDialog({ title: "New folder", label: "Folder name", confirm: "Create" });
      if (!name) return;
      try {
        const f = await post("/api/folders", { name, parent_id: this.folderId });
        await this.s.loadTree();
        this.folderId = f.id;
      } catch (e) { toastError(e); }
    },
    async newTable() {
      const name = await promptDialog({ title: "New table", label: "What container is this? (e.g. “Box 08: Games”)", confirm: "Create & start adding" });
      if (!name) return;
      try {
        const t = await post("/api/tables", { name, folder_id: this.folderId });
        await this.s.loadTree();
        await this.pickTable(t.id);
      } catch (e) { toastError(e); }
    },
    async pickTable(id) {
      try {
        this.table = await get(`/api/tables/${id}`);
        this.folderId = this.table.folder_id ?? null;
        this.resetCapture();
        this.step = "capture";
      } catch (e) { toastError(e); }
    },
    changeTable() { this.discardDraft(); this.step = "where"; },

    // ---------------------------------------------------- capture
    resetCapture() {
      this.files.forEach((f) => URL.revokeObjectURL(f.url));
      Object.assign(this, { files: [], notes: "", proposal: null, rows: [], instruction: "", error: "", limitNotice: "" });
    },
    addFiles(list) {
      const imgs = [...list].filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
      const room = Math.max(0, MAX_PHOTOS - this.files.length);
      imgs.slice(0, room).forEach((file) => this.files.push({ file, url: URL.createObjectURL(file) }));
      const left = imgs.length - Math.min(imgs.length, room);
      // Persistent (not a toast): say exactly what was left out and what to do
      this.limitNotice = left
        ? `Only ${MAX_PHOTOS} photos fit in one batch — ${left} photo${left > 1 ? "s were" : " was"} not added. ` +
          "Analyze these first, then add the rest in a new batch."
        : "";
      this.$nextTick(() => icons(this.$root));
    },
    removeFile(i) { URL.revokeObjectURL(this.files[i].url); this.files.splice(i, 1); this.limitNotice = ""; },
    get atLimit() { return this.files.length >= MAX_PHOTOS; },

    startTimer(label) {
      this.workingLabel = label;
      this.elapsed = 0;
      clearInterval(this.timer);
      this.timer = setInterval(() => this.elapsed++, 1000);
    },
    stopTimer() { clearInterval(this.timer); this.timer = null; },

    async analyze() {
      if (!this.files.length) return;
      this.error = "";
      this.retryFn = null;
      this.step = "working";
      this.startTimer("Looking at the photos…");
      this.abort = new AbortController();
      try {
        const fd = new FormData();
        for (const f of this.files) fd.append("photos", await resizeImage(f.file, 1568, 0.88));
        fd.append("notes", this.notes);
        fd.append("skip_existing", "1");
        const res = await runJob("POST", `/api/tables/${this.table.id}/ai/propose`, fd, { signal: this.abort.signal });
        this.proposal = { photos: res.photos, notes: res.notes };
        this.rows = res.items.map(toRow);
        if (!this.rows.length) {
          this.error = "No new items found in these photos. Try another angle, add a note, or type it in.";
          this.discardDraft();
          this.step = "capture";
          return;
        }
        this.step = "confirm";
      } catch (e) {
        if (e.name !== "AbortError") this.fail(e, () => this.analyze());
        this.step = "capture";
      } finally { this.stopTimer(); this.abort = null; }
    },
    cancel() { this.abort?.abort(); },

    /** Show an error with a "Try again" button that repeats the failed step. */
    fail(e, retry) {
      this.error = e.message || String(e);
      this.retryFn = retry || null;
    },
    tryAgain() {
      const f = this.retryFn;
      this.error = "";
      this.retryFn = null;
      f?.();
    },


    // ---------------------------------------------------- confirm
    async refine() {
      const text = this.instruction.trim();
      if (!text) return;
      this.step = "working";
      this.startTimer("Applying your change…");
      try {
        const res = await runJob("POST", `/api/tables/${this.table.id}/ai/refine`, {
          photo_ids: this.proposal.photos.map((p) => p.id), items: this.rows.map(toApi), instruction: text, notes: this.notes,
        });
        this.rows = res.items.map(toRow);
        if (res.notes) this.proposal.notes = res.notes;
        this.instruction = "";
      } catch (e) { this.fail(e, () => this.refine()); }
      finally { this.stopTimer(); this.step = "confirm"; }
    },
    async confirm() {
      const items = this.rows.filter((r) => r.include && r.description.trim()).map(toApi);
      if (!items.length) return;
      this.step = "working";
      this.startTimer("Adding…");
      try {
        const res = await post(`/api/tables/${this.table.id}/ai/commit`, { items, photo_ids: this.proposal.photos.map((p) => p.id) });
        this.recordAdded(res.created);
        this.proposal = null;
        this.resetCapture();
        this.step = "capture";
      } catch (e) { this.fail(e, () => this.confirm()); this.step = "confirm"; }
      finally { this.stopTimer(); }
    },
    discardDraft() {
      if (this.proposal) post("/api/ai/discard", { photo_ids: this.proposal.photos.map((p) => p.id) }).catch(() => {});
      this.proposal = null;
      this.rows = [];
    },
    retake() { this.discardDraft(); this.step = "capture"; },
    recordAdded(items) {
      for (const it of items) this.added.unshift({ id: it.id, description: it.description, qty: it.quantity_display, table: this.table.name, thumb: it.photos[0]?.thumb });
      toast(`Added ${items.length} item${items.length > 1 ? "s" : ""} to ${this.table.name}`);
    },

    // ---------------------------------------------------- manual entry
    startManual() {
      this.manual = { description: "", qty: "", observation: "", custom: {}, files: [] };
      this.step = "manual";
      this.$nextTick(() => this.$root.querySelector("[data-manual-desc]")?.focus());
    },
    addManualFiles(list) {
      if (!this.manual) return;
      [...list].filter((f) => f.type.startsWith("image/")).forEach((file) => this.manual.files.push({ file, url: URL.createObjectURL(file) }));
    },
    async saveManual(again = true) {
      const m = this.manual;
      if (!m.description.trim()) return;
      this.step = "working";
      this.startTimer("Adding…");
      try {
        const item = await post(`/api/tables/${this.table.id}/items`, {
          description: m.description.trim(), quantity: m.qty.trim() || null, observation: m.observation.trim(), custom: m.custom,
        });
        if (m.files.length) {
          const fd = new FormData();
          for (const f of m.files) fd.append("files", await resizeImage(f.file, 2400, 0.9));
          const photos = await api("POST", `/api/items/${item.id}/photos`, fd);
          item.photos = photos;
        }
        this.recordAdded([item]);
        m.files.forEach((f) => URL.revokeObjectURL(f.url));
        if (again) this.startManual(); else this.step = "capture";
      } catch (e) { this.fail(e, () => this.saveManual(again)); this.step = "manual"; }
      finally { this.stopTimer(); }
    },

    qty: qtyFormat,
    photoFor(i) { return this.proposal?.photos[i]; },
  };
}
