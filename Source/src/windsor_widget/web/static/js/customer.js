(() => {
  const interactiveSelector = "a, button, input, select, textarea, label";

  function openRow(row) {
    const href = row?.dataset?.rowHref;
    if (href) window.location.assign(href);
  }

  document.addEventListener("dblclick", (event) => {
    if (event.target.closest(interactiveSelector)) return;
    openRow(event.target.closest("tr[data-row-href]"));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    if (event.target.closest(interactiveSelector)) return;
    const row = event.target.closest("tr[data-row-href]");
    if (row) {
      event.preventDefault();
      openRow(row);
    }
  });
})();
