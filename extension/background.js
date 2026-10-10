// Firefox closes toolbar popups the instant a native file-picker dialog
// opens (a long-standing platform bug, not fixable from extension code).
// Workaround: no popup at all — clicking the icon opens the extraction
// UI as a normal tab instead, where file inputs work correctly.

const api = typeof browser !== "undefined" ? browser : chrome;

api.action.onClicked.addListener(() => {
  api.tabs.create({ url: api.runtime.getURL("extract.html") });
});
