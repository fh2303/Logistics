const api = typeof browser !== "undefined" ? browser : chrome;

const API_BASE = "http://127.0.0.1:8000";
const TARGET = "netchb"; // NetCHB is the only filing target.

// Quick calls (list, create, delete) fail fast. Attaching a file runs Docling
// plus an LLM call, so it gets a much longer limit.
const QUICK_TIMEOUT_MS = 15000;
const ATTACH_TIMEOUT_MS = 180000;

const NETCHB_URL_PATTERN = "https://www.netchb.com/app/entry/*";

// Display order and labels for the NetCHB fields netchb_mapping.py produces.
const FIELD_LABELS = [
  ["imp", "Importer of Record"],
  ["ulc", "Ultimate Consignee"],
  ["mot", "Mode of Transportation"],
  ["scac0", "Master SCAC"],
  ["mb0", "Master B/L"],
  ["grw", "Gross Weight"],
  ["desc", "Description of Merchandise"],
  ["locGoods", "Location of Goods"],
  ["cCode", "Carrier Code"],
  ["vNo", "Voyage/Flight No"],
  ["dImp", "Date of Import"],
  ["eArrive", "Estimated Arrival Date"],
];

const MODE_NAMES = {
  10: "Vessel, non-container",
  11: "Vessel, container",
  40: "Air, non-container",
};

const CATEGORY_NAMES = {
  arrival_notice: "Arrival notice",
  bol: "BOL / AWB",
  isf: "ISF",
  invoice: "Commercial invoice",
  packing_list: "Packing list",
  other: "Other",
};

const $ = (id) => document.getElementById(id);

const shipmentSelect = $("shipmentSelect");
const deleteShipmentBtn = $("deleteShipmentBtn");
const newLabelInput = $("newLabel");
const createBtn = $("createBtn");
const shipmentStatus = $("shipmentStatus");

const attachSection = $("attachSection");
const fileInput = $("fileInput");
const modeSelect = $("modeSelect");
const attachBtn = $("attachBtn");
const attachStatus = $("attachStatus");
const uploadLog = $("uploadLog");

const documentsSection = $("documentsSection");
const documentsBody = $("documentsBody");
const documentsEmpty = $("documentsEmpty");

const reviewSection = $("reviewSection");
const warningsBox = $("warningsBox");
const warningsList = $("warningsList");
const conflictsBox = $("conflictsBox");
const conflictsList = $("conflictsList");
const fieldsBody = $("fieldsBody");
const fillBtn = $("fillBtn");
const fillStatus = $("fillStatus");

let currentShipmentId = null;
let currentView = null;
let busy = false;

// ---------------------------------------------------------------------------
// Small helpers. Everything extracted from a document is untrusted text, so
// the page is only ever built with textContent, never innerHTML.
// ---------------------------------------------------------------------------

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (key === "className") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child);
  }
  return node;
}

function setStatus(node, text, kind) {
  node.textContent = text;
  node.className = "status" + (kind ? " " + kind : "");
}

