/* Searchable catalogue picker. The native select remains the submitted value. */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("select[data-searchable]").forEach((select, number) => {
    const wrap = document.createElement("div");
    wrap.className = "smart-select";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "smart-select-toggle";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    const caption = document.createElement("span");
    const chevron = document.createElement("span");
    chevron.textContent = "⌄";
    chevron.setAttribute("aria-hidden", "true");
    button.append(caption, chevron);
    const popup = document.createElement("div");
    popup.className = "smart-select-popup";
    popup.hidden = true;
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = select.dataset.searchPlaceholder || "Поиск группы…";
    input.autocomplete = "off";
    input.setAttribute("aria-label", input.placeholder);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    const list = document.createElement("div");
    list.className = "smart-select-options";
    list.id = "group-options-" + number;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", select.getAttribute("aria-label") || "Группы заданий");
    input.setAttribute("aria-controls", list.id);
    button.setAttribute("aria-controls", list.id);
    const footer = document.createElement("div");
    footer.className = "smart-select-footer";
    footer.setAttribute("role", "status");
    popup.append(input, list, footer);
    select.before(wrap);
    wrap.append(select, button, popup);
    select.hidden = true;
    const label = select.labels && select.labels[0];
    button.setAttribute("aria-label", select.getAttribute("aria-label") || (label && label.textContent.trim()) || "Группа заданий");
    if (label) label.addEventListener("click", event => { event.preventDefault(); button.focus(); });
    let options = [], focused = -1;
    function close() {
      popup.hidden = true;
      button.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-expanded", "false");
    }
    function highlight(index) {
      focused = index;
      [...list.children].forEach((node, i) => node.classList.toggle("focused", i === focused));
      if (focused >= 0) {
        input.setAttribute("aria-activedescendant", list.children[focused].id);
        list.children[focused].scrollIntoView({block: "nearest"});
      } else input.removeAttribute("aria-activedescendant");
    }
    function choose(option) {
      select.value = option.value;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      close();
      button.focus();
    }
    function render() {
      const query = input.value.trim().toLocaleLowerCase();
      options = [...select.options].filter(option => !option.disabled && option.textContent.toLocaleLowerCase().includes(query));
      list.replaceChildren();
      options.forEach((option, index) => {
        const item = document.createElement("div");
        item.id = list.id + "-" + index;
        item.className = "smart-select-option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(option.selected));
        item.textContent = option.textContent;
        item.addEventListener("mousedown", event => event.preventDefault());
        item.addEventListener("click", () => choose(option));
        list.append(item);
      });
      footer.textContent = options.length ? "Найдено: " + options.length : (select.dataset.emptyMessage || "Группы не найдены. Попробуйте другое название.");
      highlight(-1);
    }
    function open() {
      popup.hidden = false;
      popup.style.left = "0";
      popup.style.right = "auto";
      if (popup.getBoundingClientRect().right > window.innerWidth - 12) {
        popup.style.left = "auto";
        popup.style.right = "0";
      }
      button.setAttribute("aria-expanded", "true");
      input.setAttribute("aria-expanded", "true");
      input.value = "";
      render();
      input.focus();
    }
    select.addEventListener("change", () => { caption.textContent = select.selectedOptions[0]?.textContent || "Без группы"; });
    caption.textContent = select.selectedOptions[0]?.textContent || "Без группы";
    button.disabled = select.disabled;
    button.addEventListener("click", () => popup.hidden ? open() : close());
    button.addEventListener("keydown", event => { if (event.key === "ArrowDown") { event.preventDefault(); open(); } });
    input.addEventListener("input", render);
    input.addEventListener("keydown", event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (options.length) highlight((focused + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (options[focused >= 0 ? focused : 0]) choose(options[focused >= 0 ? focused : 0]);
      } else if (event.key === "Escape") { event.preventDefault(); close(); button.focus(); }
    });
    wrap.addEventListener("focusout", event => { if (!wrap.contains(event.relatedTarget)) close(); });
    document.addEventListener("click", event => { if (!wrap.contains(event.target)) close(); });
  });
});
