// Sidebar folder tree: rendering, multi-select, drag & drop, context menus, bulk actions.
import { ageDays, confirmDialog, fmtDateTime, contextMenu, esc, folderPicker, icon, icons, impactText, patch, post,
  promptDialog, relTime, toast, toastError } from "./util.js";

export function renderTree(root, s) {
  if (!root) return;
  const byParent = {};
  const showArch = s.showArchived;
  for (const f of s.folders) if (showArch || f.effective_active) (byParent[`f${f.parent_id ?? "root"}`] ||= []).push(f);
  const tablesBy = {};
  for (const t of s.tables) if (showArch || t.effective_active) (tablesBy[t.folder_id ?? "root"] ||= []).push(t);

  const activeTab = s.tabs.find((t) => t.key === s.activeKey);
  const order = []; // visible node order for shift-range selection
  const out = [];

  const walk = (parentKey, depth) => {
    for (const f of byParent[`f${parentKey}`] || []) {
      const open = s.expanded.has(f.id);
      const hasKids = (byParent[`f${f.id}`] || []).length || (tablesBy[f.id] || []).length;
      const cls = ["node", "folder", open && "open", !f.effective_active && "archived",
        s.sel.folders.has(f.id) && "selected", activeTab?.type === "folder" && activeTab.id === f.id && "active"].filter(Boolean).join(" ");
      order.push({ kind: "folder", id: f.id });
      out.push(`<div class="${cls}" style="--depth:${depth}" data-kind="folder" data-id="${f.id}" draggable="true"
          role="treeitem" aria-expanded="${open}" tabindex="-1" title="${esc(f.name)}${f.content_updated_at ? "\nLast update: " + fmtDateTime(f.content_updated_at) : ""}">
        <span class="twisty">${hasKids ? icon("chevron-right") : ""}</span>
        <span class="icon">${icon(open ? "folder-open" : f.effective_active ? "folder" : "archive")}</span>
        <span class="name">${esc(f.name)}</span>
        ${dateMeta(f.content_updated_at, s.staleDays)}
        <button class="btn btn-ghost btn-icon btn-sm more" data-more aria-label="Folder actions">${icon("more-horizontal")}</button>
      </div>`);
      if (open) walk(f.id, depth + 1);
      if (open) tablesOf(f.id, depth + 1);
    }
  };
  const tablesOf = (fid, depth) => {
    for (const t of tablesBy[fid] || []) {
      const cls = ["node", "table", !t.effective_active && "archived", s.sel.tables.has(t.id) && "selected",
        activeTab?.type === "table" && activeTab.id === t.id && "active"].filter(Boolean).join(" ");
      order.push({ kind: "table", id: t.id });
      out.push(`<div class="${cls}" style="--depth:${depth}" data-kind="table" data-id="${t.id}" draggable="true" role="treeitem" tabindex="-1"
          title="${esc(t.name)} · ${t.item_count} items\nLast update: ${fmtDateTime(t.content_updated_at)}${t.content_updated_by ? " by " + esc(t.content_updated_by) : ""}">
        <span class="twisty"></span>
        <span class="icon">${icon(t.effective_active ? "table-2" : "archive")}</span>
        <span class="name">${esc(t.name)}</span>
        ${dateMeta(t.content_updated_at, s.staleDays)}
        <button class="btn btn-ghost btn-icon btn-sm more" data-more aria-label="Table actions">${icon("more-horizontal")}</button>
      </div>`);
    }
  };
  walk("root", 0);
  tablesOf("root", 0);

  root.innerHTML = out.length
    ? out.join("") + `<div class="tree-root-drop" data-root-drop></div>`
    : `<div class="tree-empty">${s.loaded ? "No folders or tables yet." : '<span class="spinner"></span>'}</div>
       ${s.user.can_edit && s.loaded ? `<div class="center"><button class="btn btn-sm" data-act="new-table">${icon("plus")} New table</button></div>` : ""}`;
  icons(root);
  root._order = order;
  if (!root._wired) wire(root, s);
}

