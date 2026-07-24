document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-allocation-form], [data-manufacture-line-form]").forEach((form) => {
    const type = form.querySelector("[data-allocation-type]");
    const customerField = form.querySelector("[data-customer-field]");
    const customerSelect = customerField ? customerField.querySelector("select") : null;
    if (!type || !customerField) return;

    const refreshCustomer = () => {
      const needsCustomer = type.value === "customer_cover" || type.value === "mto";
      customerField.hidden = !needsCustomer;
      if (customerSelect) customerSelect.required = needsCustomer;
      if (!needsCustomer && customerSelect) customerSelect.value = "";
    };
    type.addEventListener("change", refreshCustomer);
    refreshCustomer();
  });

  document.querySelectorAll("[data-manufacture-line-form]").forEach((form) => {
    const toggle = form.querySelector("[data-bring-in-toggle]");
    const quantityField = form.querySelector("[data-bring-in-quantity]");
    if (!toggle || !quantityField) return;
    const refresh = () => {
      quantityField.hidden = !toggle.checked;
    };
    toggle.addEventListener("change", refresh);
    refresh();
  });
});

// Searchable supplier/item comboboxes. Options stay in the page so filtering is
// instant and does not create a request for each keypress.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-smart-combobox]").forEach((root) => {
    const input = root.querySelector("[data-smart-input]");
    const value = root.querySelector("[data-smart-value]");
    const menu = root.querySelector("[data-smart-menu]");
    const options = Array.from(root.querySelectorAll("[data-smart-option]"));
    if (!input || !value || !menu || !options.length) return;

    let visible = [];
    let activeIndex = -1;

    const normalized = (text) => String(text || "").trim().toLocaleLowerCase();
    const selectOption = (option) => {
      input.value = option.dataset.label || option.textContent.trim();
      value.value = option.dataset.value || "";
      input.setCustomValidity("");
      menu.hidden = true;
      activeIndex = -1;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const refresh = () => {
      value.value = "";
      const query = normalized(input.value);
      const terms = query.split(/\s+/).filter(Boolean);
      visible = options.filter((option) => {
        const haystack = normalized(`${option.dataset.label || ""} ${option.textContent || ""}`);
        return terms.every((term) => haystack.includes(term));
      }).slice(0, 30);
      options.forEach((option) => {
        const isVisible = visible.includes(option);
        option.hidden = !isVisible;
        option.style.display = isVisible ? "block" : "none";
        option.classList.remove("active");
      });
      activeIndex = visible.length ? 0 : -1;
      if (activeIndex >= 0) visible[activeIndex].classList.add("active");
      menu.hidden = !query || !visible.length;
    };

    input.addEventListener("input", refresh);
    input.addEventListener("focus", () => {
      if (input.value.trim()) refresh();
    });
    input.addEventListener("keydown", (event) => {
      if (menu.hidden || !visible.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (activeIndex >= 0) visible[activeIndex].classList.remove("active");
        const direction = event.key === "ArrowDown" ? 1 : -1;
        activeIndex = (activeIndex + direction + visible.length) % visible.length;
        visible[activeIndex].classList.add("active");
        visible[activeIndex].scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        selectOption(visible[activeIndex]);
      } else if (event.key === "Escape") {
        menu.hidden = true;
      }
    });
    options.forEach((option) => option.addEventListener("click", () => selectOption(option)));
    document.addEventListener("click", (event) => {
      if (!root.contains(event.target)) menu.hidden = true;
    });

    const form = root.closest("form");
    if (form) {
      form.addEventListener("submit", (event) => {
        if (value.value) return;
        const exact = options.find((option) => normalized(option.dataset.label) === normalized(input.value));
        if (exact) {
          selectOption(exact);
          return;
        }
        input.setCustomValidity("Choose a value from the filtered list.");
        input.reportValidity();
        event.preventDefault();
      });
    }
  });

  document.querySelectorAll("[data-yu-mapping-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Update the master YU workbook mapping? A timestamped backup will be created first.")) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll("[data-yu-export-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Create the supplier-facing YU order and move this manufacture order to In Production?")) {
        event.preventDefault();
      }
    });
  });
});
