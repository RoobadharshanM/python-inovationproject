/* Stump AI — unified analytics API renderer */
(() => {
  'use strict';
  const page = document.querySelector('[data-analytics]');
  if (!page || !window.CricketUI) return;
  const UI = window.CricketUI;
  const form = page.querySelector('[data-analytics-form]');
  const endpoint = '/api/analytics-data/';
  const status = page.querySelector('[data-analytics-status]');
  const errorPanel = page.querySelector('#analyticsError');
  const summary = document.getElementById('analyticsSummary');
  const details = document.getElementById('analyticsDetails');
  const raw = document.getElementById('analyticsRaw');
  let loading = false;
  let latest = null;

  const unwrap = (payload) => payload?.data && typeof payload.data === 'object' ? payload.data : payload || {};
  const list = (value) => Array.isArray(value) ? value : [];
  const num = (row, ...keys) => { for (const key of keys) { const item = UI.numberValue(row?.[key]); if (item !== null) return item; } return 0; };

  function queryString() {
    if (!form) return '';
    const params = new URLSearchParams();
    new FormData(form).forEach((value, key) => { const clean = String(value).trim(); if (clean) params.set(key, clean); });
    return params.toString();
  }

  function setLoading(value, finalState = 'ready') {
    loading = value;
    summary?.setAttribute('aria-busy', String(value));
    details?.setAttribute('aria-busy', String(value));
    if (status) {
      status.dataset.state = value ? 'loading' : finalState;
      status.textContent = value
        ? 'Refreshing analytics…'
        : finalState === 'error'
          ? 'Analytics are unavailable.'
          : finalState === 'empty'
            ? 'No records match the active filters.'
            : 'Live analytics are up to date.';
    }
    if (errorPanel) errorPanel.hidden = value || finalState !== 'error';
    if (form) {
      const button = form.querySelector('button[type="submit"]');
      if (button) { button.disabled = value; button.classList.toggle('is-loading', value); }
    }
    document.querySelectorAll('[data-chart-status]').forEach((node) => node.classList.toggle('is-loading', value));
  }

  function setSummary(payload) {
    const data = unwrap(payload);
    const values = data.overview?.summary || {};
    const map = {
      matches: values.matches ?? values.total_matches,
      teams: values.teams ?? values.total_teams,
      venues: values.venues ?? values.total_venues,
      average_score: values.average_team_score ?? values.average_score
    };
    Object.entries(map).forEach(([key, value]) => document.querySelectorAll(`[data-summary="${key}"]`).forEach((node) => { node.textContent = UI.numberValue(value) === null ? '—' : UI.shortNumber(value, key === 'average_score' ? 1 : 0); }));
  }

  function seasonSeries(data) {
    const seasons = list(data.season_stats?.seasons || data.overview?.seasons);
    return {
      labels: seasons.map((row) => String(row.season)),
      datasets: [
        { label: 'Total runs', data: seasons.map((row) => num(row, 'total_runs', 'runs')), borderColor: UI.palette()[0], backgroundColor: `${UI.palette()[0]}20`, tension: 0.3, yAxisID: 'runs' },
        { label: 'Wickets', data: seasons.map((row) => num(row, 'wickets')), borderColor: UI.palette()[1], backgroundColor: `${UI.palette()[1]}20`, tension: 0.3 },
        { label: 'Average score', data: seasons.map((row) => num(row, 'average_team_score', 'average_score')), borderColor: UI.palette()[2], backgroundColor: `${UI.palette()[2]}20`, tension: 0.3 }
      ]
    };
  }

  function normalizedProfiles(rows, metrics, limit = 5) {
    const selected = rows.filter(Boolean).slice(0, limit);
    if (!selected.length) return { labels: metrics.map((metric) => metric[0]), datasets: [] };
    const maxima = metrics.map(([, ...keys]) => Math.max(...selected.map((row) => num(row, ...keys)), 0));
    return {
      labels: metrics.map(([label]) => label),
      datasets: selected.map((row, index) => ({
        label: row.name || row.team || row.player || `Entity ${index + 1}`,
        data: metrics.map(([, ...keys], metricIndex) => maxima[metricIndex] ? Number((num(row, ...keys) / maxima[metricIndex] * 100).toFixed(2)) : 0)
      }))
    };
  }

  function teamRadar(data) {
    const comparison = data.team_comparison;
    const metrics = [['Played', 'matches', 'played'], ['Wins', 'wins'], ['Win %', 'win_percentage'], ['Runs', 'runs'], ['Avg score', 'average_score'], ['Wickets', 'wickets']];
    const source = comparison ? [comparison.team_a, comparison.team_b] : list(data.team_stats?.teams);
    return normalizedProfiles(source, metrics, 5);
  }

  function playerBars(data) {
    const comparison = data.player_comparison;
    const metrics = [['Runs', 'runs'], ['Average', 'average', 'batting_average'], ['Strike rate', 'strike_rate'], ['4s', 'fours'], ['6s', 'sixes'], ['Wickets', 'wickets'], ['Economy', 'economy']];
    const source = comparison ? [comparison.player_a, comparison.player_b] : list(data.player_stats?.players).slice(0, 5);
    const profile = normalizedProfiles(source, metrics, comparison ? 2 : 5);
    // Economy and bowling average are better on a separate scale; cap all profile
    // dimensions to 0-100 so grouped bars remain visually comparable.
    profile.datasets = profile.datasets.map((dataset) => ({ ...dataset, data: dataset.data.map((item) => Math.max(0, Math.min(100, Number(item) || 0))) }));
    return profile;
  }

  function venueRows(data) { return list(data.venue_stats?.venues || data.venue_analysis?.metrics); }

  function winRows(data) {
    const comparison = data.team_comparison;
    if (comparison?.team_a && comparison?.team_b) return [comparison.team_a, comparison.team_b];
    return list(data.team_stats?.teams);
  }

  function scatter(data) {
    const supplied = list(data.overview?.player_output?.points);
    if (supplied.length) return { points: supplied };
    const batters = list(data.overview?.top_batters);
    const bowlers = list(data.overview?.top_bowlers);
    const bowlMap = new Map(bowlers.map((row) => [String(row.player_id ?? row.player), row]));
    const points = batters.map((row) => {
      const id = String(row.player_id ?? row.player); const bowling = bowlMap.get(id);
      return bowling ? { x: num(row, 'runs'), y: num(bowling, 'wickets'), label: row.player || id } : null;
    }).filter(Boolean);
    return { points };
  }

  function renderCharts(payload) {
    const data = unwrap(payload);
    UI.createChart('analyticsTrendChart', 'line', seasonSeries(data));
    UI.createChart('teamComparisonChart', 'radar', teamRadar(data));
    UI.createChart('playerComparisonChart', 'bar', playerBars(data), { horizontal: true });
    UI.createChart('venuePerformanceChart', 'bar', venueRows(data), { horizontal: true });
    UI.createChart('analyticsResultChart', 'doughnut', winRows(data).map((row) => ({ team: row.name || row.team, wins: num(row, 'wins') })));
    UI.createChart('analyticsEfficiencyChart', 'scatter', scatter(data), { label: 'Player' });
  }

  function tableNode(headers, rows, emptyMessage) {
    const wrap = document.createElement('div'); wrap.className = 'table-wrap';
    const table = document.createElement('table'); table.className = 'table';
    const thead = document.createElement('thead'); const heading = document.createElement('tr');
    headers.forEach((label) => { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; heading.append(cell); }); thead.append(heading); table.append(thead);
    const body = document.createElement('tbody');
    if (!rows.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = headers.length; td.className = 'table-empty'; td.textContent = emptyMessage; tr.append(td); body.append(tr); }
    rows.forEach((values) => { const tr = document.createElement('tr'); values.forEach((value) => { const td = document.createElement('td'); td.textContent = value ?? '—'; tr.append(td); }); body.append(tr); });
    table.append(body); wrap.append(table); return wrap;
  }

  function renderDetails(payload) {
    if (!details) return;
    const data = unwrap(payload); details.replaceChildren();
    if (data.team_comparison) {
      const comparison = data.team_comparison;
      const article = document.createElement('article'); article.className = 'table-panel';
      const header = document.createElement('header'); header.className = 'table-panel-header'; const title = document.createElement('h2'); title.textContent = 'Team comparison summary'; header.append(title); article.append(header);
      article.append(tableNode(['Team', 'Played', 'Won', 'Win %', 'Runs', 'Average', 'Wickets'], [comparison.team_a, comparison.team_b].filter(Boolean).map((team) => [team.name, UI.shortNumber(team.played, 0), UI.shortNumber(team.wins, 0), UI.shortNumber(team.win_percentage, 1), UI.shortNumber(team.runs, 0), UI.shortNumber(team.average_score, 1), UI.shortNumber(team.wickets, 0)]), 'No team comparison data is available.'));
      details.append(article);
    } else if (data.player_comparison) {
      const comparison = data.player_comparison; const article = document.createElement('article'); article.className = 'table-panel';
      const header = document.createElement('header'); header.className = 'table-panel-header'; const title = document.createElement('h2'); title.textContent = 'Player comparison summary'; header.append(title); article.append(header);
      article.append(tableNode(['Player', 'Runs', 'Average', 'Strike rate', 'Wickets', 'Economy'], [comparison.player_a, comparison.player_b].filter(Boolean).map((player) => [player.name, UI.shortNumber(player.runs, 0), UI.shortNumber(player.average, 2), UI.shortNumber(player.strike_rate, 2), UI.shortNumber(player.wickets, 0), UI.shortNumber(player.economy, 2)]), 'No player comparison data is available.'));
      details.append(article);
    } else if (data.player_trend) {
      const trend = data.player_trend;
      const article = document.createElement('article'); article.className = 'table-panel';
      const header = document.createElement('header'); header.className = 'table-panel-header'; const title = document.createElement('h2'); title.textContent = `${trend.player?.name || 'Player'} season trend`; header.append(title); article.append(header);
      article.append(tableNode(['Season', 'Runs', 'Average', 'Strike rate', 'Wickets', 'Economy'], list(trend.points).map((point) => [point.season, UI.shortNumber(point.runs, 0), UI.shortNumber(point.average, 2), UI.shortNumber(point.strike_rate, 2), UI.shortNumber(point.wickets, 0), UI.shortNumber(point.economy, 2)]), 'No seasonal player data is available.'));
      details.append(article);
    } else if (data.venue_analysis) {
      const venue = data.venue_analysis;
      const article = document.createElement('article'); article.className = 'table-panel';
      const header = document.createElement('header'); header.className = 'table-panel-header'; const title = document.createElement('h2'); title.textContent = 'Venue analysis summary'; header.append(title); article.append(header);
      article.append(tableNode(['Venue', 'Matches', 'Average first innings', 'Bat first wins', 'Chasing wins', 'Boundaries', 'Wickets'], list(venue.metrics).map((row) => [row.venue, UI.shortNumber(row.matches, 0), UI.shortNumber(row.average_first_innings_score, 1), UI.shortNumber(row.batting_first_wins, 0), UI.shortNumber(row.chasing_wins, 0), UI.shortNumber(row.boundaries, 0), UI.shortNumber(row.wickets, 0)]), 'No venue data is available for this selection.'));
      details.append(article);
    } else {
      const article = document.createElement('article'); article.className = 'table-panel';
      const header = document.createElement('header'); header.className = 'table-panel-header'; const title = document.createElement('h2'); title.textContent = 'Recent filtered matches'; header.append(title); article.append(header);
      article.append(tableNode(['Date', 'Match', 'Venue', 'Winner'], list(data.overview?.recent_matches).map((match) => [match.date, `${match.team1} v ${match.team2}`, match.venue, match.winner || 'No result']), 'No matches match the active filters.'));
      details.append(article);
    }
    details.setAttribute('aria-busy', 'false');
  }

  function showEmpty() {
    ['analyticsTrendChart','teamComparisonChart','playerComparisonChart','venuePerformanceChart','analyticsResultChart','analyticsEfficiencyChart'].forEach((id) => UI.showChartEmpty(id, true));
    if (details) details.innerHTML = '<div class="error-state"><i class="bi bi-database-exclamation state-icon" aria-hidden="true"></i><h2>Analytics unavailable</h2><p>No chart data was returned. No sample values have been used.</p></div>';
  }

  async function load({ push = false, notify = true } = {}) {
    if (loading) return;
    let failed = false;
    let finalState = 'ready';
    setLoading(true); const params = queryString();
    try {
      const payload = await UI.fetchJSON(`${endpoint}${params ? `?${params}` : ''}`, { cache: 'no-store' });
      latest = payload; setSummary(payload); renderCharts(payload); renderDetails(payload);
      if (unwrap(payload).overview?.empty) finalState = 'empty';
      if (raw) raw.textContent = JSON.stringify(unwrap(payload), null, 2);
      const pageURL = `${form?.getAttribute('action') || window.location.pathname}${params ? `?${params}` : ''}`;
      if (push) history.pushState({ analytics: true }, '', pageURL);
      if (notify) UI.showToast('Analytics updated from the active filters.', 'success', 2600);
    } catch (error) {
      failed = true;
      showEmpty();
      const message = page.querySelector('[data-analytics-error-message]');
      if (message) message.textContent = error.message || 'The analytics request could not be completed.';
      if (raw) raw.textContent = 'No analytics response was received.';
      if (notify) UI.showToast('Analytics could not be refreshed. No sample values were used.', 'error');
      console.warn('Analytics request failed:', error);
    } finally {
      loading = false;
      setLoading(false, failed ? 'error' : finalState);
    }
  }

  function syncForm() {
    if (!form) return; const params = new URLSearchParams(window.location.search);
    new FormData(form).forEach((_, key) => { const field = form.elements.namedItem(key); if (field && 'value' in field) field.value = params.get(key) || ''; });
  }

  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!form.checkValidity()) { event.stopPropagation(); form.classList.add('was-validated'); form.querySelector(':invalid')?.focus(); return; }
    load({ push: true });
  });
  window.addEventListener('popstate', () => { syncForm(); load({ push: false, notify: false }); });
  load({ push: false, notify: false });
})();
