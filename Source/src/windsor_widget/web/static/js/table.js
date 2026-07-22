(() => {
  function numberValue(cell) {
    const raw = cell.dataset.value || cell.textContent || "0";
    const parsed = Number(String(raw).replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function textValue(cell) {
    return (cell.dataset.value || cell.textContent || "").trim().toLocaleLowerCase();
  }

  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-sortable-table]").forEach((table) => {
      const headers = table.querySelectorAll("thead th[data-sort]");
      const body = table.querySelector("tbody");
      headers.forEach((header, index) => {
        header.addEventListener("click", () => {
          const next = header.dataset.sortDirection === "asc" ? "desc" : "asc";
          headers.forEach((candidate) => delete candidate.dataset.sortDirection);
          header.dataset.sortDirection = next;
          const mode = header.dataset.sort;
          const rows = Array.from(body.querySelectorAll("tr"));
          rows.sort((left, right) => {
            const a = left.children[index];
            const b = right.children[index];
            const first = mode === "number" ? numberValue(a) : textValue(a);
            const second = mode === "number" ? numberValue(b) : textValue(b);
            if (first < second) return next === "asc" ? -1 : 1;
            if (first > second) return next === "asc" ? 1 : -1;
            return 0;
          });
          rows.forEach((row) => body.appendChild(row));
        });
      });
    });
  });
})();
