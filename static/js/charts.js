/**
 * Chart Components - Reusable Chart.js wrappers with dark mode support
 * Usage: Add data-chart="type" and data-chart-config='{}' to canvas elements
 * Currency and data come from server context - never hardcoded
 */

const Charts = {
  // Get currency from page context (set by server)
  getCurrency() {
    return document.querySelector('meta[name="currency"]')?.content || 'USD';
  },

  // Format currency using page locale
  formatCurrency(value, currency) {
    currency = currency || this.getCurrency();
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format(value);
  },

  // Default color palettes
  colors: {
    primary: ['rgba(20, 184, 166, 0.85)', 'rgba(13, 148, 136, 0.85)'],
    categorical: [
      'rgba(20, 184, 166, 0.85)',   // teal
      'rgba(244, 63, 94, 0.85)',    // rose
      'rgba(168, 85, 247, 0.85)',   // purple
      'rgba(251, 191, 36, 0.85)',   // amber
      'rgba(59, 130, 246, 0.85)',   // blue
      'rgba(34, 197, 94, 0.85)',    // green
      'rgba(236, 72, 153, 0.85)',   // pink
    ],
    aging: {
      current: 'rgba(16, 185, 129, 0.85)',
      days30: 'rgba(251, 191, 36, 0.85)',
      days60: 'rgba(249, 115, 22, 0.85)',
      days90: 'rgba(239, 68, 68, 0.85)',
    }
  },

  // Check dark mode
  isDark() {
    return document.documentElement.classList.contains('dark');
  },

  // Get theme-aware colors
  getThemeColors() {
    const dark = this.isDark();
    return {
      text: dark ? 'rgb(203, 213, 225)' : 'rgb(71, 85, 105)',
      textBold: dark ? 'rgb(248, 250, 252)' : 'rgb(15, 23, 42)',
      grid: dark ? 'rgba(71, 85, 105, 0.3)' : 'rgba(203, 213, 225, 0.5)',
      border: dark ? 'rgba(30, 41, 59, 1)' : 'rgba(255, 255, 255, 1)',
      tooltipBg: dark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(255, 255, 255, 0.95)',
    };
  },

  // Common tooltip config
  getTooltipConfig() {
    const theme = this.getThemeColors();
    return {
      backgroundColor: theme.tooltipBg,
      titleColor: theme.textBold,
      bodyColor: theme.text,
      borderColor: theme.grid,
      borderWidth: 1,
      padding: 12,
      displayColors: true,
    };
  },

  // Create donut chart
  donut(canvas, { labels, data, colors, centerText, currency }) {
    const theme = this.getThemeColors();
    const chartColors = colors || this.colors.categorical.slice(0, labels.length);

    return new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: chartColors,
          borderColor: theme.border,
          borderWidth: 3,
          hoverOffset: 8
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '65%',
        plugins: {
          legend: {
            position: 'right',
            labels: {
              color: theme.text,
              padding: 16,
              usePointStyle: true,
              pointStyle: 'rectRounded',
              font: { size: 12 }
            }
          },
          tooltip: {
            ...this.getTooltipConfig(),
            callbacks: {
              label: (ctx) => {
                const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                const pct = ((ctx.raw / total) * 100).toFixed(1);
                const val = currency
                  ? new Intl.NumberFormat('en-US', { style: 'currency', currency, minimumFractionDigits: 0 }).format(ctx.raw)
                  : ctx.raw.toLocaleString();
                return `${ctx.label}: ${val} (${pct}%)`;
              }
            }
          }
        }
      },
      plugins: centerText ? [{
        id: 'centerText',
        beforeDraw: (chart) => {
          const { ctx, width, height } = chart;
          const legendWidth = chart.legend?.width || 0;
          const centerX = (width - legendWidth) / 2;
          const centerY = height / 2;
          const isDark = document.documentElement.classList.contains('dark');

          ctx.save();
          ctx.font = 'bold 14px system-ui';
          ctx.fillStyle = isDark ? 'rgb(148, 163, 184)' : 'rgb(100, 116, 139)';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(centerText.label || 'Total', centerX, centerY - 12);

          ctx.font = 'bold 18px ui-monospace, monospace';
          ctx.fillStyle = centerText.color || (isDark ? 'rgb(248, 250, 252)' : 'rgb(15, 23, 42)');
          ctx.fillText(centerText.value, centerX, centerY + 10);
          ctx.restore();
        }
      }] : []
    });
  },

  // Create bar chart
  bar(canvas, { labels, datasets, horizontal, stacked, currency }) {
    const theme = this.getThemeColors();

    return new Chart(canvas, {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: horizontal ? 'y' : 'x',
        scales: {
          x: {
            stacked,
            grid: { color: theme.grid },
            ticks: { color: theme.text }
          },
          y: {
            stacked,
            grid: { color: theme.grid },
            ticks: { color: theme.text }
          }
        },
        plugins: {
          legend: {
            display: datasets.length > 1,
            labels: { color: theme.text, usePointStyle: true }
          },
          tooltip: {
            ...this.getTooltipConfig(),
            callbacks: {
              label: (ctx) => {
                const val = Charts.formatCurrency(ctx.raw, currency);
                return `${ctx.dataset.label}: ${val}`;
              }
            }
          }
        }
      }
    });
  },

  // Create line chart
  line(canvas, { labels, datasets, currency }) {
    const theme = this.getThemeColors();

    return new Chart(canvas, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: {
            grid: { color: theme.grid },
            ticks: { color: theme.text }
          },
          y: {
            grid: { color: theme.grid },
            ticks: { color: theme.text }
          }
        },
        plugins: {
          legend: {
            labels: { color: theme.text, usePointStyle: true }
          },
          tooltip: {
            ...this.getTooltipConfig(),
            callbacks: {
              label: (ctx) => {
                const val = Charts.formatCurrency(ctx.raw, currency);
                return `${ctx.dataset.label}: ${val}`;
              }
            }
          }
        }
      }
    });
  },

  // Create aging chart (stacked horizontal bar)
  aging(canvas, { labels, current, days30, days60, days90, currency }) {
    const colors = this.colors.aging;
    return this.bar(canvas, {
      labels,
      horizontal: true,
      stacked: true,
      currency,
      datasets: [
        { label: 'Current', data: current, backgroundColor: colors.current },
        { label: '1-30 Days', data: days30, backgroundColor: colors.days30 },
        { label: '31-60 Days', data: days60, backgroundColor: colors.days60 },
        { label: '60+ Days', data: days90, backgroundColor: colors.days90 },
      ]
    });
  },

  // Create trend line chart (revenue vs expenses style)
  trendLine(canvas, { labels, datasets, currency }) {
    const theme = this.getThemeColors();
    const defaultColors = [
      { border: 'rgba(13, 148, 136, 1)', bg: 'rgba(13, 148, 136, 0.1)' },
      { border: 'rgba(244, 63, 94, 1)', bg: 'rgba(244, 63, 94, 0.1)' },
      { border: 'rgba(59, 130, 246, 1)', bg: 'rgba(59, 130, 246, 0.1)' },
    ];

    const chartDatasets = datasets.map((ds, i) => ({
      label: ds.label,
      data: ds.data,
      borderColor: ds.color || defaultColors[i % defaultColors.length].border,
      backgroundColor: ds.fill || defaultColors[i % defaultColors.length].bg,
      borderWidth: 2.5,
      fill: true,
      tension: 0.4,
      pointRadius: 0,
      pointHoverRadius: 6,
      pointHoverBackgroundColor: ds.color || defaultColors[i % defaultColors.length].border,
      pointHoverBorderColor: '#fff',
      pointHoverBorderWidth: 2,
    }));

    return new Chart(canvas, {
      type: 'line',
      data: { labels, datasets: chartDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: { color: theme.text, font: { size: 11 } } },
          y: {
            grid: { color: theme.grid },
            ticks: {
              color: theme.text,
              font: { size: 11 },
              callback: (v) => Charts.formatCurrency(v, currency)
            }
          }
        },
        plugins: {
          legend: { display: true, labels: { color: theme.text, usePointStyle: true } },
          tooltip: {
            ...this.getTooltipConfig(),
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${Charts.formatCurrency(ctx.raw, currency)}`
            }
          }
        }
      }
    });
  },

  // Create grouped bar chart (cash flow style)
  groupedBar(canvas, { labels, datasets, currency }) {
    const theme = this.getThemeColors();
    const defaultColors = ['rgba(16, 185, 129, 0.85)', 'rgba(239, 68, 68, 0.85)'];

    const chartDatasets = datasets.map((ds, i) => ({
      label: ds.label,
      data: ds.data,
      backgroundColor: ds.color || defaultColors[i % defaultColors.length],
      borderRadius: 6,
      barPercentage: 0.6,
      categoryPercentage: 0.7,
    }));

    return new Chart(canvas, {
      type: 'bar',
      data: { labels, datasets: chartDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: theme.text } },
          y: {
            grid: { color: theme.grid },
            ticks: {
              color: theme.text,
              callback: (v) => Charts.formatCurrency(v, currency)
            }
          }
        },
        plugins: {
          legend: { display: true, labels: { color: theme.text, usePointStyle: true } },
          tooltip: {
            ...this.getTooltipConfig(),
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${Charts.formatCurrency(ctx.raw, currency)}`
            }
          }
        }
      }
    });
  },

  // Create horizontal bar chart (top customers/suppliers style)
  horizontalBar(canvas, { labels, data, color, currency }) {
    const theme = this.getThemeColors();
    const barColor = color || 'rgba(20, 184, 166, 0.85)';

    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: barColor,
          borderRadius: 4,
          barThickness: 20,
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { color: theme.grid },
            ticks: {
              color: theme.text,
              callback: (v) => Charts.formatCurrency(v, currency)
            }
          },
          y: {
            grid: { display: false },
            ticks: { color: theme.text, font: { size: 11 } }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...this.getTooltipConfig(),
            callbacks: {
              label: (ctx) => Charts.formatCurrency(ctx.raw, currency)
            }
          }
        }
      }
    });
  },

  // Create sparkline (mini line chart)
  sparkline(canvas, { data, color, fill }) {
    const theme = this.getThemeColors();
    const strokeColor = color || 'rgba(20, 184, 166, 1)';
    const fillColor = fill || strokeColor.replace('1)', '0.1)');

    return new Chart(canvas, {
      type: 'line',
      data: {
        labels: data.map((_, i) => i),
        datasets: [{
          data,
          borderColor: strokeColor,
          backgroundColor: fillColor,
          borderWidth: 1.5,
          fill: true,
          tension: 0.4,
          pointRadius: 0,
          pointHoverRadius: 3,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            ...this.getTooltipConfig(),
            callbacks: {
              title: () => '',
              label: (ctx) => ctx.raw.toLocaleString()
            }
          }
        },
        scales: {
          x: { display: false },
          y: { display: false }
        },
        interaction: { intersect: false, mode: 'index' }
      }
    });
  },

  // Create mini bar chart
  miniBar(canvas, { data, color }) {
    const theme = this.getThemeColors();
    const barColor = color || 'rgba(20, 184, 166, 0.7)';

    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels: data.map((_, i) => i),
        datasets: [{
          data,
          backgroundColor: barColor,
          borderRadius: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: false },
          y: { display: false, beginAtZero: true }
        }
      }
    });
  },

  // Create gauge/progress ring
  gauge(canvas, { value, max, color, label }) {
    const theme = this.getThemeColors();
    const percentage = Math.min((value / max) * 100, 100);
    const gaugeColor = color || 'rgba(20, 184, 166, 0.85)';

    return new Chart(canvas, {
      type: 'doughnut',
      data: {
        datasets: [{
          data: [percentage, 100 - percentage],
          backgroundColor: [gaugeColor, theme.grid],
          borderWidth: 0,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '75%',
        rotation: -90,
        circumference: 180,
        plugins: { legend: { display: false }, tooltip: { enabled: false } }
      },
      plugins: label ? [{
        id: 'gaugeLabel',
        beforeDraw: (chart) => {
          const { ctx, width, height } = chart;
          ctx.save();
          ctx.font = 'bold 14px ui-monospace';
          ctx.fillStyle = theme.textBold;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(label, width / 2, height - 10);
          ctx.restore();
        }
      }] : []
    });
  },

  // Auto-initialize charts from data attributes
  init() {
    document.querySelectorAll('[data-chart]').forEach(canvas => {
      const type = canvas.dataset.chart;
      const config = JSON.parse(canvas.dataset.chartConfig || '{}');

      if (this[type]) {
        this[type](canvas, config);
      }
    });
  }
};

// Auto-init when DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => Charts.init());
} else {
  Charts.init();
}

// Export for manual use
window.Charts = Charts;
