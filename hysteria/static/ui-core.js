// Shared mechanics only: pages own polling schedules, state and DOM updates.
(function (root) {
  "use strict";

  function formatBytes(value, compactZero) {
    var bytes = Math.max(0, Number(value) || 0);
    if (!bytes && compactZero) return "0 B";
    var units = ["B", "KB", "MB", "GB", "TB"], index = 0;
    while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index++; }
    return bytes.toFixed(2) + " " + units[index];
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character];
    });
  }

  async function fetchWithTimeout(url, options, timeoutMs, controller) {
    if (controller === undefined) {
      controller = typeof AbortController === "function" ? new AbortController() : null;
    }
    var requestOptions = Object.assign({}, options || {});
    if (controller) requestOptions.signal = controller.signal;
    var timer;
    var timeoutError = new Error("request timeout");
    timeoutError.code = "timeout";
    var timeout = new Promise(function (_resolve, reject) {
      timer = setTimeout(function () {
        reject(timeoutError);
        if (controller) controller.abort();
      }, timeoutMs);
    });
    try {
      return await Promise.race([root.fetch(url, requestOptions), timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  root.Hy2UI = Object.freeze({ formatBytes: formatBytes, escapeHtml: escapeHtml, fetchWithTimeout: fetchWithTimeout });
})(globalThis);
