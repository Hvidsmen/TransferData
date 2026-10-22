/* Refresh read-only fragments; keep navigation, filters and forms intact. */
(() => {
  "use strict";
  const selector = "[data-live][id]";
  if (!document.querySelector(selector)) return;
  const indicator = document.getElementById("live-status");
  let timer, busy = false, stopped = false;
  function status(message) {
    if (indicator) {
      indicator.hidden = false;
      indicator.textContent = message;
    }
  }
  async function refresh() {
    if (busy || stopped || document.hidden) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(window.location.href, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: {"X-ETL-Live": "1"},
      });
      if (response.redirected || response.status === 401 || response.status === 403) {
        stopped = true;
        status("Сессия завершена или доступ изменён. Обновите страницу для входа.");
        return;
      }
      if (!response.ok) throw new Error("Request failed");
      const next = new DOMParser().parseFromString(await response.text(), "text/html");
      const fragments = Array.from(document.querySelectorAll(selector));
      if (fragments.some(node => !next.getElementById(node.id))) throw new Error("Missing fragment");
      const scrollX = window.scrollX, scrollY = window.scrollY;
      for (const current of fragments) {
        if (current.contains(document.activeElement)) continue;
        const replacement = next.getElementById(current.id);
        if (current.innerHTML === replacement.innerHTML) continue;
        const offsets = Array.from(current.querySelectorAll(".table-scroll, .table-responsive"))
          .map(node => [node.scrollLeft, node.scrollTop]);
        current.replaceWith(replacement);
        replacement.querySelectorAll(".table-scroll, .table-responsive").forEach((node, index) => {
          if (offsets[index]) [node.scrollLeft, node.scrollTop] = offsets[index];
        });
      }
      window.scrollTo(scrollX, scrollY);
      status("● Автообновление · каждые 3 секунды");
    } catch (_) {
      status("Нет связи с порталом. Повторяем подключение…");
    } finally {
      clearTimeout(timeout);
      busy = false;
      if (!stopped && !document.hidden) timer = setTimeout(refresh, 3000);
    }
  }
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden) refresh();
  });
  status("● Автообновление · каждые 3 секунды");
  timer = setTimeout(refresh, 3000);
})();
