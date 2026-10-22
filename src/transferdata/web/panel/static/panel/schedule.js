document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".schedule-picker").forEach(picker => {
    const role = name => picker.querySelector('[data-role="' + name + '"]');
    const mode = role("mode"), at = role("time"), day = role("day");
    const rawBox = picker.querySelector(".schedule-raw"), raw = rawBox.querySelector("input");
    const checks = [...picker.querySelectorAll("input[data-weekday]")];
    const labels = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
    function decode(value) {
      const parts = (value || "").trim().split(/\s+/);
      if (parts.length !== 5 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) return false;
      const [minute, hour, date, month, week] = parts;
      if (+minute > 59 || +hour > 23 || month !== "*") return false;
      at.value = hour.padStart(2, "0") + ":" + minute.padStart(2, "0");
      checks.forEach(c => c.checked = false);
      if (date === "*" && week === "*") mode.value = "daily";
      else if (date === "*" && /^[0-6](,[0-6])*$/.test(week)) {
        mode.value = "weekly";
        checks.forEach(c => c.checked = week.split(",").includes(c.value));
      } else if (week === "*" && /^\d+$/.test(date) && +date >= 1 && +date <= 31) {
        mode.value = "monthly"; day.value = String(+date);
      } else return false;
      return true;
    }
    function show(write) {
      const advanced = mode.value === "advanced";
      role("week").hidden = mode.value !== "weekly";
      role("month").hidden = mode.value !== "monthly";
      role("time-label").hidden = advanced;
      at.required = !advanced; at.disabled = advanced;
      rawBox.hidden = !advanced;
      const selected = checks.filter(c => c.checked).map(c => c.value);
      checks[0].setCustomValidity(!advanced && mode.value === "weekly" && !selected.length ? "Выберите хотя бы один день недели" : "");
      checks.forEach(c => c.disabled = mode.value !== "weekly");
      day.disabled = mode.value !== "monthly";
      if (advanced) { role("time").setCustomValidity(""); role("time").disabled = true; role("week").hidden = true;
        picker.querySelector(".schedule-preview").textContent = "Нестандартное расписание: задайте пять полей cron."; return; }
      const clock = at.value.split(":");
      if (write && clock.length === 2 && (mode.value !== "weekly" || selected.length)) {
        raw.value = [+clock[1], +clock[0], mode.value === "monthly" ? day.value : "*", "*", mode.value === "weekly" ? selected.join(",") : "*"].join(" ");
      }
      let description = "Каждый день";
      if (mode.value === "weekly") description = selected.length ? "По дням: " + selected.map(n => labels[+n]).join(", ") : "Выберите дни недели";
      if (mode.value === "monthly") description = "Каждый месяц, " + day.value + " числа";
      const zone = picker.dataset.timezone || document.getElementById("id_timezone")?.value || "Europe/Moscow";
      picker.querySelector(".schedule-preview").textContent = description + " в " + (at.value || "—") + " · " + zone;
    }
    const recognized = decode(raw.value);
    if (!recognized) {
      if (picker.dataset.advanced === "true") mode.value = "advanced";
      else if (!raw.value) { mode.value = "daily"; raw.value = "0 2 * * *"; }
      else { mode.add(new Option("Проверьте существующее расписание", "advanced")); mode.value = "advanced"; }
    }
    role("mode").addEventListener("change", () => {
      if (mode.value === "weekly" && !checks.some(c => c.checked)) checks[0].checked = true;
      show(true);
    });
    [at, day, ...checks].forEach(input => input.addEventListener("change", () => show(true)));
    document.getElementById("id_timezone")?.addEventListener("input", () => show(false));
    picker.querySelector(".schedule-controls").hidden = false;
    show(false);
  });
});
