document.addEventListener("DOMContentLoaded", () => {
  const type = document.getElementById("id_job_type");
  if (!type) return;
  const table = ["source_schema", "source_table", "target_schema", "target_table", "batch_size", "allow_empty", "columns"];
  const procedure = ["procedure_schema", "procedure_name", "procedure_parameters", "procedure_timeout"];
  function update() {
    const isProcedure = type.value === "procedure";
    for (const [names, visible] of [[table, !isProcedure], [procedure, isProcedure]]) {
      for (const name of names) {
        const input = document.getElementById("id_" + name);
        if (!input) continue;
        const row = input.closest(".editor-field, .form-row");
        if (row) row.hidden = !visible;
        input.disabled = !visible;
        if (!visible) input.required = false;
      }
    }
  }
  type.addEventListener("change", update);
  update();
});
