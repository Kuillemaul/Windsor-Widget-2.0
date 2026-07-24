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
