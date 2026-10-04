// Shared helpers: API client, formatting, toasts, dialogs, context menus, icons.

const CSRF = document.querySelector('meta[name="csrf-token"]')?.content;

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function api(method, url, body, { signal } = {}) {
  const opts = { method, headers: { "X-CSRFToken": CSRF, Accept: "application/json" }, signal };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Network error, check your connection", 0);
  }
  if (res.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); throw new ApiError("Not logged in", 401); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
  return data;
}
export const get = (u, o) => api("GET", u, undefined, o);
export const post = (u, b, o) => api("POST", u, b ?? {}, o);
export const patch = (u, b) => api("PATCH", u, b);
export const put = (u, b) => api("PUT", u, b);
export const del = (u) => api("DELETE", u);

// ------------------------------------------------------------ formatting

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function parseIso(iso) { return iso ? new Date(iso) : null; }

export function relTime(iso, short = false) {
  const d = parseIso(iso);
  if (!d) return "";
  const s = (Date.now() - d.getTime()) / 1000;
  const units = [[60, "s", "second"], [60, "m", "minute"], [24, "h", "hour"], [30, "d", "day"], [12, "mo", "month"], [Infinity, "y", "year"]];
  let v = s;
  if (v < 45) return short ? "now" : "just now";
  for (let i = 0; i < units.length; i++) {
    const [div, sh, long] = units[i];
    if (Math.abs(v) < div || i === units.length - 1) {
      const n = Math.max(1, Math.floor(v));
      return short ? `${n}${sh}` : `${n} ${long}${n === 1 ? "" : "s"} ago`;
    }
    v /= div;
  }
}

/** "2026-10-03 21:45" in the timezone configured in Settings (window.APP_TZ), else the browser's. */
export function fmtDateTime(iso, { date = true, time = true } = {}) {
  const d = parseIso(iso);
  if (!d) return "";
  const opts = { timeZone: window.APP_TZ || undefined, hourCycle: "h23" };
  if (date) Object.assign(opts, { year: "numeric", month: "2-digit", day: "2-digit" });
  if (time) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  try { return new Intl.DateTimeFormat("sv-SE", opts).format(d); }
  catch { return d.toISOString().slice(0, 16).replace("T", " "); }
}

export function ageDays(iso) {
  const d = parseIso(iso);
  return d ? (Date.now() - d.getTime()) / 86400000 : Infinity;
}

export function fmtNum(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (Number.isInteger(n)) return Math.abs(n) >= 10000 ? n.toLocaleString("en-US") : String(n);
  return String(+n.toFixed(4));
}

const SINGULAR = { pcs: "pc", units: "unit", pairs: "pair", packs: "pack", boxes: "box", bags: "bag", sets: "set",
  rolls: "roll", bottles: "bottle", tubes: "tube", sheets: "sheet", bundles: "bundle", tabs: "tab", caps: "cap",
  caplets: "caplet", kits: "kit", cases: "case" };