function dateMeta(iso, staleDays) {
  if (!iso) return "";
  const stale = ageDays(iso) > staleDays;
  return `<span class="meta${stale ? " stale" : ""}">${relTime(iso, true)}</span>`;
}

function wire(root, s) {
  root._wired = true;

  root.addEventListener("click", (e) => {
    if (e.target.closest('[data-act="new-table"]')) return s.newTable(null);
    const node = e.target.closest(".node");
    if (!node) {
      if (s.selCount) s.clearSelection();
      return;
    }
    const kind = node.dataset.kind;
    const id = Number(node.dataset.id);
    if (e.target.closest("[data-more]")) {
      const r = e.target.closest("[data-more]").getBoundingClientRect();
      return showMenu(s, kind, id, r.left, r.bottom);
    }
    if (e.ctrlKey || e.metaKey) return toggleSel(s, kind, id);
    if (e.shiftKey && s.sel.anchor) return rangeSel(s, root, kind, id);
    if (s.selCount) s.clearSelection();
    if (kind === "folder") {
      if (e.target.closest(".twisty")) return s.toggleExpanded(id);
      s.toggleExpanded(id, true);
      s.openFolder(id);
    } else {
      s.openTable(id);
    }
  });

  root.addEventListener("dblclick", (e) => {
    const node = e.target.closest(".node.folder");
    if (node && !e.target.closest("[data-more]")) s.toggleExpanded(Number(node.dataset.id), false);
  });

  root.addEventListener("contextmenu", (e) => {
    const node = e.target.closest(".node");
    e.preventDefault();
    if (!node) {
      if (!s.user.can_edit) return;
      return contextMenu(e.clientX, e.clientY, [
        { label: "New folder", icon: "folder-plus", action: () => s.newFolder(null) },
        { label: "New table", icon: "table-2", action: () => s.newTable(null) },
      ]);
    }
    const kind = node.dataset.kind, id = Number(node.dataset.id);
    const inSel = kind === "folder" ? s.sel.folders.has(id) : s.sel.tables.has(id);
    if (s.selCount > 1 && inSel) return bulkMenu(s, e.clientX, e.clientY);
    showMenu(s, kind, id, e.clientX, e.clientY);
  });

  // Keyboard: Delete removes selection, Escape clears it
  root.tabIndex = 0;
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && s.selCount) s.clearSelection();
    if ((e.key === "Delete" || e.key === "Backspace") && s.selCount && s.user.can_edit) bulk(s, "delete");
  });

  // ---- drag & drop (move folders/tables; multi-selection moves together)
  let dragging = null;
  root.addEventListener("dragstart", (e) => {
    const node = e.target.closest(".node");
    if (!node || !s.user.can_edit) return e.preventDefault();
    const kind = node.dataset.kind, id = Number(node.dataset.id);
    const inSel = kind === "folder" ? s.sel.folders.has(id) : s.sel.tables.has(id);
    dragging = inSel ? { folders: [...s.sel.folders], tables: [...s.sel.tables] }
      : { folders: kind === "folder" ? [id] : [], tables: kind === "table" ? [id] : [] };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", "inventory-node");
  });
  root.addEventListener("dragover", (e) => {
    if (!dragging) return;
    const target = e.target.closest(".node.folder") || e.target.closest("[data-root-drop]");
    root.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
    if (!target) return;
    if (target.dataset.id && dragging.folders.includes(Number(target.dataset.id))) return;
    e.preventDefault();
    target.classList.add("drop-target");
  });
  root.addEventListener("dragleave", (e) => e.target.closest?.(".drop-target")?.classList.remove("drop-target"));
  root.addEventListener("drop", async (e) => {
    const target = e.target.closest(".node.folder") || e.target.closest("[data-root-drop]");
    root.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
    if (!dragging || !target) return;
    e.preventDefault();
    const dest = target.dataset.id ? Number(target.dataset.id) : null;
    const payload = { ...dragging, action: "move", target_folder_id: dest };
    dragging = null;
    try {
      await post("/api/nodes/bulk", payload);
      if (dest) s.expanded.add(dest);
      s.clearSelection();
      await s.loadTree();
    } catch (err) { toastError(err); }
  });
  root.addEventListener("dragend", () => { dragging = null; });
}

