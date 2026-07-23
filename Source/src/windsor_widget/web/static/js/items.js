document.addEventListener("DOMContentLoaded", () => {
  const selectAll = document.querySelector("[data-policy-select-all]");
  const boxes = Array.from(document.querySelectorAll("[data-policy-select]"));
  const countLabel = document.querySelector("[data-policy-selection-count]");
  const submitButton = document.querySelector("[data-bulk-policy-submit]");
  const form = document.querySelector("#bulk-policy-form");

  if (!boxes.length || !countLabel || !submitButton || !form) {
    return;
  }

  const refresh = () => {
    const selected = boxes.filter((box) => box.checked).length;
    countLabel.textContent = `${selected} item${selected === 1 ? "" : "s"} selected`;
    submitButton.disabled = selected === 0;

    if (selectAll) {
      selectAll.checked = selected === boxes.length;
      selectAll.indeterminate = selected > 0 && selected < boxes.length;
    }
  };

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      boxes.forEach((box) => {
        box.checked = selectAll.checked;
      });
      refresh();
    });
  }

  boxes.forEach((box) => box.addEventListener("change", refresh));

  form.addEventListener("submit", (event) => {
    const selected = boxes.filter((box) => box.checked).length;
    if (!selected) {
      event.preventDefault();
      return;
    }

    const policySelect = form.querySelector('select[name="policy"]');
    const policyLabel =
      policySelect && policySelect.selectedOptions.length
        ? policySelect.selectedOptions[0].textContent
        : "the selected policy";

    if (
      !window.confirm(
        `Change ${selected} selected item${selected === 1 ? "" : "s"} to ${policyLabel}?`
      )
    ) {
      event.preventDefault();
    }
  });

  refresh();
});
