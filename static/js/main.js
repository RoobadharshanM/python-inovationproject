/* Stump AI — shared UI, theme and Chart.js helpers */
(() => {
  'use strict';

  const root = document.documentElement;
  const THEME_KEY = 'stump-theme';
  const chartRegistry = new Map();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const css = (name, fallback = '') => getComputedStyle(root).getPropertyValue(name).trim() || fallback;
  const palette = () => [
    css('--primary', '#4d8dff'),
    css('--secondary', '#8257ff'),
    css('--cyan-400', '#22d3ee'),
    css('--green-400', '#34d399'),
    css('--orange-400', '#fb923c'),
    css('--red-400', '#fb7185'),
    '#60a5fa',
    '#c084fc'
  ];

  const normalizeKey = (value) => String(value ?? '')
    .replace(/([a-z0-9])([A-Z])/g, '$1$2')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');

  const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const isFiniteNumber = (value) => typeof value === 'number' ? Number.isFinite(value) : value !== null && value !== '' && Number.isFinite(Number(value));
  const numberValue = (value) => isFiniteNumber(value) ? Number(value) : null;
  const shortNumber = (value, digits = 1) => {
    const number = numberValue(value);
    if (number === null) return '—';
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(number);
  };

  function themeColors() {
    const dark = root.dataset.bsTheme !== 'light';
    return {
      text: dark ? '#eaf1ff' : '#26364f',
      muted: dark ? '#98a9c3' : '#6c7d96',
      grid: dark ? 'rgba(153, 177, 215, 0.12)' : 'rgba(46, 74, 112, 0.12)',
      border: dark ? 'rgba(164, 190, 226, 0.16)' : 'rgba(32, 67, 112, 0.16)',
      tooltipBg: dark ? '#0d1c33' : '#ffffff',
      tooltipTitle: dark ? '#ffffff' : '#12213a',
      tooltipBody: dark ? '#c4d1e5' : '#52647e'
    };
  }

  function findValue(source, aliases, directPaths = []) {
    if (!source) return undefined;
    for (const path of directPaths) {
      const value = path.split('.').reduce((current, key) => current?.[key], source);
      if (value !== undefined && value !== null) return value;
    }

    const wanted = new Set(aliases.map(normalizeKey));
    const queue = [{ value: source, depth: 0 }];
    const seen = new Set();
    while (queue.length) {
      const current = queue.shift();
      if (!current.value || seen.has(current.value)) continue;
      seen.add(current.value);
      if (current.depth > 7) continue;
      if (Array.isArray(current.value)) {
        current.value.forEach((item) => queue.push({ value: item, depth: current.depth + 1 }));
        continue;
      }
      if (!isObject(current.value)) continue;
      for (const [key, value] of Object.entries(current.value)) {
        if (value !== undefined && value !== null && wanted.has(normalizeKey(key))) return value;
        if (isObject(value) || Array.isArray(value)) queue.push({ value, depth: current.depth + 1 });
      }
    }
    return undefined;
  }

  function rowsFrom(value) {
    if (Array.isArray(value)) return value;
    if (!isObject(value)) return [];
    const rowKey = ['rows', 'items', 'results', 'records', 'data', 'values', 'entries'].find((key) => Array.isArray(value[key]));
    if (rowKey) return value[rowKey];
    return [];
  }

  function labelFromObject(item, fallbackIndex) {
    if (!isObject(item)) return String(item ?? fallbackIndex);
    const aliases = ['label', 'name', 'season', 'year', 'team', 'team_name', 'player', 'player_name', 'venue', 'venue_name', 'category', 'result_type', 'decision', 'toss_decision', 'metric', 'date'];
    for (const key of aliases) {
      if (item[key] !== undefined && item[key] !== null && item[key] !== '') return item[key];
    }
    const first = Object.values(item).find((entry) => ['string', 'number'].includes(typeof entry));
    return first ?? fallbackIndex;
  }

  function valueFromObject(item) {
    if (!isObject(item)) return numberValue(item);
    const aliases = ['value', 'y', 'count', 'total', 'runs', 'wickets', 'average', 'avg_score', 'mean_score', 'score', 'strike_rate', 'economy', 'matches', 'wins', 'win_probability', 'probability', 'batting_average', 'bowling_average'];
    for (const key of aliases) {
      const value = numberValue(item[key]);
      if (value !== null) return value;
    }
    return null;
  }

  function objectDictionary(value, preferredKeys = []) {
    if (!isObject(value) || Array.isArray(value)) return null;
    const reserved = new Set(['labels', 'label', 'datasets', 'data', 'values', 'rows', 'items', 'results', 'records', 'columns', 'x', 'y', 'points', 'name', 'title']);
    const entries = Object.entries(value).filter(([key, item]) => !reserved.has(key) && (isFiniteNumber(item) || typeof item === 'string'));
    if (!entries.length) return null;
    const preferred = preferredKeys.map(normalizeKey);
    entries.sort((a, b) => {
      const ai = preferred.indexOf(normalizeKey(a[0]));
      const bi = preferred.indexOf(normalizeKey(b[0]));
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    });
    return entries.map(([label, item], index) => ({ label, value: numberValue(item) ?? 0, index }));
  }

  function normalizeSeries(raw) {
    const empty = { labels: [], datasets: [], points: [] };
    if (raw === undefined || raw === null) return empty;

    if (Array.isArray(raw) && raw.length && Array.isArray(raw[0])) {
      return { labels: raw[0].map(String), datasets: raw.slice(1).map((values) => ({ data: values.map(numberValue).filter((v) => v !== null) })), points: [] };
    }

    if (Array.isArray(raw)) {
      if (raw.every(isFiniteNumber)) return { labels: raw.map((_, index) => String(index + 1)), datasets: [{ data: raw.map(Number) }], points: [] };
      const points = raw.map((item, index) => {
        if (Array.isArray(item)) return { label: String(item[0] ?? index + 1), value: numberValue(item[1]), x: numberValue(item[0]), y: numberValue(item[1]) };
        const label = labelFromObject(item, index + 1);
        const value = valueFromObject(item);
        const explicitX = numberValue(item?.x);
        const explicitY = numberValue(item?.y);
        return { label: String(label), value, x: explicitX, y: explicitY ?? value };
      }).filter((point) => point.value !== null || (point.x !== null && point.y !== null));
      return { labels: points.map((point) => point.label), datasets: [{ data: points.map((point) => point.value ?? point.y) }], points };
    }

    if (!isObject(raw)) return empty;
    if (Array.isArray(raw.points)) {
      const points = raw.points.map((point, index) => ({ label: labelFromObject(point, index + 1), x: numberValue(point.x), y: numberValue(point.y), value: valueFromObject(point) })).filter((p) => p.x !== null && p.y !== null);
      return { labels: points.map((p) => p.label), datasets: [{ data: points.map((p) => p.y) }], points };
    }
    if (Array.isArray(raw.datasets) && raw.datasets.length) {
      return {
        labels: (raw.labels || raw.data || []).map(String),
        datasets: raw.datasets.map((dataset) => ({ ...dataset, data: (dataset.data || []).map(numberValue) })),
        points: []
      };
    }

    const rows = rowsFrom(raw);
    if (rows.length) {
      if (rows.every((row) => isObject(row) && numberValue(row.x) !== null && numberValue(row.y) !== null)) {
        const points = rows.map((row, index) => ({ label: labelFromObject(row, index + 1), x: Number(row.x), y: Number(row.y) }));
        return { labels: points.map((p) => p.label), datasets: [{ data: points.map((p) => p.y) }], points };
      }
      return normalizeSeries(rows);
    }

    const values = ['values', 'data', 'items', 'results'].map((key) => raw[key]).find(Array.isArray);
    if (Array.isArray(values)) {
      const labels = Array.isArray(raw.labels) ? raw.labels.map(String) : values.map((_, index) => String(index + 1));
      return { labels, datasets: [{ data: values.map(numberValue) }], points: [] };
    }

    const dictionary = objectDictionary(raw);
    if (dictionary) return { labels: dictionary.map((item) => String(item.label)), datasets: [{ data: dictionary.map((item) => item.value) }], points: [] };
    return empty;
  }

  function hasSeriesData(series) {
    if (!series) return false;
    if (series.points?.some((point) => point.x !== null && point.y !== null)) return true;
    return series.datasets?.some((dataset) => Array.isArray(dataset.data) && dataset.data.some((value) => value !== null && Number.isFinite(Number(value)))) || false;
  }

  function setChartState(card, state) {
    if (!card) return;
    const status = card.querySelector('[data-chart-status]');
    if (status) {
      status.classList.toggle('is-loading', state === 'loading');
      status.classList.toggle('is-error', state === 'empty' || state === 'error');
      const label = status.lastChild;
      if (label?.nodeType === Node.TEXT_NODE) label.textContent = state === 'loading' ? 'Loading' : state === 'empty' ? 'No data' : state === 'error' ? 'Unavailable' : 'Live data';
    }
  }

  function showChartEmpty(id, visible = true) {
    const canvas = document.getElementById(id);
    const container = canvas?.closest('[data-chart-container]');
    const empty = container?.querySelector('[data-chart-empty]');
    if (canvas) canvas.hidden = visible;
    if (empty) empty.hidden = !visible;
    setChartState(canvas?.closest('[data-chart-card]'), visible ? 'empty' : 'live');
  }

  function makeDataset(label, data, index, type) {
    const colors = palette();
    const color = colors[index % colors.length];
    const common = {
      label: label || 'Value',
      data,
      borderColor: color,
      backgroundColor: type === 'line' ? `${color}22` : `${color}cc`,
      borderWidth: type === 'line' || type === 'radar' || type === 'scatter' ? 2 : 0,
      pointRadius: type === 'line' ? 2.5 : type === 'scatter' ? 4 : 0,
      pointHoverRadius: type === 'scatter' ? 6 : 4,
      pointBackgroundColor: color,
      pointBorderColor: css('--bg-elevated', '#0b172a'),
      pointBorderWidth: type === 'scatter' ? 1 : 0,
      borderRadius: type === 'bar' ? 5 : 0,
      borderSkipped: false,
      tension: 0.32,
      fill: type === 'line' ? false : undefined
    };
    if (type === 'radar') {
      common.backgroundColor = `${color}38`;
      common.pointBackgroundColor = color;
    }
    if (type === 'doughnut') {
      return { label, data, backgroundColor: data.map((_, itemIndex) => colors[itemIndex % colors.length]), borderColor: css('--border', 'rgba(255,255,255,.1)'), borderWidth: 2, hoverOffset: 6 };
    }
    if (type === 'scatter') {
      return { label, data, backgroundColor: `${color}cc`, pointRadius: 5, pointHoverRadius: 7 };
    }
    return common;
  }

  function chartOptions(type, horizontal = false) {
    const theme = themeColors();
    const baseTooltip = {
      backgroundColor: theme.tooltipBg,
      titleColor: theme.tooltipTitle,
      bodyColor: theme.tooltipBody,
      borderColor: theme.border,
      borderWidth: 1,
      padding: 10,
      displayColors: true,
      usePointStyle: true
    };
    if (['doughnut', 'radar'].includes(type)) {
      return {
        responsive: true,
        maintainAspectRatio: false,
        animation: reducedMotion.matches ? false : { duration: 650 },
        plugins: {
          legend: { labels: { color: theme.muted, usePointStyle: true, boxWidth: 8, padding: 16, font: { size: 10 } } },
          tooltip: baseTooltip
        }
      };
    }
    const valueScale = {
      beginAtZero: true,
      grid: { color: theme.grid, drawBorder: false },
      border: { display: false },
      ticks: { color: theme.muted, maxTicksLimit: 6, font: { size: 9 } }
    };
    const xScale = horizontal
      ? { type: 'linear', grid: { display: false }, border: { display: false }, ticks: { color: theme.muted, maxTicksLimit: 6, font: { size: 9 } } }
      : type === 'scatter'
        ? { type: 'linear', beginAtZero: true, grid: { color: theme.grid, drawBorder: false }, border: { display: false }, ticks: { color: theme.muted, maxTicksLimit: 6, font: { size: 9 } } }
        : { type: 'category', grid: { display: false }, border: { display: false }, ticks: { color: theme.muted, maxTicksLimit: 7, font: { size: 9 }, autoSkip: true, maxRotation: 0 } };
    const yScale = horizontal
      ? { type: 'category', grid: { display: false }, border: { display: false }, ticks: { color: theme.muted, font: { size: 9 } } }
      : { ...valueScale, stacked: false };
    return {
      indexAxis: horizontal ? 'y' : 'x',
      responsive: true,
      maintainAspectRatio: false,
      animation: reducedMotion.matches ? false : { duration: 650 },
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: type === 'scatter' || horizontal, labels: { color: theme.muted, usePointStyle: true, boxWidth: 8, padding: 14, font: { size: 10 } } },
        tooltip: baseTooltip
      },
      scales: { x: xScale, y: yScale }
    };
  }

  function createChart(id, type, rawData, options = {}) {
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === 'undefined') return null;
    const existing = chartRegistry.get(id);
    if (existing) existing.destroy();
    const series = normalizeSeries(rawData);
    if (!hasSeriesData(series)) {
      showChartEmpty(id, true);
      return null;
    }

    let datasets = series.datasets;
    let data = series;
    if (type === 'scatter') {
      const points = series.points.length ? series.points : series.labels.map((label, index) => ({ label, x: series.datasets[0]?.data[index], y: null })).filter((point) => point.x !== null && point.y !== null);
      if (!points.length) {
        showChartEmpty(id, true);
        return null;
      }
      datasets = [{ label: options.label || 'Player', data: points.map((point) => ({ x: point.x, y: point.y })) }];
    } else if (!datasets.length) {
      showChartEmpty(id, true);
      return null;
    } else {
      datasets = datasets.map((dataset, index) => ({ ...makeDataset(dataset.label, dataset.data, index, type), ...dataset, data: dataset.data }));
    }

    const chart = new Chart(canvas, {
      type,
      data: { labels: series.labels, datasets },
      options: { ...chartOptions(type, options.horizontal), ...options.options }
    });
    chartRegistry.set(id, chart);
    showChartEmpty(id, false);
    return chart;
  }

  function refreshRegisteredCharts() {
    chartRegistry.forEach((chart, id) => {
      if (!chart || !document.body.contains(chart.canvas)) {
        chart?.destroy();
        chartRegistry.delete(id);
        return;
      }
      if (chart.config.type === 'doughnut' || chart.config.type === 'radar') {
        const theme = themeColors();
        if (chart.options.plugins?.legend?.labels) chart.options.plugins.legend.labels.color = theme.muted;
        if (chart.options.plugins?.tooltip) {
          chart.options.plugins.tooltip.backgroundColor = theme.tooltipBg;
          chart.options.plugins.tooltip.titleColor = theme.tooltipTitle;
          chart.options.plugins.tooltip.bodyColor = theme.tooltipBody;
          chart.options.plugins.tooltip.borderColor = theme.border;
        }
      } else {
        const scales = chart.options.scales || {};
        Object.values(scales).forEach((scale) => {
          if (scale.ticks) scale.ticks.color = themeColors().muted;
          if (scale.grid?.color) scale.grid.color = themeColors().grid;
          if (scale.border) scale.border.color = themeColors().border;
        });
        if (chart.options.plugins?.tooltip) {
          chart.options.plugins.tooltip.backgroundColor = themeColors().tooltipBg;
          chart.options.plugins.tooltip.titleColor = themeColors().tooltipTitle;
          chart.options.plugins.tooltip.bodyColor = themeColors().tooltipBody;
          chart.options.plugins.tooltip.borderColor = themeColors().border;
        }
      }
      chart.update(reducedMotion.matches ? 'none' : undefined);
    });
  }

  function destroyCharts(scope = document) {
    chartRegistry.forEach((chart, id) => {
      if (!scope.contains(chart?.canvas)) return;
      chart.destroy();
      chartRegistry.delete(id);
    });
  }

  function getJSON(id) {
    const element = document.getElementById(id);
    if (!element) return null;
    try { return JSON.parse(element.textContent || 'null'); } catch (_) { return null; }
  }

  async function fetchJSON(url, options = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), options.timeout || 15000);
    try {
      const response = await fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...(options.headers || {}) },
        signal: controller.signal,
        ...options,
        headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...(options.headers || {}) }
      });
      if (!response.ok) {
        let detail = '';
        try {
          const body = await response.json();
          detail = body.detail || body.error || '';
        } catch (_) { /* response was not JSON */ }
        const error = new Error(detail || `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
      }
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  function showToast(message, type = 'info', timeout = 4800) {
    const host = document.getElementById('toastHost');
    if (!host || !message) return;
    const toast = document.createElement('div');
    toast.className = `app-toast is-${type}`;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    const icon = type === 'success' ? 'bi-check2-circle' : type === 'error' ? 'bi-exclamation-circle' : 'bi-info-circle';
    toast.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i><p class="flex-grow-1"></p><button class="btn-close btn-close-sm" type="button" aria-label="Dismiss"></button>`;
    toast.querySelector('p').textContent = String(message);
    host.append(toast);
    const remove = () => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(6px)';
      window.setTimeout(() => toast.remove(), 180);
    };
    toast.querySelector('button').addEventListener('click', remove);
    window.setTimeout(remove, timeout);
  }

  function applyTheme(theme, persist = true) {
    const next = theme === 'light' ? 'light' : 'dark';
    root.dataset.bsTheme = next;
    root.dataset.userTheme = next;
    if (persist) {
      try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* storage can be disabled */ }
    }
    const toggle = document.getElementById('themeToggle');
    if (toggle) {
      const isDark = next === 'dark';
      toggle.setAttribute('aria-label', `Switch to ${isDark ? 'light' : 'dark'} theme`);
      toggle.title = `Switch to ${isDark ? 'light' : 'dark'} theme`;
      toggle.innerHTML = `<i class="bi ${isDark ? 'bi-sun-fill' : 'bi-moon-stars-fill'}" aria-hidden="true"></i>`;
    }
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', next === 'dark' ? '#071225' : '#eef3fb');
    window.requestAnimationFrame(refreshRegisteredCharts);
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme: next } }));
    window.dispatchEvent(new CustomEvent('stump:themechange', { detail: { theme: next } }));
  }

  function initTheme() {
    const initial = root.dataset.bsTheme === 'light' ? 'light' : 'dark';
    applyTheme(initial, false);
    document.getElementById('themeToggle')?.addEventListener('click', () => applyTheme(root.dataset.bsTheme === 'dark' ? 'light' : 'dark'));
  }

  function initHeader() {
    const header = document.querySelector('[data-site-header]');
    const topButton = document.getElementById('scrollTop');
    document.querySelectorAll('[data-reload]').forEach((button) => button.addEventListener('click', () => window.location.reload()));
    const update = () => {
      const scrolled = window.scrollY > 18;
      header?.classList.toggle('is-scrolled', scrolled);
      topButton?.classList.toggle('is-visible', window.scrollY > 520);
    };
    update();
    window.addEventListener('scroll', update, { passive: true });
    topButton?.addEventListener('click', () => window.scrollTo({ top: 0, behavior: reducedMotion.matches ? 'auto' : 'smooth' }));
    document.querySelectorAll('#primaryNavigation .nav-link[href]').forEach((link) => link.addEventListener('click', () => {
      if (window.innerWidth < 992) document.getElementById('primaryNavigation')?.classList.remove('show');
    }));
  }

  function initInputIcons() {
    document.querySelectorAll('.input-icon-wrap').forEach((wrapper) => {
      const input = wrapper.querySelector('input, textarea');
      if (!input) return;
      if (wrapper.querySelector('.password-toggle')) wrapper.classList.add('has-password');
      const syncValueState = () => {
        wrapper.classList.toggle('has-value', String(input.value || '').trim().length > 0);
      };
      input.addEventListener('input', syncValueState);
      input.addEventListener('change', syncValueState);
      input.addEventListener('blur', syncValueState);
      syncValueState();
      // Chrome may restore autofilled values after DOMContentLoaded.
      window.setTimeout(syncValueState, 120);
      window.setTimeout(syncValueState, 600);
    });
  }

  function initPasswordToggles() {
    document.querySelectorAll('[data-password-toggle]').forEach((button) => button.addEventListener('click', () => {
      const input = button.parentElement?.querySelector('input');
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      button.innerHTML = `<i class="bi ${show ? 'bi-eye-slash' : 'bi-eye'}" aria-hidden="true"></i>`;
    }));
  }

  function initFormLoading() {
    document.querySelectorAll('[data-loading-form]:not([data-prediction-form]):not([data-analytics-form])').forEach((form) => form.addEventListener('submit', (event) => {
      if (!form.checkValidity()) {
        event.preventDefault();
        event.stopPropagation();
        form.classList.add('was-validated');
        form.querySelector(':invalid')?.focus();
        return;
      }
      const button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;
      button.disabled = true;
      button.closest('form')?.classList.add('is-loading');
      button.querySelector('.button-label')?.setAttribute('aria-busy', 'true');
    }));
    window.addEventListener('pageshow', () => {
      document.querySelectorAll('[data-loading-form]').forEach((form) => {
        form.classList.remove('is-loading', 'was-validated');
        form.querySelectorAll('button[type="submit"]').forEach((button) => { button.disabled = false; });
      });
    });
  }

  function initConfirmations() {
    document.querySelectorAll('[data-confirm-delete]').forEach((form) => form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirmMessage || 'Delete this record? This cannot be undone.')) event.preventDefault();
    }));
  }

  function initMessages() {
    document.querySelectorAll('[data-django-messages] [data-message-level]').forEach((message) => {
      const tags = message.dataset.messageLevel || '';
      const type = tags.includes('error') ? 'error' : tags.includes('success') ? 'success' : 'info';
      showToast(message.textContent.trim(), type);
    });
  }

  function initPagination() {
    document.querySelectorAll('.pagination a[href]').forEach((link) => {
      const url = new URL(link.href, window.location.href);
      const current = new URL(window.location.href);
      current.searchParams.forEach((value, key) => {
        if (key !== 'page' && !url.searchParams.has(key)) url.searchParams.set(key, value);
      });
      const page = url.searchParams.get('page');
      if (page) url.searchParams.set('page', page);
      link.href = `${url.pathname}${url.search}${url.hash}`;
    });
  }

  function initTooltips() {
    if (!window.bootstrap?.Tooltip) return;
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((element) => new bootstrap.Tooltip(element));
  }

  function readPlayerContext() {
    const ids = ['playerSeasonChartData', 'player-chart-data', 'player-profile-data', 'player-seasonal-data', 'player-performance-data', 'player-data-context'];
    for (const id of ids) {
      const value = getJSON(id);
      if (value && typeof value === 'object' && Object.keys(value).length) return value;
    }
    return null;
  }

  function initPlayerCharts() {
    const page = document.querySelector('[data-player-season-chart]');
    if (!page) return;
    const context = readPlayerContext();
    const canvas = document.getElementById('playerSeasonChart');
    const hasPlayerData = context && (context.labels?.length || context.datasets?.some((dataset) => Array.isArray(dataset.data) && dataset.data.some((item) => numberValue(item) !== null)));
    if (!canvas || !hasPlayerData || typeof Chart === 'undefined') {
      if (canvas) showChartEmpty('playerSeasonChart', true);
      return;
    }
    chartRegistry.get('playerSeasonChart')?.destroy();
    const theme = themeColors();
    const chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: context.labels || [],
        datasets: (context.datasets || []).map((dataset, index) => ({
          ...dataset,
          borderColor: dataset.borderColor || palette()[index % palette().length],
          backgroundColor: dataset.backgroundColor || `${palette()[index % palette().length]}20`,
          tension: 0.32,
          pointRadius: 3,
          yAxisID: ['runs', 'wickets'].includes(dataset.yAxisID) ? dataset.yAxisID : 'rates'
        }))
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: theme.muted, usePointStyle: true, boxWidth: 8 } }, tooltip: { backgroundColor: theme.tooltipBg, titleColor: theme.tooltipTitle, bodyColor: theme.tooltipBody, borderColor: theme.border, borderWidth: 1 } },
        scales: {
          x: { grid: { display: false }, ticks: { color: theme.muted } },
          y: { beginAtZero: true, grid: { color: theme.grid }, ticks: { color: theme.muted } },
          runs: { display: false, beginAtZero: true },
          wickets: { display: false, beginAtZero: true },
          rates: { display: false, beginAtZero: true }
        }
      }
    });
    chartRegistry.set('playerSeasonChart', chart);
    showChartEmpty('playerSeasonChart', false);
  }

  window.CricketUI = {
    themeColors,
    palette,
    findValue,
    normalizeSeries,
    hasSeriesData,
    createChart,
    refreshRegisteredCharts,
    destroyCharts,
    showChartEmpty,
    fetchJSON,
    showToast,
    getJSON,
    shortNumber,
    css,
    numberValue,
    rowsFrom,
    labelFromObject
  };

  window.CricketApp = window.CricketApp || {};
  window.CricketApp.charts = chartRegistry;
  window.CricketApp.showToast = showToast;
  window.CricketApp.theme = { get current() { return root.dataset.bsTheme; }, set: applyTheme, refresh: refreshRegisteredCharts };
  window.CricketApp.refreshDashboardCharts = () => refreshRegisteredCharts();

  // Stable page-level helpers used by the focused workspace scripts. Keeping
  // this small compatibility surface prevents each feature from depending on
  // a particular chart implementation or on a third-party bundle.
  window.StumpAI = Object.freeze({
    qs: (selector, scope = document) => scope.querySelector(selector),
    qsa: (selector, scope = document) => Array.from(scope.querySelectorAll(selector)),
    formatNumber: (value, options = {}) => {
      const number = numberValue(value);
      if (number === null) return '0';
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, ...options }).format(number);
    },
    formatDate: (value, options = {}) => {
      if (!value) return '—';
      const date = new Date(String(value).length === 10 ? `${value}T00:00:00` : value);
      return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', ...options }).format(date);
    },
    prefersReducedMotion: () => reducedMotion.matches,
    getCookie: (name) => document.cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith(`${encodeURIComponent(name)}=`))?.slice(`${encodeURIComponent(name)}=`.length) || '',
    fetchJSON,
    setDataState: (element, state, text) => { if (element) { element.dataset.state = state; if (typeof text === 'string') element.textContent = text; } },
    showToast: (message, level = 'info', title = '') => showToast(title ? `${title}: ${message}` : message, ['success', 'error', 'warning'].includes(level) ? level : 'info'),
    setFormLoading: (form, loading) => { if (!form) return; form.classList.toggle('is-loading', loading); form.setAttribute('aria-busy', loading ? 'true' : 'false'); form.querySelectorAll('button[type="submit"]').forEach((button) => { button.disabled = loading; }); },
    applyTheme
  });

  initTheme();
  initHeader();
  initInputIcons();
  initPasswordToggles();
  initFormLoading();
  initConfirmations();
  initMessages();
  initPagination();
  initTooltips();
  initPlayerCharts();
})();