function toggleSel(s, kind, id) {
  const set = kind === "folder" ? s.sel.folders : s.sel.tables;
  set.has(id) ? set.delete(id) : set.add(id);
  s.sel.anchor = { kind, id };
  s.updateSelCount();
  s.redrawTree();
}

function rangeSel(s, root, kind, id) {
  const order = root._order || [];
  const idx = (n) => order.findIndex((o) => o.kind === n.kind && o.id === n.id);
  let a = idx(s.sel.anchor), b = idx({ kind, id });
  if (a < 0 || b < 0) return toggleSel(s, kind, id);
  if (a > b) [a, b] = [b, a];
  for (const o of order.slice(a, b + 1)) (o.kind === "folder" ? s.sel.folders : s.sel.tables).add(o.id);
  s.updateSelCount();
  s.redrawTree();
}

function showMenu(s, kind, id, x, y) {
  const ed = s.user.can_edit;
  if (kind === "folder") {
    const f = s.folder(id);
    return contextMenu(x, y, [
      { label: "Open folder view", icon: "layout-list", action: () => s.openFolder(id) },
      { label: "See all items", icon: "list", action: () => s.openFolderItems(id) },
      ed && "-",
      ed && { label: "Guided add here…", icon: "scan-line", action: () => window.dispatchEvent(new CustomEvent("guided:open", { detail: { folderId: id } })) },
      ed && { label: "New table here", icon: "table-2", action: () => s.newTable(id) },
      ed && { label: "New subfolder", icon: "folder-plus", action: () => s.newFolder(id) },
      ed && { label: "Import XLSX here…", icon: "file-spreadsheet", action: () => window.dispatchEvent(new CustomEvent("import:open", { detail: { folderId: id } })) },
      "-",
      ed && { label: "Rename", icon: "pencil", action: () => rename(s, "folder", id, f.name) },
      ed && { label: "Move to…", icon: "folder-input", action: () => moveTo(s, { folders: [id], tables: [] }) },
      ed && { label: f.active ? "Archive (inactive)" : "Reactivate", icon: f.active ? "archive" : "archive-restore",
        action: () => setActive(s, { folders: [id], tables: [] }, !f.active) },
      { label: "Copy link", icon: "link", action: () => s.copyLink(`/f/${id}`) },
      { label: "Search in this folder", icon: "search", action: () => window.dispatchEvent(new CustomEvent("palette:open", { detail: { folders: [id] } })) },
      { label: "Export XLSX", icon: "download", action: () => { location.href = `/api/export/xlsx?folder_id=${id}`; } },
      ed && "-",
      ed && { label: "Delete…", icon: "trash-2", danger: true, action: () => bulk(s, "delete", { folders: [id], tables: [] }) },
    ]);
  }
  const t = s.table(id);
  contextMenu(x, y, [
    { label: "Open", icon: "table-2", action: () => s.openTable(id) },
    ed && "-",
    ed && t.effective_active && { label: "Guided add into this…", icon: "scan-line", action: () => window.dispatchEvent(new CustomEvent("guided:open", { detail: { tableId: id } })) },
    ed && { label: "Rename", icon: "pencil", action: () => rename(s, "table", id, t.name) },
    ed && { label: "Move to…", icon: "folder-input", action: () => moveTo(s, { folders: [], tables: [id] }) },
    ed && { label: t.active ? "Archive (inactive)" : "Reactivate", icon: t.active ? "archive" : "archive-restore",
      action: () => setActive(s, { folders: [], tables: [id] }, !t.active) },
    { label: "Copy link", icon: "link", action: () => s.copyLink(`/t/${id}`) },
    { label: "Export XLSX", icon: "download", action: () => { location.href = `/api/export/xlsx?tables=${id}`; } },
    { label: "Export CSV", icon: "file-text", action: () => { location.href = `/api/tables/${id}/export.csv`; } },
    ed && "-",
    ed && { label: "Delete…", icon: "trash-2", danger: true, action: () => bulk(s, "delete", { folders: [], tables: [id] }) },
  ]);
}

