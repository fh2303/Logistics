// Runs only on https://www.netchb.com/app/entry/* (see manifest.json).
// Listens for a "fillFields" message containing {id: value} pairs
// (the exact output shape of netchb_mapping.py's map_to_netchb_fields)
// and writes each value into the matching form field.

const api = typeof browser !== "undefined" ? browser : chrome;

// Sets a value in a way that works for BOTH plain HTML inputs and
// framework-controlled ones (React/Angular/Vue). Plain inputs only
// need .value = ...; controlled ones need the native setter + a
// dispatched event so the framework's own state updates. This does
// both every time, since doing so is harmless on plain inputs.
function setNativeValue(element, value) {
  const proto = Object.getPrototypeOf(element);
  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");

  if (descriptor && descriptor.set) {
    descriptor.set.call(element, value);
  } else {
    element.value = value;
  }

  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function fillFields(fields) {
  const results = { filled: [], missing: [], skipped_empty: [] };

  for (const [id, value] of Object.entries(fields)) {
    if (value === "" || value === null || value === undefined) {
      results.skipped_empty.push(id);
      continue;
    }

    const el = document.getElementById(id);
    if (!el) {
      // Field not found on the page — likely a wrong id, a page
      // layout change, or a field that's conditionally hidden
      // (e.g. Master SCAC/B/L only show for certain transport modes).
      results.missing.push(id);
      continue;
    }

    setNativeValue(el, value);
    results.filled.push(id);
  }

  return results;
}

api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "fillFields") {
    const results = fillFields(message.fields);
    sendResponse(results);
  }
  return true; // keep the message channel open for the async response
});
