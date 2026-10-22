(() => {
  const data = document.getElementById("chart-data");
  if (!data || !window.Plotly) return;
  const points = JSON.parse(data.textContent);
  Plotly.newPlot("durations", [{x: points.map(p => String(p.id)), y: points.map(p => p.duration_seconds),
    type: "bar", marker: {color: "#8885fb"}, hovertemplate: "Запуск #%{x}<br>%{y:.1f} с<extra></extra>"}], {
    height: 280, margin: {l: 52, r: 24, t: 12, b: 48}, paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    font: {family: "Segoe UI, sans-serif", color: "#97a5b9", size: 10},
    xaxis: {type: "category", title: {text: "Запуск"}, showgrid: false, zeroline: false},
    yaxis: {title: {text: "Секунды"}, gridcolor: "#eff3f9", zeroline: false}
  }, {responsive: true, displayModeBar: false});
})();
