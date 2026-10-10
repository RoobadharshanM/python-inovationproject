/* Stump AI — dashboard API, tables and fourteen responsive visualizations */
(() => {
  'use strict';
  const page = document.querySelector('[data-dashboard]');
  if (!page || !window.CricketUI) return;
  const UI = window.CricketUI;
  // The KPI hero and the chart grid are sibling <section> elements. Use the
  // page main scope for shared chart/status nodes instead of querying only the
  // hero section.
  const dashboardRoot = page.closest('main') || document;
  const endpoint = page.dataset.dashboardEndpoint || page.dataset.apiUrl || '/api/dashboard-stats/';
  const status = page.querySelector('[data-dashboard-status]');
  const errorPanel = page.querySelector('#dashboardError');
  const refreshButton = document.getElementById('dashboardRefresh');
  const seasonSelect = document.getElementById('dashboardSeason');
  const lastUpdated = page.querySelector('[data-last-updated]');
  let loading = false;
  let latest = null;

  const unwrap = (payload) => payload?.data && typeof payload.data === 'object' ? payload.data : payload || {};
  const chartSource = (payload) => unwrap(payload)?.charts || {};
  const rows = (value) => Array.isArray(value) ? value : [];
  const value = (row, ...keys) => {
    for (const key of keys) {
      const numeric = UI.numberValue(row?.[key]);
      if (numeric !== null) return numeric;
    }
    return 0;
  };

  function setStatus(state, message) {
    if (status) { status.dataset.state = state; status.textContent = message; }
    if (errorPanel) errorPanel.hidden = state !== 'error';
    if (refreshButton) {
      refreshButton.disabled = state === 'loading';
      refreshButton.querySelector('i')?.classList.toggle('spin', state === 'loading');
    }
    dashboardRoot.querySelectorAll('[data-chart-status]').forEach((node) => node.classList.toggle('is-loading', state === 'loading'));
  }

  function populateSeasons(payload) {
    if (!seasonSelect) return;
    const root = unwrap(payload);
    const seasons = root.filters?.available_seasons || root.charts?.matches_by_season?.map((row) => row.season) || [];
    const selected = new URLSearchParams(window.location.search).get('season') || seasonSelect.value || '';
    seasonSelect.replaceChildren(new Option('All seasons', ''));
    seasons.map(String).forEach((season) => seasonSelect.add(new Option(season, season)));
    if (seasons.map(String).includes(selected)) seasonSelect.value = selected;
  }

  function setKpis(payload) {
    const root = unwrap(payload);
    const kpis = root.kpis || root;
    const map = {
      kpiMatches: ['total_matches', 'matches'], kpiCompleted: ['completed_matches', 'completed'],
      kpiRuns: ['total_runs', 'runs'], kpiAverage: ['average_score', 'average_team_score'],
      kpiTeams: ['total_teams', 'teams'], kpiPlayers: ['total_players', 'players'],
      kpiVenues: ['total_venues', 'venues'], kpiWickets: ['total_wickets', 'wickets']
    };
    Object.entries(map).forEach(([id, keys]) => {
      const element = document.getElementById(id);
      if (!element) return;
      const raw = keys.map((key) => kpis[key]).find((item) => UI.numberValue(item) !== null);
      element.textContent = UI.numberValue(raw) === null ? '—' : UI.shortNumber(raw, ['kpiAverage'].includes(id) ? 1 : 0);
    });
  }

  function seasonTrend(data) {
    const list = rows(data);
    return {
      labels: list.map((row) => String(row.season)),
      datasets: [
        { label: 'Matches', data: list.map((row) => value(row, 'matches')), borderColor: UI.palette()[0], backgroundColor: `${UI.palette()[0]}20`, tension: 0.3 },
        { label: 'Runs', data: list.map((row) => value(row, 'runs')), borderColor: UI.palette()[1], backgroundColor: `${UI.palette()[1]}20`, tension: 0.3, yAxisID: 'runs' },
        { label: 'Average score', data: list.map((row) => value(row, 'average_score')), borderColor: UI.palette()[2], backgroundColor: `${UI.palette()[2]}20`, tension: 0.3 }
      ]
    };
  }

  function chasingComparison(batting, chasing) {
    const first = rows(batting);
    const second = rows(chasing);
    const ids = new Map();
    first.forEach((row) => ids.set(String(row.team_id ?? row.team), row.team));
    second.forEach((row) => ids.set(String(row.team_id ?? row.team), row.team));
    const labels = [...ids.values()].map(String);
    return {
      labels,
      datasets: [
        { label: 'Won batting first', data: labels.map((label) => value(first.find((row) => String(row.team ?? row.team_id) === label), 'wins')), backgroundColor: UI.palette()[0], borderColor: UI.css('--border'), borderWidth: 2 },
        { label: 'Won chasing', data: labels.map((label) => value(second.find((row) => String(row.team ?? row.team_id) === label), 'wins')), backgroundColor: UI.palette()[1], borderColor: UI.css('--border'), borderWidth: 2 }
      ]
    };
  }

  function scatterFromPlayers(batters, bowlers) {
    const bowlMap = new Map(rows(bowlers).map((row) => [String(row.player_id ?? row.player), row]));
    const points = rows(batters).map((row) => {
      const id = String(row.player_id ?? row.player);
      const bowling = bowlMap.get(id);
      return bowling ? { x: value(row, 'runs'), y: value(bowling, 'wickets'), label: row.player || id } : null;
    }).filter(Boolean);
    return { points };
  }

  function heatmapData(seasonRows) {
    const list = rows(seasonRows);
    if (!list.length) return null;
    return {
      rows: list.map((row) => String(row.season)),
      columns: ['Matches', 'Runs', 'Average score'],
      values: list.map((row) => [value(row, 'matches'), value(row, 'runs'), value(row, 'average_score')])
    };
  }

  function renderCharts(payload) {
    const charts = chartSource(payload);
    UI.createChart('matchesBySeasonChart', 'line', seasonTrend(charts.matches_by_season));
    UI.createChart('teamWinsChart', 'bar', charts.team_wins, { horizontal: true });
    UI.createChart('teamRunsChart', 'bar', charts.runs_by_team, { horizontal: true });
    UI.createChart('teamAverageChart', 'bar', charts.average_score_by_team, { horizontal: true });
    UI.createChart('topBattersChart', 'bar', charts.top_batters, { horizontal: true });
    UI.createChart('topBowlersChart', 'bar', charts.top_bowlers, { horizontal: true });
    UI.createChart('winPercentagesChart', 'doughnut', rows(charts.win_percentages).map((row) => ({ team: row.team, wins: value(row, 'win_percentage') })));
    UI.createChart('tossDecisionsChart', 'doughnut', charts.toss_results);
    UI.createChart('matchesByVenueChart', 'bar', charts.matches_by_venue, { horizontal: true });
    UI.createChart('battingChasingChart', 'doughnut', chasingComparison(charts.batting_first_wins, charts.chasing_wins));
    UI.createChart('wicketsChart', 'bar', charts.wickets_by_team, { horizontal: true });
    UI.createChart('teamRadarChart', 'radar', charts.team_radar);
    UI.createChart('playerOutputScatterChart', 'scatter', charts.player_output || scatterFromPlayers(charts.top_batters, charts.top_bowlers), { label: 'Player' });
    renderHeatmap(heatmapData(charts.matches_by_season));
  }

  function renderHeatmap(matrix) {
    const container = dashboardRoot.querySelector('[data-heatmap]');
    if (!container) return;
    container.replaceChildren();
    if (!matrix?.rows?.length || !matrix?.columns?.length) {
      const empty = document.createElement('div');
      empty.className = 'chart-empty heatmap-empty';
      empty.innerHTML = '<i class="bi bi-grid-3x3-gap" aria-hidden="true"></i><span>No matrix data available</span>';
      container.append(empty);
      return;
    }
    const numeric = matrix.values.flat().filter((item) => item !== null && item !== undefined);
    const min = Math.min(...numeric);
    const max = Math.max(...numeric);
    const span = max - min || 1;
    const table = document.createElement('table');
    table.className = 'heatmap-table';
    const head = document.createElement('thead');
    const heading = document.createElement('tr');
    const corner = document.createElement('th'); corner.scope = 'col'; corner.textContent = 'Season'; heading.append(corner);
    matrix.columns.forEach((label) => { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; heading.append(cell); });
    head.append(heading); table.append(head);
    const body = document.createElement('tbody');
    matrix.rows.forEach((label, rowIndex) => {
      const row = document.createElement('tr');
      const rowHead = document.createElement('th'); rowHead.scope = 'row'; rowHead.textContent = label; row.append(rowHead);
      matrix.columns.forEach((column, columnIndex) => {
        const cell = document.createElement('td');
        const item = UI.numberValue(matrix.values[rowIndex]?.[columnIndex]);
        cell.className = 'heatmap-cell';
        if (item === null) { cell.textContent = '—'; cell.style.background = 'var(--surface-soft)'; }
        else {
          const intensity = (item - min) / span;
          const hue = 220 - intensity * 220;
          cell.style.background = `linear-gradient(145deg, hsl(${hue} 82% ${35 + intensity * 15}%), hsl(${hue} 78% ${24 + intensity * 14}%))`;
          cell.textContent = UI.shortNumber(item, 1);
          cell.title = `${label} · ${column}: ${UI.shortNumber(item, 2)}`;
        }
        row.append(cell);
      });
      body.append(row);
    });
    table.append(body); container.append(table);
    const legend = document.createElement('div'); legend.className = 'heatmap-legend';
    legend.innerHTML = `<span>${UI.shortNumber(min, 1)}</span><span class="heatmap-legend-bar" aria-hidden="true"></span><span>${UI.shortNumber(max, 1)}</span>`;
    container.append(legend);
  }

  function cell(row, content) { const td = document.createElement('td'); if (row?.numeric) td.className = 'numeric'; td.textContent = content ?? '—'; return td; }

  function renderTables(payload) {
    const root = unwrap(payload);
    const comparisonBody = document.getElementById('dashboardComparisonBody');
    const recentBody = document.getElementById('dashboardRecentBody');
    const teams = rows(root.charts?.team_comparison);
    if (comparisonBody) {
      comparisonBody.replaceChildren();
      teams.forEach((team) => {
        const row = document.createElement('tr');
        const name = document.createElement('th'); name.scope = 'row'; name.textContent = team.team; row.append(name);
        [team.matches, team.wins, team.win_percentage, team.runs, team.average_score, team.wickets].forEach((item) => row.append(cell(null, UI.numberValue(item) === null ? '—' : UI.shortNumber(item, 1))));
        comparisonBody.append(row);
      });
      if (!teams.length) { const row = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 7; td.className = 'table-empty'; td.textContent = 'No team metrics are available.'; row.append(td); comparisonBody.append(row); }
    }
    if (recentBody) {
      recentBody.replaceChildren();
      rows(root.recent_matches).forEach((match) => {
        const row = document.createElement('tr');
        row.append(cell(null, new Intl.DateTimeFormat(undefined, { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(match.date))));
        const matchCell = document.createElement('td'); const link = document.createElement('a'); link.className = 'table-link'; link.href = `/matches/${match.id}/`; link.textContent = `${match.team1} v ${match.team2}`; matchCell.append(link); row.append(matchCell);
        row.append(cell(null, match.winner || 'No result')); recentBody.append(row);
      });
      if (!rows(root.recent_matches).length) { const row = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 3; td.className = 'table-empty'; td.textContent = 'No recent matches are available.'; row.append(td); recentBody.append(row); }
    }
  }

  function showEmpty() {
    dashboardRoot.querySelector('.dashboard-chart-grid')?.setAttribute('aria-busy', 'false');
    ['matchesBySeasonChart','teamWinsChart','teamRunsChart','teamAverageChart','topBattersChart','topBowlersChart','winPercentagesChart','tossDecisionsChart','matchesByVenueChart','battingChasingChart','wicketsChart','teamRadarChart','playerOutputScatterChart'].forEach((id) => UI.showChartEmpty(id, true));
    renderHeatmap(null);
  }

  async function load() {
    if (loading) return;
    loading = true; setStatus('loading', 'Loading live database statistics…');
    const season = seasonSelect?.value || '';
    const url = `${endpoint}${endpoint.includes('?') ? '&' : '?'}${season ? `season=${encodeURIComponent(season)}` : ''}`;
    try {
      const payload = await UI.fetchJSON(url, { cache: 'no-store' });
      latest = payload; populateSeasons(payload); setKpis(payload); renderCharts(payload); renderTables(payload); dashboardRoot.querySelector('.dashboard-chart-grid')?.setAttribute('aria-busy', 'false');
      const generated = unwrap(payload).generated_at;
      if (lastUpdated) lastUpdated.textContent = `Updated ${new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(generated ? new Date(generated) : new Date())}`;
      setStatus('ready', unwrap(payload).empty ? 'No records are available for this selection.' : 'Live database statistics are up to date.');
    } catch (error) {
      showEmpty(); setStatus('error', 'Dashboard data is unavailable.');
      page.querySelector('[data-dashboard-error-message]').textContent = error.message || 'The request could not be completed. No sample values were substituted.';
      UI.showToast('Dashboard data could not be loaded. No sample values were used.', 'error');
      console.warn('Dashboard request failed:', error);
    } finally { loading = false; }
  }

  refreshButton?.addEventListener('click', load);
  seasonSelect?.addEventListener('change', () => {
    const url = new URL(window.location.href); if (seasonSelect.value) url.searchParams.set('season', seasonSelect.value); else url.searchParams.delete('season');
    history.replaceState({}, '', url); load();
  });
  document.addEventListener('themechange', () => { if (latest) window.CricketApp?.refreshDashboardCharts?.(); });
  window.CricketApp = window.CricketApp || {}; window.CricketApp.refreshDashboardCharts = () => { renderCharts(latest); if (latest) setKpis(latest); };
  load();
})();
