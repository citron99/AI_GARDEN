document.addEventListener("DOMContentLoaded", () => {
  const select = document.getElementById("privacyLanguage");
  select.value = window.I18N.getLanguage();
  select.addEventListener("change", () => window.I18N.setLanguage(select.value));
  window.addEventListener("ai-garden-language", (event) => { select.value = event.detail; });

  fetch("/api/v1/public/privacy-config")
    .then((response) => {
      if (!response.ok) throw new Error("privacy config unavailable");
      return response.json();
    })
    .then((config) => {
      const fallback = window.I18N.t("Не настроено оператором");
      document.querySelectorAll("[data-controller-name]").forEach((node) => {
        node.textContent = config.controller_name || fallback;
      });
      document.querySelectorAll("[data-privacy-email]").forEach((node) => {
        node.textContent = config.contact_email || fallback;
        if (config.contact_email) node.href = `mailto:${config.contact_email}`;
      });
      document.querySelectorAll("[data-policy-version]").forEach((node) => {
        node.textContent = config.policy_version;
      });
      document.querySelectorAll("[data-policy-date]").forEach((node) => {
        node.textContent = config.effective_date;
      });
      document.querySelectorAll("[data-retention]").forEach((node) => {
        node.textContent = config.retention_days[node.dataset.retention];
      });
      document.querySelectorAll("[data-unattached-hours]").forEach((node) => {
        node.textContent = config.unattached_photo_retention_hours;
      });
      const processors = Object.values(config.processors).filter(Boolean).join(", ");
      document.querySelectorAll("[data-processors]").forEach((node) => {
        node.textContent = processors;
      });
    })
    .catch(() => {});
});
