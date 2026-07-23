(() => {
  const KEY = "windsor.priceFileRoot.v1";
  const DEFAULT_ROOT =
    "C:\\Users\\WindsorTradingInfo\\WINDSOR TRADING CO TRUST\\" +
    "Windsor Trading - Documents\\data\\Customer Prices";

  let dialog;
  let input;
  let status;
  let queuedPath = "";

  const tidyRoot = (value) => String(value || "").trim().replace(/[\\/]+$/, "");
  const tidyRelative = (value) => String(value || "").trim().replace(/^[\\/]+/, "");
  const currentRoot = () => tidyRoot(localStorage.getItem(KEY) || "");

  function fullPath(relative) {
    return `${currentRoot()}\\${tidyRelative(relative)}`;
  }

  function fileUri(path) {
    const normal = path.replace(/\\/g, "/");
    const encoded = normal
      .split("/")
      .map((part, index) => (index === 0 ? part : encodeURIComponent(part)))
      .join("/");
    return `file:///${encoded}`;
  }

  function buildDialog() {
    dialog = document.createElement("dialog");
    dialog.className = "price-root-dialog";
    dialog.innerHTML = `
      <form method="dialog" class="price-root-form">
        <div>
          <p class="eyebrow">This computer</p>
          <h2>Customer Prices folder</h2>
          <p class="price-root-help">
            Stored only in this browser. It does not change another user's computer.
          </p>
        </div>
        <label><span>Local OneDrive folder</span>
          <input type="text" data-price-root-input autocomplete="off">
        </label>
        <div class="price-root-examples">
          <code>...\\Windsor Trading - Documents\\data\\Customer Prices</code>
          <code>...\\Windsor Trading - Documents (1)\\data\\Customer Prices</code>
        </div>
        <p class="price-root-status" data-price-root-status></p>
        <div class="price-root-actions">
          <button class="button" value="cancel">Cancel</button>
          <button class="button button-primary" type="button" data-price-root-save>
            Save for this computer
          </button>
        </div>
      </form>
    `;
    document.body.appendChild(dialog);
    input = dialog.querySelector("[data-price-root-input]");
    status = dialog.querySelector("[data-price-root-status]");
    dialog.querySelector("[data-price-root-save]").addEventListener("click", () => {
      const value = tidyRoot(input.value);
      if (!/^[A-Za-z]:\\/.test(value)) {
        status.textContent = "Enter the full Windows folder beginning with a drive letter.";
        return;
      }
      localStorage.setItem(KEY, value);
      const pending = queuedPath;
      queuedPath = "";
      dialog.close();
      if (pending) launch(pending);
    });
  }

  function settings() {
    if (!dialog) buildDialog();
    input.value = currentRoot() || DEFAULT_ROOT;
    status.textContent = "";
    dialog.showModal();
    input.focus();
    input.select();
  }

  function launch(relative) {
    if (!currentRoot()) {
      queuedPath = relative;
      settings();
      status.textContent =
        "Set the Customer Prices folder, then save to continue opening Excel.";
      return;
    }
    window.location.href = `ms-excel:ofe|u|${fileUri(fullPath(relative))}`;
  }

  async function copyPath(relative) {
    if (!currentRoot()) {
      settings();
      status.textContent = "Set the Customer Prices folder before copying a path.";
      return;
    }
    const path = fullPath(relative);
    try {
      await navigator.clipboard.writeText(path);
    } catch {
      window.prompt("Copy this local workbook path:", path);
    }
  }

  document.addEventListener("click", (event) => {
    const open = event.target.closest("[data-open-price-file]");
    if (open) {
      event.preventDefault();
      launch(open.dataset.priceRelativePath || "");
      return;
    }
    const copy = event.target.closest("[data-copy-price-path]");
    if (copy) {
      event.preventDefault();
      copyPath(copy.dataset.priceRelativePath || "");
      return;
    }
    if (event.target.closest("[data-price-root-settings]")) {
      event.preventDefault();
      queuedPath = "";
      settings();
    }
  });
})();
