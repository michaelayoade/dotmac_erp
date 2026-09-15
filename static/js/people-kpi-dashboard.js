/* Read-only chart lifecycle for the People KPI workspace. No external services. */
(() => {
  'use strict';
  if (window.DotmacKPICharts) return;
  const selector = '#kpi-workspace canvas[data-kpi-chart]';
  const instances = new Map();
  const destroy = (element) => {
    for (const [canvas, chart] of instances) {
      if (canvas === element || element.contains(canvas)) {
        chart.destroy();
        instances.delete(canvas);
      }
    }
  };
  const mount = () => {
    if (!window.Chart) return;
    document.querySelectorAll(selector).forEach((canvas) => {
      if (instances.has(canvas)) return;
      let payload;
      try {
        payload = JSON.parse(canvas.dataset.kpiConfig);
      } catch (_) {
        // The accessible table remains usable if a chart payload is invalid.
        return;
      }
      const theme = window.Charts?.getThemeColors();
      const palette = window.Charts?.colors.categorical || [];
      const isDepartment = canvas.dataset.kpiChart === 'departments';
      const data = isDepartment ? payload : {
        labels: payload.labels,
        datasets: [{ data: payload.data, backgroundColor: palette.slice(0, 2) }]
      };
      if (isDepartment) {
        data.datasets = data.datasets.map((series, index) => ({
          ...series, backgroundColor: palette[index], borderWidth: 0
        }));
      }
      const chart = new window.Chart(canvas, {
        type: isDepartment ? 'bar' : 'doughnut', data,
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          indexAxis: isDepartment ? 'y' : 'x',
          plugins: {
            legend: { position: 'bottom', labels: { color: theme?.text } },
            tooltip: {
              callbacks: {
                label: (context) => {
                  const count = isDepartment ? context.parsed.x : context.parsed;
                  const total = isDepartment
                    ? data.datasets.reduce((sum, series) => sum + Number(series.data[context.dataIndex] || 0), 0)
                    : data.datasets[0].data.reduce((sum, value) => sum + Number(value), 0);
                  return `${context.dataset.label || context.label}: ${count} (${total ? (count * 100 / total).toFixed(1) : '0.0'}%)`;
                }
              }
            }
          },
          ...(isDepartment ? { scales: {
            x: { stacked: true, beginAtZero: true, ticks: { precision: 0, color: theme?.text }, grid: { color: theme?.grid } },
            y: { stacked: true, ticks: { color: theme?.text }, grid: { display: false } }
          }} : {})
        }
      });
      instances.set(canvas, chart);
    });
  };
  window.DotmacKPICharts = { mount, destroy };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
  document.addEventListener('htmx:beforeCleanupElement', (event) => destroy(event.detail.elt));
  document.addEventListener('htmx:afterSwap', mount);
  new MutationObserver(() => { destroy(document.documentElement); mount(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
})();
