// Photo viewer modal with zoom (wheel / pinch / buttons), pan, prev/next, set-main, delete.
import { confirmDialog, del, icons, post, toastError } from "./util.js";

export function viewerComponent() {
  return {
    open: false,
    photos: [],
    index: 0,
    title: "",
    writable: false,
    onChange: null,
    zoom: 1,
    pz: null,

    init() {
      window.addEventListener("viewer:open", (e) => this.show(e.detail));
      window.addEventListener("keydown", (e) => {
        if (!this.open) return;
        if (e.key === "Escape") { e.stopPropagation(); this.close(); }
        else if (e.key === "ArrowRight") this.go(1);
        else if (e.key === "ArrowLeft") this.go(-1);
        else if (e.key === "+" || e.key === "=") this.zoomBy(1);
        else if (e.key === "-") this.zoomBy(-1);
        else if (e.key === "0") this.reset();
      }, true);
    },
    get photo() { return this.photos[this.index]; },

    show({ photos, index = 0, title = "", writable = false, onChange = null }) {
      this.photos = photos;
      this.index = Math.min(index, photos.length - 1);
      this.title = title;
      this.writable = writable;
      this.onChange = onChange;
      this.open = true;
      this.$nextTick(() => { icons(this.$root); this.setupZoom(); });
    },
    close() {
      this.open = false;
      this.pz?.destroy();
      this.pz = null;
    },
    go(d) {
      if (this.photos.length < 2) return;
      this.index = (this.index + d + this.photos.length) % this.photos.length;
      this.$nextTick(() => this.setupZoom());
    },
    setupZoom() {
      const img = this.$refs.img;
      if (!img || !window.Panzoom) return;
      this.pz?.destroy();
      this.pz = Panzoom(img, { maxScale: 8, minScale: 1, step: 0.35, cursor: "grab" });
      this.zoom = 1;
      img.addEventListener("panzoomchange", (e) => { this.zoom = e.detail.scale; });
      const stage = this.$refs.stage;
      stage.onwheel = (e) => { e.preventDefault(); this.pz.zoomWithWheel(e); };
      img.ondblclick = (e) => (this.zoom > 1.05 ? this.reset() : this.pz.zoomToPoint(2.5, e));
    },
    zoomBy(d) { if (this.pz) d > 0 ? this.pz.zoomIn() : this.pz.zoomOut(); },
    reset() { this.pz?.reset(); },

    async makeMain() {
      try { await post(`/api/photos/${this.photo.id}/main`); this.onChange?.(); this.close(); }
      catch (e) { toastError(e); }
    },
    async remove() {
      const ok = await confirmDialog({ title: "Delete this photo?", danger: true, confirm: "Delete", message: "The photo will be removed from this item." });
      if (!ok) return;
      try {
        await del(`/api/photos/${this.photo.id}`);
        this.photos.splice(this.index, 1);
        this.onChange?.();
        if (!this.photos.length) return this.close();
        this.index = Math.min(this.index, this.photos.length - 1);
        this.$nextTick(() => this.setupZoom());
      } catch (e) { toastError(e); }
    },
  };
}
