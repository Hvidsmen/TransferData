document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("bulk-jobs");
  if (!form) return;
  const boxes = [...document.querySelectorAll('input[name="selected"][form="bulk-jobs"]')];
  const all = document.getElementById("select-all-jobs");
  const operation = form.elements.operation;
  function update() {
    const count = boxes.filter(box => box.checked).length;
    document.getElementById("selection-count").textContent = "Выбрано: " + count;
    document.getElementById("apply-bulk").disabled = count === 0;
    all.checked = count > 0 && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
    const picker = form.elements.group.closest(".smart-select") || form.elements.group;
    picker.hidden = operation.value !== "group";
  }
  all.addEventListener("change", () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
  boxes.forEach(box => box.addEventListener("change", update));
  operation.addEventListener("change", update);
  update();
});
