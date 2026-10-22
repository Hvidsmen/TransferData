/* Timers use server timestamps, including after live-fragment replacement. */
(() => {
  "use strict";
  function duration(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    return `${Math.floor(total / 60)} мин ${String(total % 60).padStart(2, "0")} с`;
  }
  function tick() {
    document.querySelectorAll("[data-scan-created]").forEach(node => {
      if (!node.dataset.observedAt) node.dataset.observedAt = String(performance.now());
      const now = Date.parse(node.dataset.serverNow) + performance.now() - Number(node.dataset.observedAt);
      const created = Date.parse(node.dataset.scanCreated);
      const started = Date.parse(node.dataset.scanStarted);
      const finished = Date.parse(node.dataset.scanFinished);
      let label;
      if (node.dataset.scanStatus === "PENDING") label = `В очереди: ${duration((now - created) / 1000)}`;
      else if (node.dataset.scanStatus === "RUNNING") label = `Чтение: ${duration((now - (started || created)) / 1000)}`;
      else label = `Всего: ${duration(((finished || now) - created) / 1000)}`;
      if (node.textContent !== label) node.textContent = label;
    });
  }
  tick();
  setInterval(tick, 1000);
})();
