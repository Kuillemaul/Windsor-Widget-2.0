(() => {
  const key = "windsor-widget-theme";
  const allowed = new Set(["windsor", "light", "dark"]);

  function applyTheme(theme) {
    const resolved = allowed.has(theme) ? theme : "windsor";
    document.documentElement.dataset.theme = resolved;
    document.querySelectorAll("[data-theme-picker]").forEach((picker) => {
      picker.value = resolved;
    });
    try { localStorage.setItem(key, resolved); } catch (_) { /* private mode */ }
  }

  let saved = "windsor";
  try { saved = localStorage.getItem(key) || "windsor"; } catch (_) { /* private mode */ }
  applyTheme(saved);

  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-picker]").forEach((picker) => {
      picker.addEventListener("change", (event) => applyTheme(event.target.value));
    });
  });
})();
