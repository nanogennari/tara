// The signed-in user's table view preferences, stored on the server so they follow the user.
// Every setting has a small-screen (phone layout, <= 600 px) and a large-screen version:
//   prefs.grid[size]          -> default row height / text size for all tables
//   prefs.tables[id][size]    -> per-table: rowH, font, hidden, widths, order, showCreated, showUpdated
import { api, toastError } from "./util.js";

const boot = JSON.parse(document.getElementById("boot")?.textContent || "{}");
export const prefs = boot.user?.prefs || {};
prefs.grid ||= {};
prefs.tables ||= {};

export const DEFAULT_GRID = { small: { rowH: 44, font: 13 }, large: { rowH: 36, font: 13 } };
export const screenSize = () => (matchMedia("(max-width: 600px)").matches ? "small" : "large");

export const gridDefault = (size = screenSize()) => ({ ...DEFAULT_GRID[size], ...(prefs.grid[size] || {}) });
export const tablePrefs = (tid, size = screenSize()) => prefs.tables[String(tid)]?.[size] || {};

const pending = {};
const timers = {};

/** Update one table's prefs (current screen size) locally now; save to the server shortly after. */
export function saveTablePrefs(tid, patch, size = screenSize()) {
  const id = String(tid);
  const perTable = { ...(prefs.tables[id] || {}) };
  const cur = { ...(perTable[size] || {}) };
  for (const [k, v] of Object.entries(patch)) (v === null ? delete cur[k] : (cur[k] = v));
  if (Object.keys(cur).length) perTable[size] = cur;
  else delete perTable[size];
  if (Object.keys(perTable).length) prefs.tables[id] = perTable;
  else delete prefs.tables[id];

  const key = `${id}:${size}`;
  pending[key] = { ...(pending[key] || {}), ...patch };
  clearTimeout(timers[key]);
  timers[key] = setTimeout(async () => {
    const body = pending[key];
    delete pending[key];
    try { await api("PATCH", `/api/me/prefs/tables/${id}?size=${size}`, body); }
    catch (e) { toastError(e); }
  }, 500);
}

/** Make a row height / text size the default for this screen size (optionally for every table). */
export async function setGridDefault(view, applyEverywhere = false, size = screenSize()) {
  const saved = await api("PUT", `/api/me/prefs/grid?size=${size}`, { ...view, apply_everywhere: applyEverywhere });
  prefs.grid = saved.grid || {};
  prefs.tables = saved.tables || {};
  window.dispatchEvent(new CustomEvent("grid:view"));
}