export function qtyFormat(q) {
  if (!q) return "";
  if (typeof q === "string") return q;
  if (q.type === "text") return q.text || "";
  if (q.value === null || q.value === undefined) return "";
  let s = fmtNum(q.value);
  if (q.unit) s += " " + (Number(q.value) === 1 && SINGULAR[q.unit] ? SINGULAR[q.unit] : q.unit);
  return (q.estimated ? "~" : "") + s;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function icons(root = document) {
  if (window.lucide) window.lucide.createIcons({ root, attrs: { "aria-hidden": "true" } });
}
export const icon = (name) => `<i data-lucide="${name}"></i>`;

// ------------------------------------------------------------ images

/** Downscale an image file in the browser (keeps uploads small and fast). */
export async function resizeImage(file, maxPx = 1568, quality = 0.88) {
  if (!file.type.startsWith("image/")) return file;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxPx / Math.max(bmp.width, bmp.height));
    if (scale === 1 && file.size < 1.5e6 && file.type === "image/jpeg") return file;
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", quality));
    return new File([blob], (file.name || "photo").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch {
    return file; // HEIC etc. that the browser can't decode: let the server try
  }
}

// ------------------------------------------------------------ toasts

let toastBox;
export function toast(message, { action, onAction, error = false, timeout = 4000 } = {}) {
  if (!toastBox) {
    toastBox = document.createElement("div");
    toastBox.className = "toasts";
    toastBox.setAttribute("role", "status");
    document.body.appendChild(toastBox);
  }
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.innerHTML = `<span>${esc(message)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action;
    b.onclick = () => { onAction?.(); el.remove(); };
    el.appendChild(b);
  }
  toastBox.appendChild(el);
  setTimeout(() => el.remove(), timeout);
}
export const toastError = (e) => { if (e?.name !== "AbortError") toast(e?.message || String(e), { error: true, timeout: 6000 }); };

// ------------------------------------------------------------ dialogs (promise-based)

function modal(html, onMount) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = html;
    document.body.appendChild(back);
    icons(back);
    const close = (v) => { back.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(null); } };
    document.addEventListener("keydown", onKey, true);
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(null); });
    onMount(back, close);
  });
}

export function confirmDialog({ title, message, confirm = "Confirm", danger = false, details = "" }) {
  return modal(`
    <div class="modal" role="alertdialog" aria-modal="true">
      <div class="modal-head"><h2>${esc(title)}</h2></div>
      <div class="modal-body"><p>${esc(message)}</p>${details}</div>
      <div class="modal-foot">
        <button class="btn" data-a="cancel">Cancel</button>
        <button class="btn ${danger ? "btn-danger solid" : "btn-primary"}" data-a="ok">${esc(confirm)}</button>
      </div>
    </div>`, (back, close) => {
    back.querySelector('[data-a="cancel"]').onclick = () => close(false);
    const ok = back.querySelector('[data-a="ok"]');
    ok.onclick = () => close(true);
    ok.focus();
  });
}

export function promptDialog({ title, label = "", value = "", confirm = "Save", placeholder = "", multiline = false }) {
  const field = multiline
    ? `<textarea rows="4" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
    : `<input value="${esc(value)}" placeholder="${esc(placeholder)}">`;
  return modal(`
    <form class="modal" role="dialog" aria-modal="true">
      <div class="modal-head"><h2>${esc(title)}</h2></div>
      <div class="modal-body"><label>${esc(label)}${field}</label></div>
      <div class="modal-foot">
        <button class="btn" type="button" data-a="cancel">Cancel</button>
        <button class="btn btn-primary" type="submit">${esc(confirm)}</button>
      </div>
    </form>`, (back, close) => {
    const input = back.querySelector("input, textarea");
    input.focus();
    input.select?.();
    back.querySelector('[data-a="cancel"]').onclick = () => close(null);
    back.querySelector("form").onsubmit = (e) => { e.preventDefault(); close(input.value.trim()); };
    if (multiline) input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); close(input.value.trim()); } });
  });
}

/** Pick a destination folder (or root). Resolves to {id} or null on cancel. */
export function folderPicker({ title, folders, exclude = new Set(), confirm = "Move here" }) {
  const byParent = {};
  folders.forEach((f) => (byParent[f.parent_id ?? "root"] ||= []).push(f));
  const rows = [];
  const walk = (pid, depth) => (byParent[pid] || []).forEach((f) => {
    if (exclude.has(f.id)) return;
    rows.push(`<label class="check scope-node" style="--depth:${depth}"><input type="radio" name="dest" value="${f.id}"> ${icon("folder")} ${esc(f.name)}</label>`);
    walk(f.id, depth + 1);
  });
  walk("root", 1);
  return modal(`
    <form class="modal" role="dialog" aria-modal="true">
      <div class="modal-head"><h2>${esc(title)}</h2></div>
      <div class="modal-body"><div class="scope-tree" style="margin:0;max-height:50vh">
        <label class="check scope-node"><input type="radio" name="dest" value="" checked> ${icon("home")} Top level</label>
        ${rows.join("")}</div></div>
      <div class="modal-foot"><button class="btn" type="button" data-a="cancel">Cancel</button>
        <button class="btn btn-primary" type="submit">${esc(confirm)}</button></div>
    </form>`, (back, close) => {
    back.querySelector('[data-a="cancel"]').onclick = () => close(null);
    back.querySelector("form").onsubmit = (e) => {
      e.preventDefault();
      const v = back.querySelector('input[name="dest"]:checked').value;
      close({ id: v ? Number(v) : null });
    };
  });
}

