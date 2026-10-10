/* Stump AI — CSRF-aware prediction forms, model status and saved result charts */
(() => {
  'use strict';
  const UI = window.CricketUI || { showToast(message, type) { console.log(`[${type}] ${message}`); }, getJSON() { return null; }, createChart() { return null; }, showChartEmpty() {} };

  function cookie(name) {
    const prefix = `${name}=`;
    const match = document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith(prefix));
    return match ? decodeURIComponent(match.slice(prefix.length)) : '';
  }

  function validateSecurity(form) {
    const field = form.querySelector('input[name="csrfmiddlewaretoken"]');
    const token = field?.value || cookie('csrftoken');
    if (!token) { form.dataset.csrfReady = 'false'; return false; }
    if (field && !field.value) field.value = token;
    form.dataset.csrfReady = 'true';
    return true;
  }

  function statusNode(form) {
    let status = form.querySelector('[data-form-status]');
    if (!status) {
      status = document.createElement('div');
      status.className = 'form-status';
      status.dataset.formStatus = '';
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      form.append(status);
    }
    return status;
  }

  function markServerFields(form) {
    form.querySelectorAll('.invalid-feedback.d-block').forEach((error) => {
      const field = error.closest('.input-group, .mb-3, .col-12, .col-md-6, .col-sm-6')?.querySelector('input, select, textarea');
      field?.classList.add('is-invalid');
      field?.setAttribute('aria-invalid', 'true');
    });
  }

  function serverErrors(form) {
    const first = form.querySelector('.is-invalid, .alert-danger, .invalid-feedback.d-block');
    if (!first) return null;
    let message = first.textContent.trim();
    if (!first.matches('.alert-danger')) {
      const field = first.closest('.input-group, .mb-3, .col-12, .col-md-6, .col-sm-6')?.querySelector('label');
      message = `${field?.textContent.trim() || 'A field'} needs your attention.`;
    }
    return { first, message: message || 'Please correct the highlighted fields.' };
  }

  function showErrors(form, message) {
    const status = statusNode(form);
    status.textContent = message;
    status.classList.add('is-error');
    UI.showToast(message, 'error');
  }

  function initFieldRelationships(form) {
    const batting = form.elements.namedItem('batting_team');
    const bowling = form.elements.namedItem('bowling_team');
    const tossWinner = form.elements.namedItem('toss_winner');
    const currentScore = form.elements.namedItem('current_score');
    const overs = form.elements.namedItem('overs_completed');
    const runRate = form.elements.namedItem('current_run_rate');

    const updateTeams = () => {
      if (!batting || !bowling) return;
      const selected = new Set([batting.value, bowling.value].filter(Boolean));
      if (tossWinner) {
        Array.from(tossWinner.options).forEach((option) => {
          if (option.value) option.disabled = Boolean(selected.size && !selected.has(option.value));
        });
        if (tossWinner.value && !selected.has(tossWinner.value)) tossWinner.value = '';
      }
      const duplicate = Boolean(batting.value && bowling.value && batting.value === bowling.value);
      batting.setCustomValidity(duplicate ? 'Choose two different teams.' : '');
      bowling.setCustomValidity(duplicate ? 'Choose two different teams.' : '');
    };

    const updateRunRate = () => {
      if (!currentScore || !overs || !runRate || runRate.value || runRate.dataset.userEdited === 'true') return;
      const score = Number(currentScore.value);
      const completed = Number(overs.value);
      if (Number.isFinite(score) && Number.isFinite(completed) && score >= 0 && completed > 0) runRate.value = (score * 6 / completed).toFixed(2);
    };

    [batting, bowling].filter(Boolean).forEach((field) => field.addEventListener('change', updateTeams));
    [currentScore, overs].filter(Boolean).forEach((field) => field.addEventListener('input', updateRunRate));
    runRate?.addEventListener('input', () => { runRate.dataset.userEdited = 'true'; });
    form.addEventListener('reset', () => window.setTimeout(() => { if (runRate) runRate.dataset.userEdited = 'false'; updateTeams(); }, 0));
    updateTeams();
  }

  function initForms() {
    document.querySelectorAll('[data-prediction-form]').forEach((form) => {
      validateSecurity(form);
      markServerFields(form);
      initFieldRelationships(form);
      form.querySelectorAll('input, select, textarea').forEach((field) => field.addEventListener('input', () => {
        field.classList.remove('is-invalid');
        field.removeAttribute('aria-invalid');
        const status = statusNode(form);
        if (status.classList.contains('is-error')) { status.textContent = ''; status.classList.remove('is-error'); }
      }));

      form.addEventListener('submit', (event) => {
        if (!validateSecurity(form)) { event.preventDefault(); showErrors(form, 'The form security token is missing. Refresh the page and try again.'); return; }
        if (!form.checkValidity()) {
          event.preventDefault(); event.stopPropagation(); form.classList.add('was-validated');
          const invalid = form.querySelector(':invalid'); invalid?.focus(); showErrors(form, 'Complete all required match fields before generating a forecast.'); return;
        }
        const button = form.querySelector('button[type="submit"]');
        if (!button || button.disabled) return;
        button.disabled = true; form.classList.add('is-loading'); form.setAttribute('aria-busy', 'true');
        const label = button.querySelector('.button-label');
        if (label) { label.dataset.originalText = label.textContent; label.textContent = form.dataset.predictionKind === 'outcome' ? 'Estimating the outcome' : 'Running the score model'; }
        const status = statusNode(form); status.classList.remove('is-error'); status.textContent = 'Submitting securely. The model may take a moment to respond.';
      });

      const errors = serverErrors(form);
      if (errors) requestAnimationFrame(() => { showErrors(form, errors.message); errors.first.focus?.({ preventScroll: true }); errors.first.scrollIntoView?.({ block: 'center' }); });
    });
  }

  async function initModelStatus() {
    const page = document.querySelector('[data-prediction-page]');
    const card = page?.querySelector('[data-model-status]');
    const url = page?.dataset.modelStatusUrl;
    if (!card || !url) return;
    const badge = card.querySelector('[data-model-status-badge]');
    const detail = card.querySelector('[data-model-status-detail]');
    try {
      const endpoint = new URL(url, window.location.origin);
      endpoint.searchParams.set('kind', page.dataset.predictionKind || 'score');
      const payload = await UI.fetchJSON(endpoint, { cache: 'no-store', timeout: 9000 });
      const trained = payload.trained ?? payload.available ?? payload.ok;
      card.dataset.state = trained ? 'ready' : 'warning';
      if (badge) { badge.textContent = trained ? 'Ready' : 'Unavailable'; badge.className = `status-badge ms-auto ${trained ? 'status-badge--success' : 'status-badge--warning'}`; }
      const version = payload.model_version || payload.version || payload.artifact || '';
      const message = payload.message || payload.detail || (trained ? 'The model artifact is available.' : 'No trained model artifact is available yet.');
      if (detail) detail.textContent = version ? `${message} Version: ${version}` : message;
    } catch (error) {
      card.dataset.state = 'error';
      if (badge) { badge.textContent = 'Unavailable'; badge.className = 'status-badge status-badge--warning ms-auto'; }
      if (detail) detail.textContent = 'Model readiness could not be checked. The form will still report server validation errors.';
      console.warn('Model status request failed:', error);
    }
  }

  function initResultChart() {
    const page = document.querySelector('[data-prediction-result]');
    if (!page) return;
    const result = UI.getJSON?.('predictionResultData');
    const kind = page.dataset.predictionType || page.dataset.predictionKind || 'score';
    if (kind === 'outcome') {
      const probabilities = result?.probabilities || result?.outcome_probabilities || {};
      const series = UI.normalizeSeries?.(probabilities);
      if (series && UI.hasSeriesData?.(series)) UI.createChart('predictionResultChart', 'doughnut', probabilities);
      else UI.showChartEmpty?.('predictionResultChart', true);
      return;
    }
    const point = result?.predicted_score ?? result?.prediction;
    const low = result?.prediction_low ?? result?.range_low;
    const high = result?.prediction_high ?? result?.range_high;
    const pointNumber = UI.numberValue?.(point);
    const lowNumber = UI.numberValue?.(low);
    const highNumber = UI.numberValue?.(high);
    const hasPoint = pointNumber !== null && pointNumber !== undefined;
    const hasRange = lowNumber !== null && lowNumber !== undefined && highNumber !== null && highNumber !== undefined;
    if (hasPoint) {
      const labels = hasRange ? ['Low', 'Estimate', 'High'] : ['Estimated score'];
      const data = hasRange ? [lowNumber, pointNumber, highNumber] : [pointNumber];
      UI.createChart?.('predictionResultChart', 'bar', { labels, datasets: [{ label: 'Runs', data }] });
    } else UI.showChartEmpty?.('predictionResultChart', true);
  }

  initForms();
  initModelStatus();
  initResultChart();

  window.addEventListener('pageshow', () => {
    document.querySelectorAll('[data-prediction-form]').forEach((form) => {
      form.classList.remove('is-loading'); form.removeAttribute('aria-busy');
      form.querySelectorAll('button[type="submit"]').forEach((button) => { if (!button.dataset.serverDisabled === 'true') button.disabled = false; });
      form.querySelectorAll('.button-label[data-original-text]').forEach((label) => { label.textContent = label.dataset.originalText; });
    });
  });
})();
