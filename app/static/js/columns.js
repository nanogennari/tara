// Add / edit a custom column (field) of a table. Used by the table view and guided add.
import { esc, patch, post, toastError } from "./util.js";

export const TYPE_LABELS = { text: "Text", longtext: "Long text", number: "Number", date: "Date",
  checkbox: "Checkbox", select: "Choice list", url: "Link" };

/** Opens the dialog; resolves to the saved column, or null if cancelled. */
export function columnDialog(tableId, existing = null) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-back dialog";
    const opts = Object.entries(TYPE_LABELS).map(([k, v]) => `<option value="${k}" ${existing?.type === k ? "selected" : ""}>${v}</option>`).join("");
    back.innerHTML = `
      <form class="modal">
        <div class="modal-head"><h2>${existing ? "Edit column" : "Add column"}</h2></div>
        <div class="modal-body">
          <label>Name<input name="label" required value="${esc(existing?.label || "")}" placeholder="e.g. Expiry, Brand, Size"></label>
          <label>Type<select name="type">${opts}</select></label>
          <label data-opts hidden>Choices (one per line)<textarea name="options" rows="4">${esc((existing?.options || []).join("\n"))}</textarea></label>
          ${existing ? '<p class="hint">Changing the type converts existing values; values that don\'t fit become empty.</p>' : ""}
        </div>
        <div class="modal-foot"><button type="button" class="btn" data-cancel>Cancel</button><button class="btn btn-primary">${existing ? "Save" : "Add column"}</button></div>
      </form>`;
    document.body.appendChild(back);
    const form = back.querySelector("form");
    const sync = () => { back.querySelector("[data-opts]").hidden = form.type.value !== "select"; };
    form.type.addEventListener("change", sync);
    sync();
    form.label.focus();
    const close = (col = null) => { back.remove(); resolve(col); };
    back.querySelector("[data-cancel]").onclick = () => close();
    back.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    form.onsubmit = async (e) => {
      e.preventDefault();
      const body = { label: form.label.value.trim(), type: form.type.value,
        options: form.options.value.split("\n").map((s) => s.trim()).filter(Boolean) };
      try {
        const col = existing
          ? await patch(`/api/tables/${tableId}/columns/${encodeURIComponent(existing.key)}`, body)
          : await post(`/api/tables/${tableId}/columns`, body);
        close(col);
      } catch (err) { toastError(err); }
    };
  });
}
