const api = typeof browser !== "undefined" ? browser : chrome;

const API_BASE = "http://127.0.0.1:8000";

const fileInput = document.getElementById("fileInput");
const modeSelect = document.getElementById("modeSelect");
const targetSelect = document.getElementById("targetSelect");
const extractBtn = document.getElementById("extractBtn");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const fillBtn = document.getElementById("fillBtn");
const fillStatusEl = document.getElementById("fillStatus");

let lastResult = null;

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

extractBtn.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    setStatus("Choose a file first.", "error");
    return;
  }

  const mode = modeSelect.value;
  const target = targetSelect.value;

  extractBtn.disabled = true;
  resultEl.style.display = "none";
  setStatus("Extracting… this can take a few seconds.");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const url = `${API_BASE}/extract?mode=${mode}&target=${target}`;
    const response = await fetch(url, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => null);
      throw new Error(
        errorBody?.detail || `Request failed (${response.status})`,
      );
    }

    const data = await response.json();
    lastResult = data;

    setStatus(data.cached ? "Done (served from cache)." : "Done.", "success");
    resultEl.textContent = JSON.stringify(data, null, 2);
    resultEl.style.display = "block";

    if (data.netchb_fields) {
      fillBtn.style.display = "block";
      fillStatusEl.textContent = "";
    }
  } catch (err) {
    setStatus(
      `Failed: ${err.message}. Is the API running at ${API_BASE}?`,
      "error",
    );
  } finally {
    extractBtn.disabled = false;
  }
});

fillBtn.addEventListener("click", async () => {
  if (!lastResult || !lastResult.netchb_fields) {
    fillStatusEl.textContent =
      "No NetCHB fields to fill — run Extract with target=netchb first.";
    fillStatusEl.style.color = "var(--error)";
    return;
  }

  fillBtn.disabled = true;
  fillStatusEl.textContent = "Looking for an open NetCHB tab…";
  fillStatusEl.style.color = "var(--muted)";

  try {
    const tabs = await api.tabs.query({
      url: "https://www.netchb.com/app/entry/*",
    });

    if (tabs.length === 0) {
      throw new Error(
        "No NetCHB entry page found open. Open https://www.netchb.com/app/entry/ in another tab first.",
      );
    }

    const targetTab = tabs[0];
    const response = await api.tabs.sendMessage(targetTab.id, {
      type: "fillFields",
      fields: lastResult.netchb_fields,
    });

    const parts = [];
    if (response.filled.length) parts.push(`filled ${response.filled.length}`);
    if (response.missing.length)
      parts.push(
        `${response.missing.length} field id(s) not found on page: ${response.missing.join(", ")}`,
      );
    if (response.skipped_empty.length)
      parts.push(
        `${response.skipped_empty.length} left blank (no data extracted)`,
      );

    fillStatusEl.textContent = parts.join(" — ");
    fillStatusEl.style.color = response.missing.length
      ? "var(--error)"
      : "var(--success)";
  } catch (err) {
    fillStatusEl.textContent = `Failed: ${err.message}`;
    fillStatusEl.style.color = "var(--error)";
  } finally {
    fillBtn.disabled = false;
  }
});