function bulkMenu(s, x, y) {
  const ed = s.user.can_edit;
  contextMenu(x, y, [
    ed && { label: `Move ${s.selCount} to…`, icon: "folder-input", action: () => bulk(s, "move") },
    ed && { label: "Archive", icon: "archive", action: () => bulk(s, "archive") },
    ed && { label: "Reactivate", icon: "archive-restore", action: () => bulk(s, "reactivate") },
    { label: "Export XLSX", icon: "download", action: () => { location.href = `/api/export/xlsx?tables=${[...s.sel.tables].join(",")}`; }, disabled: !s.sel.tables.size },
    ed && "-",
    ed && { label: `Delete ${s.selCount}…`, icon: "trash-2", danger: true, action: () => bulk(s, "delete") },
    "-",
    { label: "Clear selection", icon: "x", action: () => s.clearSelection() },
  ]);
}

async function rename(s, kind, id, current) {
  const name = await promptDialog({ title: `Rename ${kind}`, label: "Name", value: current });
  if (!name || name === current) return;
  try {
    await patch(`/api/${kind === "folder" ? "folders" : "tables"}/${id}`, { name });
    await s.loadTree();
    s.refreshViews((k) => k === `${kind}:${id}`);
  } catch (e) { toastError(e); }
}

async function moveTo(s, nodes) {
  const exclude = new Set(nodes.folders);
  const dest = await folderPicker({ title: "Move to folder", folders: s.folders, exclude });
  if (!dest) return;
  try {
    await post("/api/nodes/bulk", { ...nodes, action: "move", target_folder_id: dest.id });
    if (dest.id) s.expanded.add(dest.id);
    s.clearSelection();
    await s.loadTree();
  } catch (e) { toastError(e); }
}

async function setActive(s, nodes, active) {
  try {
    await post("/api/nodes/bulk", { ...nodes, action: active ? "reactivate" : "archive" });
    await s.loadTree();
    s.refreshViews();
    toast(active ? "Reactivated" : "Archived — hidden from search, read-only");
  } catch (e) { toastError(e); }
}

/** Bulk action on the selection (or on explicit nodes). Exported for the sidebar bulk bar. */
export async function bulk(s, action, nodes) {
  nodes ||= { folders: [...s.sel.folders], tables: [...s.sel.tables] };
  if (!nodes.folders.length && !nodes.tables.length) return;
  if (action === "move") return moveTo(s, nodes);
  if (action === "archive" || action === "reactivate") { await setActive(s, nodes, action === "reactivate"); return s.clearSelection(); }
  if (action !== "delete") return;
  try {
    const impact = await post("/api/nodes/impact", nodes);
    const ok = await confirmDialog({
      title: "Move to trash?", danger: true, confirm: "Move to trash",
      message: `This will move ${impactText(impact)} to the trash.`,
      details: `<p class="muted small">You can undo right after, or restore from the Trash within the retention period.</p>`,
    });
    if (!ok) return;
    const res = await post("/api/nodes/bulk", { ...nodes, action: "delete" });
    s.clearSelection();
    await s.loadTree();
    toast(`Moved ${impactText(res)} to trash`, {
      action: "Undo", timeout: 10000,
      onAction: async () => {
        try { await post(`/api/trash/${res.batch}/restore`); await s.loadTree(); toast("Restored"); }
        catch (e) { toastError(e); }
      },
    });
    s.refreshViews((k) => k === "trash");
  } catch (e) { toastError(e); }
}
