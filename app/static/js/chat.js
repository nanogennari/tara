// "Ask AI" side panel: chat with tool-using assistant that sees the screen and can navigate.
import { esc, icons, runJob } from "./util.js";

const KEY = "chat.history";

/** Tiny, safe markdown: escape first, then links / bold / italics / code / lists / paragraphs. */
export function renderMarkdown(src) {
  let s = esc(src || "");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>").replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\((item|table|folder):(\d+)\)/g,
    (_, text, kind, id) => `<a href="/${kind[0]}/${id}" class="ref ref-${kind}" data-ref="${kind}:${id}">${text}</a>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const lines = s.split("\n");
  let html = "", list = null;
  for (const line of lines) {
    const m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (m) {
      const tag = /^\s*\d/.test(line) ? "ol" : "ul";
      if (list !== tag) { if (list) html += `</${list}>`; html += `<${tag}>`; list = tag; }
      html += `<li>${m[1]}</li>`;
    } else {
      if (list) { html += `</${list}>`; list = null; }
      html += line.trim() ? `<p>${line}</p>` : "";
    }
  }
  if (list) html += `</${list}>`;
  return html;
}

export function chatComponent() {
  return {
    open: false,
    input: "",
    busy: false,
    shareContext: true,
    effort: localStorage.getItem("chat.effort") || "medium",
    efforts: { low: "Quick", medium: "Normal", high: "Thorough" },
    messages: JSON.parse(sessionStorage.getItem(KEY) || "[]"),
    abort: null,
    tick: 0, // bumped when the selection / active tab changes so the context label refreshes
    get s() { return Alpine.store("app"); },
    md: renderMarkdown,

    init() {
      window.addEventListener("chat:toggle", () => this.toggle());
      this.$watch("effort", (v) => localStorage.setItem("chat.effort", v));
      window.addEventListener("screen:changed", () => this.tick++);
      window.addEventListener("chat:ask", (e) => { this.show(); this.input = e.detail || ""; this.send(); });
      this.$watch("messages", () => {
        sessionStorage.setItem(KEY, JSON.stringify(this.messages.slice(-40)));
        this.$nextTick(() => { this.scroll(); icons(this.$root); });
      });
    },
    toggle() { this.open ? (this.open = false) : this.show(); },
    show() {
      this.open = true;
      this.$nextTick(() => { this.$refs.input?.focus(); this.scroll(); icons(this.$root); });
    },
    scroll() { const b = this.$refs.body; if (b) b.scrollTop = b.scrollHeight; },
    clear() { this.messages = []; },
    get contextLabel() { void this.tick; return this.s.describeContext(); },
    suggestions() {
      const c = this.s.screenContext();
      if (c.selected_rows?.length) return ["Summarise the selected rows", "Where else do we have similar items?"];
      if (c.active?.type === "table") return ["What's in this box?", "Anything expired or about to expire?", "Which quantities are only estimates?"];
      return ["What do we have for first aid?", "Which boxes haven't been updated in a while?", "Where are the laptop chargers?"];
    },

    async send(text) {
      const q = (text ?? this.input).trim();
      if (!q || this.busy) return;
      this.input = "";
      const history = this.messages.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }));
      this.messages.push({ role: "user", content: q });
      this.busy = true;
      this.abort = new AbortController();
      try {
        const res = await runJob("POST", "/api/ai/chat", {
          message: q, history, context: this.shareContext ? this.s.screenContext() : null, effort: this.effort,
        }, { signal: this.abort.signal });
        this.messages.push({ role: "assistant", content: res.reply, steps: res.steps, model: res.model });
        this.runActions(res.actions || []);
      } catch (e) {
        if (e.name !== "AbortError") this.messages.push({ role: "assistant", content: e.message, error: true });
      } finally {
        this.busy = false;
        this.abort = null;
        this.$nextTick(() => this.$refs.input?.focus());
      }
    },
    stop() { this.abort?.abort(); },
    /** Re-ask the question that produced the error at index i (without duplicating it). */
    retry(i) {
      const q = this.messages[i - 1]?.role === "user" ? this.messages[i - 1].content : null;
      if (!q || this.busy) return;
      this.messages.splice(i - 1, 2);
      this.send(q);
    },

    runActions(actions) {
      for (const a of actions) {
        if (a.type === "open_table") this.s.openTable(a.table_id, a.highlight?.length ? { highlight: a.highlight } : {});
        else if (a.type === "open_folder") this.s.openFolder(a.folder_id);
        else if (a.type === "open_search") this.s.openSearch(a.query, { folders: a.folder_ids || [] });
      }
      if (actions.length && matchMedia("(max-width: 900px)").matches) this.open = false;
    },
    onClick(e) {
      const a = e.target.closest("[data-ref]");
      if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return; // let the browser open a new tab
      e.preventDefault();
      const [kind, id] = a.dataset.ref.split(":");
      if (kind === "item") this.s.openItem(Number(id));
      else if (kind === "table") this.s.openTable(Number(id));
      else this.s.openFolder(Number(id));
      if (matchMedia("(max-width: 900px)").matches) this.open = false;
    },
    onKey(e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(); }
    },
  };
}