function prettyField(name) {
  const s = String(name).replace(/_/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatAdded(value) {
  if (!value) return "";
  // SQLite CURRENT_TIMESTAMP is UTC, "YYYY-MM-DD HH:MM:SS".
  const d = new Date(String(value).replace(" ", "T") + "Z");
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

function remember(id) {
  try {
    if (id === null) localStorage.removeItem("lastShipmentId");
    else localStorage.setItem("lastShipmentId", String(id));
  } catch (_) {
    /* storage can be unavailable; the page works without it */
  }
}

function recall() {
  try {
    const v = localStorage.getItem("lastShipmentId");
    return v ? Number(v) : null;
  } catch (_) {
    return null;
  }
}

async function apiFetch(path, options = {}, timeoutMs = QUICK_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(API_BASE + path, {
      ...options,
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(
        `The API did not answer within ${Math.round(timeoutMs / 1000)} seconds`,
      );
    }
    throw new Error(`Could not reach the API at ${API_BASE}. Is it running?`);
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    let detail = body && body.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function setBusy(value) {
  busy = value;
  shipmentSelect.disabled = value;
  createBtn.disabled = value;
  attachBtn.disabled = value;
  deleteShipmentBtn.disabled = value || currentShipmentId === null;
  for (const b of documentsBody.querySelectorAll("button")) b.disabled = value;
}

// ---------------------------------------------------------------------------
// Shipments: list, create, select, delete
// ---------------------------------------------------------------------------

async function loadShipments(selectId) {
  const data = await apiFetch("/shipments");
  const list = data.shipments || [];

  shipmentSelect.replaceChildren(
    el("option", {
      value: "",
      text: list.length ? "Select…" : "No shipments yet",
    }),
  );
  for (const s of list) {
    const n = s.document_count;
    shipmentSelect.append(
      el("option", {
        value: String(s.id),
        text: `${s.label} (${n} ${n === 1 ? "document" : "documents"})`,
      }),
    );
  }

  const wanted = list.some((s) => s.id === selectId) ? selectId : null;
  shipmentSelect.value = wanted === null ? "" : String(wanted);
  return wanted;
}

async function openShipment(id) {
  if (id === null) {
    currentShipmentId = null;
    currentView = null;
    remember(null);
    renderNothing();
    return;
  }
  const view = await apiFetch(`/shipments/${id}?target=${TARGET}`);
  currentShipmentId = id;
  remember(id);
  renderView(view);
}

shipmentSelect.addEventListener("change", async () => {
  if (busy) return;
  const id = shipmentSelect.value ? Number(shipmentSelect.value) : null;
  setStatus(shipmentStatus, "");
  setBusy(true);
  try {
    await openShipment(id);
  } catch (err) {
    setStatus(shipmentStatus, `Failed: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
});

createBtn.addEventListener("click", async () => {
  if (busy) return;
  const label = newLabelInput.value.trim();
  if (!label) {
    setStatus(shipmentStatus, "Give the shipment a name first.", "error");
    return;
  }
  setBusy(true);
  setStatus(shipmentStatus, "Creating…");
  try {
    const view = await apiFetch("/shipments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    newLabelInput.value = "";
    await loadShipments(view.shipment.id);
    currentShipmentId = view.shipment.id;
    remember(currentShipmentId);
    renderView(view);
    setStatus(shipmentStatus, `Created "${view.shipment.label}".`, "success");
  } catch (err) {
    setStatus(shipmentStatus, `Failed: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
});

newLabelInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") createBtn.click();
});

deleteShipmentBtn.addEventListener("click", async () => {
  if (busy || currentShipmentId === null) return;
  const label = currentView && currentView.shipment.label;
  const count = currentView ? currentView.documents.length : 0;
  const ok = confirm(
    `Delete "${label}" and its ${count} attached ${count === 1 ? "document" : "documents"}? This can't be undone.`,
  );
  if (!ok) return;

  setBusy(true);
  try {
    await apiFetch(`/shipments/${currentShipmentId}`, { method: "DELETE" });
    await loadShipments(null);
    await openShipment(null);
    setStatus(shipmentStatus, `Deleted "${label}".`, "success");
  } catch (err) {
    setStatus(shipmentStatus, `Failed: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
});

// ---------------------------------------------------------------------------
// Attaching documents
// ---------------------------------------------------------------------------

attachBtn.addEventListener("click", async () => {
  if (busy || currentShipmentId === null) return;
  const files = Array.from(fileInput.files);
  if (files.length === 0) {
    setStatus(attachStatus, "Choose at least one file first.", "error");
    return;
  }

  const shipmentId = currentShipmentId;
  const mode = modeSelect.value;
  uploadLog.replaceChildren();
  setBusy(true);

  let lastView = null;
  let attachedCount = 0;

  // One at a time: the server handles a single extraction at once anyway, and
  // each response is the shipment recomputed with that file included.
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    setStatus(
      attachStatus,
      `Extracting ${file.name} (${i + 1} of ${files.length})… this can take a while.`,
    );
    const item = el("li", { text: `${file.name}: working…` });
    uploadLog.append(item);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const data = await apiFetch(
        `/shipments/${shipmentId}/documents?mode=${encodeURIComponent(mode)}&target=${TARGET}`,
        { method: "POST", body: formData },
        ATTACH_TIMEOUT_MS,
      );
      lastView = data;
      if (data.attached) {
        attachedCount++;
        item.className = "ok";
        item.textContent = `${file.name}: attached${data.cached ? " (extraction reused from cache)" : ""}`;
      } else {
        item.className = "dup";
        item.textContent = `${file.name}: already attached to this shipment, skipped`;
      }
    } catch (err) {
      item.className = "fail";
      item.textContent = `${file.name}: failed — ${err.message}`;
    }
  }

  fileInput.value = "";
  if (lastView && currentShipmentId === shipmentId) renderView(lastView);
  try {
    await loadShipments(shipmentId); // refresh the document counts
  } catch (_) {
    /* the list is cosmetic here */
  }

  const failed =
    files.length - attachedCount - uploadLog.querySelectorAll(".dup").length;
  setStatus(
    attachStatus,
    failed > 0
      ? `${attachedCount} attached, ${failed} failed.`
      : `${attachedCount} attached.`,
    failed > 0 ? "error" : "success",
  );
  setBusy(false);
});

async function removeDocument(doc) {
  if (busy || currentShipmentId === null) return;
  if (!confirm(`Remove "${doc.name}" from this shipment?`)) return;
  setBusy(true);
  try {
    const view = await apiFetch(
      `/shipments/${currentShipmentId}/documents/${doc.id}?target=${TARGET}`,
      { method: "DELETE" },
    );
    renderView(view);
    await loadShipments(currentShipmentId);
    setStatus(attachStatus, `Removed ${doc.name}.`, "success");
  } catch (err) {
    setStatus(attachStatus, `Failed: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderNothing() {
  attachSection.hidden = true;
  documentsSection.hidden = true;
  reviewSection.hidden = true;
  deleteShipmentBtn.disabled = true;
  uploadLog.replaceChildren();
  setStatus(attachStatus, "");
  setStatus(fillStatus, "");
}

function renderView(view) {
  currentView = view;
  attachSection.hidden = false;
  documentsSection.hidden = false;
  reviewSection.hidden = false;
  deleteShipmentBtn.disabled = busy;

  renderDocuments(view.documents || []);
  renderWarnings(view.warnings || []);
  renderConflicts(view.conflicts || []);
  renderFields(view.netchb_fields || {});

  fillBtn.disabled = !hasFillableValues(view);
  setStatus(fillStatus, "");
}

function hasFillableValues(view) {
  return Object.values((view && view.netchb_fields) || {}).some(
    (v) => v !== "" && v !== null && v !== undefined,
  );
}

function renderDocuments(documents) {
  documentsBody.replaceChildren();
  documentsEmpty.hidden = documents.length > 0;

  for (const doc of documents) {
    const removeBtn = el("button", {
      className: "danger small",
      type: "button",
      text: "Remove",
    });
    removeBtn.disabled = busy;
    removeBtn.addEventListener("click", () => removeDocument(doc));

    documentsBody.append(
      el(
        "tr",
        null,
        el("td", { text: doc.name }),
        el(
          "td",
          null,
          el("span", {
            className: "tag",
            text: CATEGORY_NAMES[doc.category] || doc.category || "Unknown",
          }),
        ),
        el("td", { className: "muted", text: formatAdded(doc.added_at) }),
        el("td", null, removeBtn),
      ),
    );
  }
}

function renderWarnings(warnings) {
  warningsList.replaceChildren();
  warningsBox.hidden = warnings.length === 0;
  for (const w of warnings) {
    warningsList.append(
      el("li", { text: typeof w === "string" ? w : JSON.stringify(w) }),
    );
  }
}

function conflictHeading(conflict) {
  const kind =
    conflict.kind === "version_change"
      ? "differs between versions of the same document"
      : "differs between documents";
  return `${prettyField(conflict.field)} — ${kind}`;
}

function valueLine(prefix, entry) {
  return el(
    "div",
    { className: "line" },
    el("strong", { text: prefix }),
    ` ${entry.value} `,
    el("span", { className: "src", text: `(${entry.source})` }),
  );
}

function renderConflicts(conflicts) {
  conflictsList.replaceChildren();
  conflictsBox.hidden = conflicts.length === 0;

  for (const c of conflicts) {
    const block = el(
      "div",
      { className: "conflict" },
      el("div", { className: "field", text: conflictHeading(c) }),
      valueLine("Using:", c.chosen),
    );
    for (const other of c.others || []) {
      block.append(valueLine("Also:", other));
    }
    conflictsList.append(block);
  }
}

function describeValue(id, value) {
  if (id === "mot" && value) {
    const name = MODE_NAMES[value];
    return name ? `${value} — ${name}` : String(value);
  }
  return String(value);
}

function renderFields(fields) {
  fieldsBody.replaceChildren();

  const known = new Set(FIELD_LABELS.map(([id]) => id));
  const rows = FIELD_LABELS.filter(([id]) => id in fields);
  // Anything the mapping adds later still shows up, under its raw id.
  for (const id of Object.keys(fields)) {
    if (!known.has(id)) rows.push([id, id]);
  }

  for (const [id, label] of rows) {
    const value = fields[id];
    const empty = value === "" || value === null || value === undefined;
    fieldsBody.append(
      el(
        "tr",
        null,
        el("td", { text: label }),
        el("td", { className: "id", text: id }),
        el("td", {
          className: "value" + (empty ? " blank" : ""),
          text: empty ? "left blank" : describeValue(id, value),
        }),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Filling the NetCHB form
// ---------------------------------------------------------------------------

async function findNetchbTab() {
  const tabs = await api.tabs.query({ url: NETCHB_URL_PATTERN });
  if (tabs.length === 0) {
    throw new Error(
      "No NetCHB entry page found open. Open https://www.netchb.com/app/entry/ in another tab first.",
    );
  }
  // With several NetCHB tabs open, prefer the one showing in its window, then
  // the one used most recently, rather than whichever the browser lists first.
  const ranked = tabs
    .slice()
    .sort(
      (a, b) =>
        Number(b.active) - Number(a.active) ||
        (b.lastAccessed || 0) - (a.lastAccessed || 0),
    );
  return { tab: ranked[0], count: tabs.length };
}

fillBtn.addEventListener("click", async () => {
  const fields = currentView && currentView.netchb_fields;
  if (!fields) {
    setStatus(fillStatus, "Nothing to fill yet.", "error");
    return;
  }

  fillBtn.disabled = true;
  setStatus(fillStatus, "Looking for an open NetCHB tab…");

  try {
    const { tab, count } = await findNetchbTab();
    const response = await api.tabs.sendMessage(tab.id, {
      type: "fillFields",
      fields,
    });

    if (!response) {
      throw new Error(
        "The NetCHB tab didn't answer. Reload that tab and try again.",
      );
    }

    const parts = [];
    if (response.filled.length) parts.push(`filled ${response.filled.length}`);
    if (response.missing.length) {
      parts.push(
        `${response.missing.length} field id(s) not found on page: ${response.missing.join(", ")}`,
      );
    }
    if (response.skipped_empty.length) {
      parts.push(`${response.skipped_empty.length} left blank (no data)`);
    }

    let text = parts.join(" — ");
    const title = tab.title ? ` "${tab.title}"` : "";
    text += ` [tab${title}${count > 1 ? `, ${count} NetCHB tabs open` : ""}]`;

    // NetCHB only shows Master SCAC / Master B/L once the mode is an ocean
    // mode, so on a first fill these can be missing for that reason.
    const conditional = response.missing.filter(
      (id) => id === "scac0" || id === "mb0",
    );
    if (conditional.length) {
      text +=
        ". Master SCAC / B/L appear only after Mode of Transportation is set to an ocean mode: set it, then click Fill again.";
    }

    setStatus(fillStatus, text, response.missing.length ? "error" : "success");
  } catch (err) {
    setStatus(fillStatus, `Failed: ${err.message}`, "error");
  } finally {
    fillBtn.disabled = !hasFillableValues(currentView);
  }
});

// ---------------------------------------------------------------------------
// Start up
// ---------------------------------------------------------------------------

(async function init() {
  setBusy(true);
  renderNothing();
  try {
    const wanted = await loadShipments(recall());
    await openShipment(wanted);
  } catch (err) {
    shipmentSelect.replaceChildren(
      el("option", { value: "", text: "API not reachable" }),
    );
    setStatus(shipmentStatus, `Failed: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
})();
