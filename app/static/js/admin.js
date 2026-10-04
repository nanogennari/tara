// Admin settings page (Alpine component).
import { api, confirmDialog, del, fmtDateTime, get, icons, patch, post, put, relTime, toast, toastError } from "./util.js";

const SWATCHES = ["#4f46e5", "#2563eb", "#0891b2", "#059669", "#16a34a", "#ca8a04", "#ea580c", "#dc2626", "#db2777", "#7c3aed", "#475569"];

document.addEventListener("alpine:init", () => {
  Alpine.data("admin", () => ({
    tab: location.hash.slice(1) || "general",
    loaded: false,
    values: {},
    ai: { provider: "anthropic", providers: {} },
    providers: {},
    embedProviders: {},
    branding: {},
    timezones: [],
    users: [],
    audit: [],
    status: null,
    statusTimer: null,
    testResult: {},
    models: {},
    customModel: false,
    customEmbed: false,
    busy: "",
    newUser: { username: "", display_name: "", email: "", role: "editor", password: "" },
    invites: [],
    usage: null,
    usageDays: 30,
    num: (n) => (n || 0).toLocaleString(),
    invite: { role: "editor", max_uses: 1, expires_days: 7, note: "" },
    searchQ: "",
    searchRes: null,
    swatches: SWATCHES,
    rel: relTime,
    fmt: fmtDateTime,

    async init() {
      await this.loadSettings();
      this.$watch("ai.provider", () => { this.customModel = false; });
      this.$watch("tab", (t) => { location.hash = t; this.onTab(); this.$nextTick(() => icons()); });
      window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (t && t !== this.tab) this.tab = t; });
      this.onTab();
      this.$nextTick(() => icons());
    },
    onTab() {
      if (this.tab === "users") { this.loadUsers(); this.loadInvites(); }
      if (this.tab === "activity") this.loadAudit();
      if (this.tab === "usage") this.loadUsage();
      clearInterval(this.statusTimer);
      if (this.tab === "search") { this.loadStatus(); this.statusTimer = setInterval(() => this.loadStatus(), 3000); }
    },
    async loadSettings() {
      try {
        const d = await get("/api/admin/settings");
        this.values = d.values;
        this.ai = d.ai;
        this.providers = d.providers;
        this.embedProviders = d.embed_providers;
        this.branding = d.branding;
        this.timezones = d.timezones;
        window.APP_TZ = d.values["server.timezone"];
        this.loaded = true;
      } catch (e) { toastError(e); }
    },
    async save(keys, providerNames = []) {
      this.busy = "save";
      try {
        const values = Object.fromEntries(keys.map((k) => [k, this.values[k]]));
        const providers = Object.fromEntries(providerNames.map((n) => [n, this.ai.providers[n]]));
        const d = await put("/api/admin/settings", { values, providers });
        this.values = d.values;
        this.ai = d.ai;
        toast("Settings saved");
        if (keys.some((k) => k.startsWith("app."))) setTimeout(() => location.reload(), 600);
      } catch (e) { toastError(e); }
      finally { this.busy = ""; }
    },

    // ---- appearance
    previewColor(c) {
      this.values["app.primary_color"] = c;
      document.documentElement.style.setProperty("--primary", c);
    },
    async uploadBrand(kind, ev) {
      const f = ev.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      try { await api("POST", `/api/admin/branding/${kind}`, fd); toast("Uploaded"); setTimeout(() => location.reload(), 500); }
      catch (e) { toastError(e); }
    },
    async removeBrand(kind) {
      try { await del(`/api/admin/branding/${kind}`); location.reload(); } catch (e) { toastError(e); }
    },

    // ---- users
    async loadUsers() { try { this.users = await get("/api/admin/users"); this.$nextTick(() => icons()); } catch (e) { toastError(e); } },
    async createUser() {
      try {
        await post("/api/admin/users", this.newUser);
        this.newUser = { username: "", display_name: "", email: "", role: "editor", password: "" };
        toast("User created");
        this.loadUsers();
      } catch (e) { toastError(e); }
    },
    async updateUser(u, data) {
      try { await patch(`/api/admin/users/${u.id}`, data); toast("Saved"); } catch (e) { toastError(e); }
      this.loadUsers();
    },
    async resetPassword(u) {
      const { promptDialog } = await import("./util.js");
      const pw = await promptDialog({ title: `New password for ${u.username}`, label: "At least 8 characters", confirm: "Set password" });
      if (pw) this.updateUser(u, { password: pw });
    },
    async deleteUser(u) {
      if (!(await confirmDialog({ title: `Delete ${u.username}?`, danger: true, confirm: "Delete user", message: "Their edits stay; they just can't sign in any more." }))) return;
      try { await del(`/api/admin/users/${u.id}`); this.loadUsers(); } catch (e) { toastError(e); }
    },

    // ---- invites
    async loadInvites() {
      try { this.invites = await get("/api/admin/invites"); this.$nextTick(() => icons()); } catch (e) { toastError(e); }
    },
    async createInvite() {
      try {
        const inv = await post("/api/admin/invites", this.invite);
        this.copy(inv.url);
        this.loadInvites();
        this.$nextTick(() => icons());
      } catch (e) { toastError(e); }
    },
    async revokeInvite(i) {
      try { await del(`/api/admin/invites/${i.id}`); this.loadInvites(); } catch (e) { toastError(e); }
    },
    async copy(text) {
      try { await navigator.clipboard.writeText(text); toast("Link copied to clipboard"); }
      catch { window.prompt("Copy this link:", text); }
    },

    // ---- AI
    get cur() { return this.ai.providers[this.ai.provider] || {}; },
    get curMeta() { return this.providers[this.ai.provider] || {}; },
    async testAI() {
      this.busy = "test";
      this.testResult = {};
      try { this.testResult = await post("/api/admin/ai/test", { provider: this.ai.provider, conf: this.cur }); }
      catch (e) { this.testResult = { ok: false, message: e.message }; }
      finally { this.busy = ""; }
    },
    async fetchModels() {
      this.busy = "models";
      try {
        const r = await post("/api/admin/ai/models", { provider: this.ai.provider, conf: this.cur });
        this.models = { ...this.models, [this.ai.provider]: r.models };
        this.customModel = false;
        toast(`${r.models.length} models found`);
      } catch (e) { toastError(e); }
      finally { this.busy = ""; }
    },
    modelOptions() {
      const live = this.models[this.ai.provider];
      // Once the server's list is loaded, show exactly that (plus the saved model); before, the suggestions
      const base = live?.length ? live : (this.curMeta.models || []);
      return [...new Set([this.cur.model, ...base].filter(Boolean))];
    },
    pickModel(v) {
      if (v === "__custom__") { this.customModel = true; this.cur.model = ""; return; }
      this.cur.model = v;
    },
    modelHint() {
      const live = this.models[this.ai.provider];
      if (!live) return "Suggested models — use “Load available models” to list what your server/account offers.";
      if (this.cur.model && !live.includes(this.cur.model)) return `“${this.cur.model}” is not in the ${live.length} models the server reported.`;
      return `${live.length} models available on the server.`;
    },
    async saveAI() {
      this.values["ai.provider"] = this.ai.provider;
      await this.save(["ai.provider", "ai.org_context", "ai.system_prompt", "ai.max_existing_items"], [this.ai.provider]);
    },

    // ---- search
    async loadStatus() { try { this.status = await get("/api/admin/search/status"); } catch { /* ignore */ } },
    async rebuild() {
      if (!(await confirmDialog({ title: "Rebuild search index?", confirm: "Rebuild", message: "All items are re-indexed in the background. Search keeps working meanwhile." }))) return;
      try { this.status = await post("/api/admin/search/rebuild"); } catch (e) { toastError(e); }
    },
    async testSearch() {
      if (!this.searchQ.trim()) return;
      try { this.searchRes = await post("/api/admin/search/test", { q: this.searchQ }); } catch (e) { toastError(e); }
    },
    embedModels() { return this.embedProviders[this.values["search.provider"]]?.models || []; },

    // ---- data
    async restore(ev) {
      const f = ev.target.files[0];
      if (!f) return;
      if (!(await confirmDialog({ title: "Restore backup?", danger: true, confirm: "Replace all data",
        message: "Everything in the current database will be replaced by the backup. Download a backup of the current data first if unsure." }))) { ev.target.value = ""; return; }
      const fd = new FormData();
      fd.append("file", f);
      this.busy = "restore";
      try { await api("POST", "/api/admin/restore", fd); toast("Backup restored"); setTimeout(() => (location.href = "/"), 1200); }
      catch (e) { toastError(e); }
      finally { this.busy = ""; ev.target.value = ""; }
    },
    async runMaintenance() {
      try { const r = await post("/api/admin/maintenance/run"); toast(`Purged ${r.trash.items} old rows, ${r.pending_photos} abandoned photos`); }
      catch (e) { toastError(e); }
    },
    async loadUsage() { try { this.usage = await get(`/api/admin/usage?days=${this.usageDays}`); } catch (e) { toastError(e); } },
    async loadAudit() { try { this.audit = await get("/api/admin/audit"); } catch (e) { toastError(e); } },
  }));
});
