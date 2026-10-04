// "Add with AI" wizard: upload photos -> AI proposal -> review (accept / edit / ask AI to change) -> commit.
import { api, get, icons, post, qtyFormat, resizeImage, toast, toastError } from "./util.js";

const MAX_PHOTOS = 12;

export function aiWizardComponent() {
  return {
    open: false,
    step: "upload", // upload | working | review
    tableId: null,
    table: null,
    files: [],       // [{file, url}]
    notes: "",
    skipExisting: true,
    dragging: false,
    proposal: null,  // {photos, notes, model}
    rows: [],
    versions: [],    // undo stack of rows snapshots
    history: [],     // instructions already applied
    instruction: "",
    rowRefine: { index: null, text: "" },
    workingLabel: "",
    elapsed: 0,
    timer: null,
    abort: null,
    error: "",

    get s() { return Alpine.store("app"); },
    get customCols() { return (this.table?.columns || []).filter((c) => c.type !== "builtin" && !c.hidden); },
    get selectedCount() { return this.rows.filter((r) => r.include).length; },

    init() {
      window.addEventListener("ai:open", (e) => this.show(e.detail.tableId));
      this.$watch("step", () => this.$nextTick(() => icons(this.$root)));
      this.$watch("rows", () => this.$nextTick(() => icons(this.$root)));
    },

    async show(tableId) {
      this.reset();
      this.tableId = tableId;
      this.open = true;
      this.$nextTick(() => icons(this.$root));
      try { this.table = await get(`/api/tables/${tableId}`); }
      catch (e) { toastError(e); this.open = false; }
    },
    reset() {
      this.files.forEach((f) => URL.revokeObjectURL(f.url));
      Object.assign(this, { step: "upload", files: [], notes: "", proposal: null, rows: [], versions: [], history: [],
        instruction: "", rowRefine: { index: null, text: "" }, error: "", table: null });
    },
    async close(force = false) {
      if (this.step === "review" && this.proposal && !force) {
        const { confirmDialog } = await import("./util.js");
        const ok = await confirmDialog({ title: "Discard this draft?", confirm: "Discard", danger: true,
          message: `${this.rows.length} proposed items haven't been added yet.` });
        if (!ok) return;
      }
      this.abort?.abort();
      this.stopTimer();
      if (this.proposal && !force) post("/api/ai/discard", { photo_ids: this.proposal.photos.map((p) => p.id) }).catch(() => {});
      this.open = false;
      this.reset();
    },

    // ---------------------------------------------------- photos
    addFiles(list) {
      const imgs = [...list].filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
      const room = MAX_PHOTOS - this.files.length;
      if (imgs.length > room) toast(`Up to ${MAX_PHOTOS} photos per request`, { error: true });
      imgs.slice(0, room).forEach((file) => this.files.push({ file, url: URL.createObjectURL(file) }));
      this.$nextTick(() => icons(this.$root));
    },
    removeFile(i) { URL.revokeObjectURL(this.files[i].url); this.files.splice(i, 1); },
    onDrop(e) { this.dragging = false; this.addFiles(e.dataTransfer.files); },
    onPaste(e) { if (this.open && this.step === "upload" && e.clipboardData?.files?.length) this.addFiles(e.clipboardData.files); },
    preview(i) {
      window.dispatchEvent(new CustomEvent("viewer:open", { detail: {
        photos: this.files.map((f, n) => ({ display: f.url, thumb: f.url, name: `Photo ${n + 1}` })), index: i } }));
    },
    previewProposal(i) {
      window.dispatchEvent(new CustomEvent("viewer:open", { detail: { photos: this.proposal.photos, index: i } }));
    },

    // ---------------------------------------------------- AI calls
    startTimer(label) {
      this.workingLabel = label;
      this.elapsed = 0;
      this.stopTimer();
      this.timer = setInterval(() => this.elapsed++, 1000);
    },
    stopTimer() { clearInterval(this.timer); this.timer = null; },

    async analyze() {
      if (!this.files.length) return;
      this.error = "";
      this.step = "working";
      this.startTimer("Preparing photos…");
      this.abort = new AbortController();
      try {
        const fd = new FormData();
        for (const f of this.files) fd.append("photos", await resizeImage(f.file, 1568, 0.88));
        fd.append("notes", this.notes);
        fd.append("skip_existing", this.skipExisting ? "1" : "0");
        this.workingLabel = `Looking at ${this.files.length} photo${this.files.length > 1 ? "s" : ""}…`;
        const res = await api("POST", `/api/tables/${this.tableId}/ai/propose`, fd, { signal: this.abort.signal });
        this.proposal = { photos: res.photos, notes: res.notes, model: res.model };
        this.rows = res.items.map(toRow);
        this.step = "review";
        if (!this.rows.length) this.error = "The AI didn't find any new items. You can ask it to look again below.";
      } catch (e) {
        if (e.name === "AbortError") { this.step = "upload"; return; }
        this.error = e.message;
        this.step = "upload";
      } finally { this.stopTimer(); this.abort = null; }
    },
    cancelWork() { this.abort?.abort(); },

    async refine(rowIndex = null) {
      const text = (rowIndex === null ? this.instruction : this.rowRefine.text).trim();
      if (!text) return;
      const before = this.step;
      this.error = "";
      this.step = "working";
      this.startTimer(rowIndex === null ? "Revising the draft…" : `Revising row ${rowIndex + 1}…`);
      this.abort = new AbortController();
      try {
        const res = await api("POST", `/api/tables/${this.tableId}/ai/refine`, {
          photo_ids: this.proposal.photos.map((p) => p.id), items: this.rows.map(toApi), instruction: text,
          row_index: rowIndex, history: this.history, notes: this.notes,
        }, { signal: this.abort.signal });
        this.versions.push(JSON.parse(JSON.stringify(this.rows)));
        const prevIncluded = this.rows.map((r) => r.include);
        this.rows = res.items.map((it, i) => {
          const row = toRow(it);
          if (rowIndex === null && i < prevIncluded.length && res.items.length === prevIncluded.length) row.include = prevIncluded[i];
          row.changed = true;
          return row;
        });
        if (res.notes) this.proposal.notes = res.notes;
        this.history.push(rowIndex === null ? text : `Row ${rowIndex + 1}: ${text}`);
        this.instruction = "";
        this.rowRefine = { index: null, text: "" };
      } catch (e) {
        if (e.name !== "AbortError") this.error = e.message;
      } finally {
        this.stopTimer();
        this.abort = null;
        this.step = before === "working" ? "review" : before;
      }
    },
    undo() {
      if (!this.versions.length) return;
      this.rows = this.versions.pop();
      this.history.pop();
    },
    openRowRefine(i) {
      this.rowRefine = { index: this.rowRefine.index === i ? null : i, text: "" };
      this.$nextTick(() => this.$root.querySelector(`[data-refine="${i}"]`)?.focus());
    },
    removeRow(i) { this.versions.push(JSON.parse(JSON.stringify(this.rows))); this.rows.splice(i, 1); },
    toggleAll(v) { this.rows.forEach((r) => (r.include = v)); },

    async commit() {
      const items = this.rows.filter((r) => r.include && r.description.trim()).map(toApi);
      if (!items.length) return;
      this.step = "working";
      this.startTimer("Adding items…");
      try {
        const res = await post(`/api/tables/${this.tableId}/ai/commit`, { items, photo_ids: this.proposal.photos.map((p) => p.id) });
        toast(`Added ${res.created.length} item${res.created.length === 1 ? "" : "s"}`);
        this.proposal = null;
        this.close(true);
        this.s.refreshViews((k) => k === `table:${this.tableId}`);
        this.s.loadTree();
      } catch (e) {
        this.error = e.message;
        this.step = "review";
      } finally { this.stopTimer(); }
    },

    photoFor(i) { return this.proposal?.photos[i]; },
    confLabel(c) { return { high: "High confidence", medium: "Medium confidence", low: "Low confidence — check carefully" }[c]; },
  };
}

export function toRow(it) {
  return {
    include: true,
    description: it.description || "",
    quantity: it.quantity,
    qtyText: qtyFormat(it.quantity),
    observation: it.observation || "",
    custom: { ...(it.custom || {}) },
    photo_indexes: it.photo_indexes || [],
    confidence: it.confidence || "medium",
    possible_duplicates: it.possible_duplicates || [],
    changed: false,
  };
}

export function toApi(r) {
  const qtyEdited = r.qtyText.trim() !== qtyFormat(r.quantity);
  return {
    description: r.description.trim(),
    quantity: qtyEdited ? r.qtyText.trim() : r.quantity,
    observation: r.observation.trim(),
    custom: r.custom,
    photo_indexes: r.photo_indexes,
    confidence: r.confidence,
  };
}
