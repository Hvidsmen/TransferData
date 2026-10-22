(() => {
  "use strict";
  const search = document.getElementById("job-monitor-search");
  const state = document.getElementById("job-monitor-status");
  if (!search || !state) return;
  function filter() {
    const rows = [...document.querySelectorAll("[data-monitor-row]")];
    const text = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    rows.forEach(row => {
      row.hidden = !(row.dataset.monitorSearch.toLocaleLowerCase().includes(text) &&
        (!state.value || row.dataset.monitorStatus === state.value));
      if (!row.hidden) visible++;
    });
    document.getElementById("job-monitor-total").textContent = String(rows.length);
    const empty = document.getElementById("job-monitor-no-match");
    if (empty) empty.hidden = !rows.length || visible > 0;
    document.getElementById("job-monitor-count").textContent = `Показано: ${visible} из ${rows.length}`;
  }
  search.addEventListener("input", filter);
  state.addEventListener("change", filter);
  document.addEventListener("etl:live-updated", filter);
  filter();
})();
