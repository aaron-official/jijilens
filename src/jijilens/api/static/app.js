// JijiLens page: upload a photo, show detected objects as boxes, search with the one tapped
// (or a box drawn by hand), and show matching listings that link to Jiji.
"use strict";

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";

const state = {
  photoId: null,
  width: 0, // photo size in the server's pixels; boxes use these
  height: 0,
  objects: [],
  box: null, // the box last searched; null = whole photo
  searchSeq: 0,
  searched: false,
};

// ---- upload -------------------------------------------------------------

$("file").addEventListener("change", (e) => e.target.files[0] && start(e.target.files[0]));
const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  const file = e.dataTransfer.files[0];
  if (file) start(file);
});
$("again").addEventListener("click", () => {
  $("file").value = "";
  $("file").click();
});

async function start(file) {
  Object.assign(state, { photoId: null, objects: [], box: null, searched: false });
  $("start").hidden = true;
  $("workspace").hidden = false;
  $("photo").src = URL.createObjectURL(file);
  $("overlay").replaceChildren();
  showEmpty("Tap a highlighted object, or drag on the photo to draw your own box.");
  setStatus("Uploading…", true);

  const form = new FormData();
  form.append("file", file);
  let photo;
  try {
    photo = await api("/api/photos", { method: "POST", body: form });
  } catch (err) {
    setStatus(`Upload failed: ${err.message}`);
    return;
  }
  Object.assign(state, { photoId: photo.photo_id, width: photo.width, height: photo.height });
  $("overlay").setAttribute("viewBox", `0 0 ${photo.width} ${photo.height}`);
  $("overlay").setAttribute("preserveAspectRatio", "none");
  detect(photo.photo_id);
}

// ---- detection ------------------------------------------------------------

async function detect(photoId) {
  setStatus("Finding objects… this takes a while on CPU. You can drag a box meanwhile.", true);
  let found;
  try {
    found = await api(`/api/photos/${photoId}/objects`, { method: "POST" });
  } catch (err) {
    if (photoId === state.photoId) setStatus("Drag on the photo to draw a box around what you want.");
    return;
  }
  if (photoId !== state.photoId) return; // a newer photo was uploaded meanwhile
  state.objects = found.objects;
  drawObjects();
  if (!found.objects.length) {
    setStatus("No objects found. Drag on the photo to draw a box.");
    return;
  }
  setStatus(`Found ${found.objects.length} object${found.objects.length > 1 ? "s" : ""}. Tap one, or drag to draw your own box.`);
  if (!state.searched) search(found.objects[0].box); // show something useful straight away
}

function drawObjects() {
  const svg = $("overlay");
  svg.querySelectorAll(".obj").forEach((g) => g.remove());
  const scale = displayScale(); // photo pixels per screen pixel, to keep labels a constant size
  state.objects.forEach((obj, i) => {
    const [x1, y1, x2, y2] = obj.box;
    const g = el("g", { class: "obj", "data-index": i, tabindex: 0, role: "button", "aria-label": `Search for this ${obj.label}` });
    g.append(el("rect", { class: "frame", x: x1, y: y1, width: x2 - x1, height: y2 - y1, rx: 6 * scale }));
    const label = obj.label;
    const tagW = (label.length * 7.4 + 14) * scale;
    const tagH = 20 * scale;
    const tagY = y1 - tagH >= 0 ? y1 - tagH : y1;
    g.append(el("rect", { class: "tag", x: x1, y: tagY, width: tagW, height: tagH, rx: 4 * scale }));
    const text = el("text", { x: x1 + 7 * scale, y: tagY + 14.5 * scale, "font-size": 13 * scale });
    text.textContent = label;
    g.append(text);
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); search(obj.box); }
    });
    svg.append(g);
  });
  markActive();
}

function markActive() {
  $("overlay").querySelectorAll(".obj").forEach((g) => {
    const box = state.objects[g.dataset.index].box;
    g.classList.toggle("active", !!state.box && box.every((v, i) => v === state.box[i]));
  });
}

window.addEventListener("resize", () => state.objects.length && drawObjects());

// ---- tap and draw ---------------------------------------------------------

let drag = null;

$("overlay").addEventListener("pointerdown", (e) => {
  if (!state.photoId) return;
  const target = e.target.closest(".obj");
  const p = toPhoto(e);
  drag = { x: p.x, y: p.y, sx: e.clientX, sy: e.clientY, target, rect: null };
  $("overlay").setPointerCapture(e.pointerId);
});

$("overlay").addEventListener("pointermove", (e) => {
  if (!drag) return;
  if (!drag.rect && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 8) return; // still a tap
  const p = toPhoto(e);
  if (!drag.rect) {
    drag.rect = el("rect", { class: "drawing" });
    $("overlay").append(drag.rect);
  }
  const box = norm(drag.x, drag.y, p.x, p.y);
  Object.entries({ x: box[0], y: box[1], width: box[2] - box[0], height: box[3] - box[1] }).forEach(([k, v]) => drag.rect.setAttribute(k, v));
});