/** Pick a table (used for "Move rows to table"). */
export function tablePicker({ title, tables, folders, exclude }) {
  const fmap = Object.fromEntries(folders.map((f) => [f.id, f]));
  const path = (fid) => { const out = []; while (fid && fmap[fid]) { out.unshift(fmap[fid].name); fid = fmap[fid].parent_id; } return out.join(" / "); };
  const opts = tables.filter((t) => t.id !== exclude && t.effective_active)
    .map((t) => ({ t, p: path(t.folder_id) })).sort((a, b) => (a.p + a.t.name).localeCompare(b.p + b.t.name))
    .map(({ t, p }) => `<option value="${t.id}">${esc(p ? p + " / " : "")}${esc(t.name)}</option>`).join("");
  return modal(`
    <form class="modal" role="dialog" aria-modal="true">
      <div class="modal-head"><h2>${esc(title)}</h2></div>
      <div class="modal-body"><label>Destination table<select>${opts}</select></label></div>
      <div class="modal-foot"><button class="btn" type="button" data-a="cancel">Cancel</button>
        <button class="btn btn-primary" type="submit">Move</button></div>
    </form>`, (back, close) => {
    back.querySelector('[data-a="cancel"]').onclick = () => close(null);
    back.querySelector("form").onsubmit = (e) => { e.preventDefault(); close(Number(back.querySelector("select").value)); };
  });
}

// ------------------------------------------------------------ context menu

let openMenu;
export function contextMenu(x, y, items) {
  openMenu?.remove();
  const m = document.createElement("div");
  m.className = "ctx";
  m.setAttribute("role", "menu");
  for (const it of items) {
    if (!it) continue;
    if (it === "-") { m.appendChild(document.createElement("hr")); continue; }
    const b = document.createElement("button");
    b.setAttribute("role", "menuitem");
    b.innerHTML = `${it.icon ? icon(it.icon) : ""}<span>${esc(it.label)}</span>`;
    if (it.danger) b.classList.add("danger");
    b.disabled = !!it.disabled;
    b.onclick = () => { m.remove(); openMenu = null; it.action(); };
    m.appendChild(b);
  }
  document.body.appendChild(m);
  icons(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 4)) + "px";
  m.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 4)) + "px";
  openMenu = m;
  m.querySelector("button:not(:disabled)")?.focus();
  const close = (e) => {
    if (e.type === "keydown" && e.key !== "Escape") return;
    if (e.type === "mousedown" && m.contains(e.target)) return;
    m.remove(); openMenu = null;
    document.removeEventListener("mousedown", close, true);
    document.removeEventListener("keydown", close, true);
  };
  setTimeout(() => {
    document.addEventListener("mousedown", close, true);
    document.addEventListener("keydown", close, true);
  });
}

export function pluralize(n, word, plural) { return `${n} ${n === 1 ? word : plural || word + "s"}`; }

export function impactText(i) {
  const parts = [];
  if (i.folders) parts.push(pluralize(i.folders, "folder"));
  if (i.tables) parts.push(pluralize(i.tables, "table"));
  parts.push(pluralize(i.items || 0, "item"));
  if (i.photos) parts.push(pluralize(i.photos, "photo"));
  return parts.join(", ");
}
