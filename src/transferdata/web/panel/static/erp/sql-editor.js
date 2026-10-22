document.addEventListener("DOMContentLoaded", () => {
  const sql = document.getElementById("id_sql_query");
  if (!sql || !window.CodeMirror) return;
  const editor = CodeMirror.fromTextArea(sql, {mode: "text/x-mssql", lineNumbers: true, indentUnit: 4});
  editor.setSize(null, 260);
  const type = document.getElementById("id_source_type");
  if (type) type.addEventListener("change", () => requestAnimationFrame(() => editor.refresh()));
});