$("overlay").addEventListener("pointerup", (e) => {
  if (!drag) return;
  const { rect, target } = drag;
  drag = null;
  if (rect) {
    const p = toPhoto(e);
    rect.remove();
    const box = norm(...[rect.x.baseVal.value, rect.y.baseVal.value].map(Math.round), Math.round(p.x), Math.round(p.y));
    const minSide = 16 * displayScale();
    if (box[2] - box[0] >= minSide && box[3] - box[1] >= minSide) search(box);
  } else if (target) {
    search(state.objects[target.dataset.index].box);
  }
});

$("overlay").addEventListener("pointercancel", () => {
  if (drag && drag.rect) drag.rect.remove();
  drag = null;
});

function toPhoto(e) {
  const r = $("overlay").getBoundingClientRect();
  return {
    x: clamp(((e.clientX - r.left) / r.width) * state.width, 0, state.width),
    y: clamp(((e.clientY - r.top) / r.height) * state.height, 0, state.height),
  };
}

function displayScale() {
  const w = $("overlay").getBoundingClientRect().width;
  return w ? state.width / w : 1;
}

// ---- search ---------------------------------------------------------------

$("whole").addEventListener("click", () => state.photoId && search(null));
$("category").addEventListener("change", () => state.searched && search(state.box));

async function search(box) {
  if (!state.photoId) return;
  state.box = box ? box.map(Math.round) : null;
  state.searched = true;
  markActive();
  drawCropPreview(state.box);
  const seq = ++state.searchSeq;
  showSkeletons();
  $("notice").hidden = true;

  let found;
  try {
    found = await api(`/api/photos/${state.photoId}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ box: state.box, category: $("category").value || null, k: 12 }),
    });
  } catch (err) {
    if (seq === state.searchSeq) showEmpty(`Search failed: ${err.message}`);
    return;
  }
  if (seq !== state.searchSeq) return; // a newer search started
  renderResults(found);
}

function renderResults({ results, close_match, threshold }) {
  const list = $("results");
  if (!results.length) {
    showEmpty("No listings found in this category.");
    return;
  }
  $("notice").hidden = close_match;
  $("notice").textContent =
    "No close match in the catalog. These are the nearest listings it has. It covers 14 kinds of product: furniture, TVs, speakers, phones, laptops, watches, shoes, bags and clothing.";
  list.replaceChildren(
    ...results.map((r) => {
      const close = threshold == null || r.similarity >= threshold;
      const card = el("a", { class: `card${close ? "" : " weak"}`, href: r.url, target: "_blank", rel: "noopener" }, document);
      const img = document.createElement("img");
      Object.assign(img, { className: "thumb", src: r.image_url, alt: "", loading: "lazy", referrerPolicy: "no-referrer" });
      const body = document.createElement("div");
      body.className = "body";
      body.append(
        textEl("span", "price", formatPrice(r)),
        textEl("span", "title", r.title),
      );
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.append(
        textEl("span", "", r.category.replace(/-/g, " ")),
        textEl("span", `score${close ? " close" : ""}`, `${Math.round(r.similarity * 100)}% match`),
      );
      body.append(meta);
      card.append(img, body);
      card.title = `${r.title}\nOpen on Jiji${r.region ? ` (${r.region})` : ""}`;
      return card;
    }),
  );
}

function showSkeletons() {
  $("results").replaceChildren(...Array.from({ length: 6 }, () => textEl("div", "skeleton", "")));
}

function showEmpty(message) {
  $("notice").hidden = true;
  $("results").replaceChildren(textEl("p", "empty", message));
  $("crop-preview").hidden = true;
}

function drawCropPreview(box) {
  const canvas = $("crop-preview");
  const img = $("photo");
  if (!img.naturalWidth) return;
  const k = img.naturalWidth / state.width; // the displayed file may be larger than the server's copy
  const [x1, y1, x2, y2] = box || [0, 0, state.width, state.height];
  const w = (x2 - x1) * k;
  const h = (y2 - y1) * k;
  const side = Math.max(w, h);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const s = canvas.width / side;
  ctx.drawImage(img, x1 * k, y1 * k, w, h, (canvas.width - w * s) / 2, (canvas.height - h * s) / 2, w * s, h * s);
  canvas.hidden = false;
}

// ---- helpers --------------------------------------------------------------

async function api(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(typeof detail === "string" ? detail : res.statusText);
  }
  return res.json();
}

function setStatus(message, busy = false) {
  const status = $("status");
  status.replaceChildren();
  if (busy) status.append(textEl("span", "spin", ""));
  status.append(document.createTextNode(message));
}

function formatPrice(r) {
  if (r.price_ugx) return `UGX ${Number(r.price_ugx).toLocaleString("en-US")}`;
  return r.price_text || "Price on request";
}

function el(tag, attrs = {}, doc) {
  const node = doc ? doc.createElement(tag) : document.createElementNS(SVG, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function textEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const norm = (ax, ay, bx, by) => [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];

// ---- categories -----------------------------------------------------------

api("/api/categories")
  .then((cats) => cats.forEach((c) => $("category").append(new Option(c.label, c.key))))
  .catch(() => {});
