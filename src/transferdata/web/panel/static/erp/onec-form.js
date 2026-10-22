document.addEventListener("DOMContentLoaded", () => {
  const type = document.getElementById("id_source_type");
  const connection = document.getElementById("id_source_connection");
  const object = document.getElementById("id_one_c_object");
  if (!type || !connection || !object) return;
  function showField(id, visible) {
    const input = document.getElementById(id);
    const row = input && (input.closest("[data-field]") || input.closest("p"));
    if (row) row.hidden = !visible;
  }
  function update() {
    showField("id_source_table", type.value === "TABLE");
    showField("id_sql_query", type.value === "SQL_QUERY");
    showField("id_one_c_object", type.value === "ONEC");
    showField("id_onec_adjust_dates", type.value === "ONEC");
    showField("id_onec_resolve_references", type.value === "ONEC");
  }
  type.addEventListener("change", update);
  let controller;
  connection.addEventListener("change", async () => {
    if (controller) controller.abort();
    controller = new AbortController();
    object.replaceChildren(new Option("Чтение списка…", ""));
    object.dispatchEvent(new Event("change", {bubbles: true}));
    try {
      const url = new URL(object.dataset.objectsUrl, window.location.origin);
      url.searchParams.set("connection", connection.value);
      const response = await fetch(url, {credentials: "same-origin", signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error();
      const data = await response.json();
      object.replaceChildren(new Option("Выберите объект 1С", ""), ...data.objects.map(item => new Option(item.name, item.id)));
      object.dispatchEvent(new Event("change", {bubbles: true}));
    } catch (error) {
      if (error.name !== "AbortError") {
        object.replaceChildren(new Option("Не удалось получить список. Обновите страницу.", ""));
        object.dispatchEvent(new Event("change", {bubbles: true}));
      }
    }
  });
  update();
});
