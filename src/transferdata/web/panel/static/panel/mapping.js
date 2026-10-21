document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("input[data-tables]").forEach(input => {
    const list = document.createElement("datalist");
    list.id = input.id + "_tables";
    JSON.parse(input.dataset.tables).forEach(name => {
      const option = document.createElement("option");
      option.value = name;
      list.append(option);
    });
    input.setAttribute("list", list.id);
    input.after(list);
  });
  document.querySelectorAll(".mapping-editor").forEach(editor => {
    const textarea = editor.querySelector("textarea");
    const rows = editor.querySelector(".mapping-rows");
    function sync() {
      const mapping = Object.create(null);
      const sources = new Set();
      const targets = new Set();
      rows.querySelectorAll(".mapping-row").forEach(row => {
        const inputs = row.querySelectorAll("input");
        inputs[0].setCustomValidity(sources.has(inputs[0].value) ? "Исходное имя повторяется" : "");
        inputs[1].setCustomValidity(targets.has(inputs[1].value) ? "Имя назначения повторяется" :
          new TextEncoder().encode(inputs[1].value).length > 63 ? "Имя длиннее 63 UTF-8 байт" : "");
        sources.add(inputs[0].value);
        targets.add(inputs[1].value);
        if (inputs[0].value) mapping[inputs[0].value] = inputs[1].value;
      });
      textarea.value = JSON.stringify(mapping, null, 2);
    }
    function add(source, target) {
      const row = document.createElement("div");
      row.className = "mapping-row";
      row.style.cssText = "display:flex;gap:8px;margin:6px 0;flex-wrap:wrap";
      [source, target].forEach((value, index) => {
        const input = document.createElement("input");
        input.type = "text";
        input.required = true;
        input.value = value;
        input.setAttribute("aria-label", index ? "Столбец назначения" : "Исходный столбец");
        input.style.width = "min(36vw,360px)";
        input.addEventListener("input", sync);
        row.append(input);
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Удалить";
      remove.addEventListener("click", () => { row.remove(); sync(); });
      row.append(remove);
      rows.append(row);
    }
    function load() {
      try {
        const mapping = JSON.parse(textarea.value || "{}");
        if (!mapping || Array.isArray(mapping) || typeof mapping !== "object") throw Error();
        rows.replaceChildren();
        Object.entries(mapping).forEach(([source, target]) => add(source, target));
      } catch (_) { alert("Проверьте JSON: нужен объект соответствий имён."); }
    }
    editor.querySelector(".mapping-reload").addEventListener("click", load);
    editor.querySelector(".mapping-add").addEventListener("click", () => add("", ""));
    editor.querySelector(".mapping-source").addEventListener("click", () => {
      const columns = JSON.parse(textarea.dataset.columns || "[]");
      if (!columns.length) { alert("Сначала получите структуру источника и обновите форму."); return; }
      const current = JSON.parse(textarea.value || "{}");
      const taken = new Set(Object.values(current));
      columns.forEach((column, index) => {
        if (!(column.name in current)) {
          let target = column.bytes > 63 ? "column_" + (index + 1) : column.name;
          while (taken.has(target)) target += "_";
          taken.add(target);
          current[column.name] = target;
        }
      });
      textarea.value = JSON.stringify(current, null, 2);
      load();
    });
    load();
  });
});
