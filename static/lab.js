'use strict';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = (n, d = 1) => Number.isFinite(n) ? `${(n * 100).toFixed(d)}%` : '—';
const num = (n, d = 3) => Number.isFinite(n) ? n.toFixed(d) : '—';
const pp = n => Number.isFinite(n) ? `${n < 0 ? '−' : '+'}${Math.abs(n * 100).toFixed(1)} pp` : '—';
const ms = n => Number.isFinite(n) ? `${(n / 1000).toFixed(2)} s` : '—';
const json = value => esc(JSON.stringify(value, null, 2));
const clone = value => structuredClone(value);
const scenario = key => ({healthy:'Current policy', stale_policy:'Outdated policy', missing_context:'Missing context'}[key] || key);
const verdict = (pass, text) => `<span class="verdict ${pass === null ? 'neutral' : pass ? 'pass' : 'fail'}">${esc(text || (pass ? 'Pass' : 'Fail'))}</span>`;
const fold = (title, value, id = '') => `<details class="evidence-fold"${id ? ` id="${id}"` : ''}><summary>${esc(title)}</summary><pre>${json(value)}</pre></details>`;
let catalog, state, currentRubric, currentRecord, originId = null, watchedJob = null, selectedDataset = null;
let connectionOpen = false, streamStateCurrent = false, heartbeat = 0, requestPending = false, savedDraft = null, driftResult, playTimer, replayTimer, driftTimer;
let recordRevision = 0, datasetRevision = '', replaySequence = 0, driftSequence = 0, userEdited = false;
const isLive = () => connectionOpen && streamStateCurrent && Date.now() - heartbeat < 25000;
const draftKey = () => JSON.stringify([readCase(), readRubric()]);
function updateDraftStatus() {
  let changed = false;
  if (currentRecord) { try { changed = savedDraft !== draftKey(); } catch { changed = true; } }
  $('result-draft-notice').hidden = !changed;
  if (currentRecord) $('result-source').textContent = changed ? 'Previous saved result' : currentRecord.status === 'completed' ? 'Saved native JevEval result' : currentRecord.status === 'generated' ? 'Saved Model answer · unscored' : currentRecord.status;
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  let value;
  try { value = await response.json(); } catch { throw new Error('The local server returned an unreadable response.'); }
  if (!response.ok) {
    const detail = value.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(e => `${e.loc?.slice(1).join('.')}: ${e.msg}`).join(' · ') : 'The request failed.');
  }
  return value;
}
function notice(text, error = false) {
  $('lab-notice').textContent = text; $('lab-notice').hidden = !text;
  $('lab-notice').classList.toggle('audit-failed', error);
}
function safely(fn) { return (...args) => Promise.resolve().then(() => fn(...args)).catch(e => notice(e.message, true)); }
function selectedView() { return document.querySelector('.lab-nav [aria-selected="true"]').dataset.view; }
function showView(view, focus = false) {
  if (!['workbench','experiments','drift','guide'].includes(view)) view = 'workbench';
  document.querySelectorAll('[data-view]').forEach(button => {
    const active = button.dataset.view === view;
    button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
    $(`view-${button.dataset.view}`).hidden = !active;
    if (active && focus) button.focus();
  });
  history.replaceState(null, '', `#${view}`);
  if (view === 'drift' && catalog) safely(() => loadDrift())(); else stopPlayback();
}

function preset(id) { return catalog.presets.find(p => p.id === id); }
function setCase(value, source = null, label = 'Editable test case') {
  $('case-input').value = value.input || '';
  $('case-output').value = value.actual_output || '';
  $('case-expected').value = value.expected_output || '';
  $('case-context').value = (value.context || []).join('\n\n--- chunk ---\n\n');
  $('case-retrieval').value = (value.retrieval_context || []).join('\n\n--- chunk ---\n\n');
  originId = source; userEdited = false; $('answer-provenance').textContent = label;
  updateDraftStatus();
}
function readCase() {
  const chunks = id => $(id).value.trim() ? $(id).value.split(/\n\n--- chunk ---\n\n/).map(s => s.trim()).filter(Boolean) : null;
  return {input:$('case-input').value.trim(), actual_output:$('case-output').value.trim(), expected_output:$('case-expected').value.trim() || null,
    context:chunks('case-context'), retrieval_context:chunks('case-retrieval')};
}
function loadTicket() {
  const ticket = catalog.tickets.find(t => t.id === $('ticket-select').value);
  setCase({...ticket, context:[catalog.policy], retrieval_context:[catalog.policy]}, null, 'Synthetic ticket · no generated answer yet');
}
function loadRubric(id) {
  const entry = preset(id); setRubric(entry.rubric, entry.description);
}
function setRubric(config, description = 'Saved rubric loaded. Changes apply to the next fresh evaluation.') {
  currentRubric = clone(config);
  const match = catalog.presets.find(p => JSON.stringify(p.rubric) === JSON.stringify(config)) || catalog.presets.find(p => p.rubric.name === config.name);
  const savedOption = $('rubric-preset').querySelector('[value="saved"]');
  if (savedOption) savedOption.remove();
  if (!match) $('rubric-preset').add(new Option('Saved custom rubric', 'saved'));
  $('rubric-preset').value = match?.id || 'saved';
  $('rubric-description').textContent = description;
  $('eval-threshold').value = currentRubric.threshold; $('eval-strict').checked = currentRubric.strict_mode;
  $('evaluation-fields').innerHTML = ['input','actual_output','context','expected_output','retrieval_context'].map(field =>
    `<label><input type="checkbox" value="${field}" ${currentRubric.evaluation_params.includes(field) ? 'checked' : ''} ${['input','actual_output'].includes(field) ? 'disabled' : ''}>${field}</label>`).join('');
  renderQuestions(); strictHelp();
  updateDraftStatus();
}
function renderQuestions() {
  $('question-editor').innerHTML = currentRubric.questions.map((q, i) => `<div class="question-edit" data-question-index="${i}">
    <div class="question-top"><details><summary><span class="type-tag ${q.type}">${{noul:'Noul',score:'Score',choice:'Choice'}[q.type]}</span>${esc(q.label)} <span aria-hidden="true">⌄</span></summary>
      <div class="question-content"><label for="question-${i}">Question Jev will answer</label><textarea id="question-${i}" data-property="question" rows="4" maxlength="2000">${esc(q.question)}</textarea>
      ${q.type === 'score' ? `<label for="levels-${i}">Ordinal levels · worst to best · one per line</label><textarea id="levels-${i}" data-property="levels" rows="4">${esc(q.levels.join('\n'))}</textarea>` : ''}
      ${q.type === 'choice' ? `<label for="options-${i}">Options & credits · JSON · null means not applicable</label><textarea id="options-${i}" data-property="options" rows="6">${json(q.options)}</textarea><p class="help">Jev receives the option names. DeepEval applies these credits locally.</p>` : ''}
      <button type="button" class="text-button remove-question" data-remove="${i}" ${currentRubric.questions.length === 1 ? 'disabled' : ''}>Remove criterion</button></div></details>
    <label>Weight <input aria-label="${esc(q.label)} weight" data-property="weight" type="number" min=".1" max="10" step=".1" value="${q.weight}"></label></div></div>`).join('');
  $('add-question').disabled = currentRubric.questions.length >= 6;
}
function readRubric() {
  const config = clone(currentRubric);
  document.querySelectorAll('.question-edit').forEach(row => {
    const q = config.questions[Number(row.dataset.questionIndex)];
    q.question = row.querySelector('[data-property="question"]').value.trim();
    q.weight = Number(row.querySelector('[data-property="weight"]').value);
    if (q.type === 'score') q.levels = row.querySelector('[data-property="levels"]').value.split('\n').map(s => s.trim()).filter(Boolean);
    if (q.type === 'choice') {
      try { q.options = JSON.parse(row.querySelector('[data-property="options"]').value); } catch { throw new Error(`Check the JSON options for ${q.label}. Use numbers for credits and null for not applicable.`); }
    }
  });
  config.evaluation_params = [...document.querySelectorAll('#evaluation-fields input:checked')].map(el => el.value);
  config.threshold = Number($('eval-threshold').value); config.strict_mode = $('eval-strict').checked;
  return config;
}
function strictHelp() {
  $('eval-threshold').disabled = $('eval-strict').checked;
  $('strict-help').textContent = $('eval-strict').checked ? 'Native strict mode uses the most likely outcomes. Every applicable question must pass; the score and threshold become 0/1 and 1.' : 'A pass means the weighted score meets the threshold.';
}
async function startCase(action) {
  if (!isLive()) throw new Error('Server offline. Wait for a fresh connection before starting a call.');
  const body = {action, test_case:readCase(), rubric:readRubric(), scenario:$('generation-scenario').value, ticket_id:$('ticket-select').value, source_record_id:originId};
  if (action === 'evaluate' && !body.test_case.actual_output) throw new Error('Generate an answer or enter actual_output before evaluating.');
  requestPending = true; updateControls(); notice(action === 'generate' ? 'Requesting a fresh Model answer…' : 'Running native JevEval against this test case…');
  try { const job = await api('/api/lab/case', body); watchedJob = job.id; await refreshState(); }
  finally { requestPending = false; updateControls(); }
}
function updateControls() {
  const busy = requestPending || !!state?.active || !!state?.monitor_active;
  for (const id of ['generate-case','evaluate-case','run-dataset']) $(id).disabled = !isLive() || busy || !state?.connection.jev_configured;
  $('stop-lab').disabled = !isLive() || !state?.active || state.active.status === 'stopping';
}
function updateConnection() {
  const live = isLive();
  $('live-lab').textContent = live ? 'Connected · updates as each provider call completes' : 'Event stream disconnected · reconnecting';
  $('live-lab').className = live ? 'connected' : 'muted';
  $('lab-offline').hidden = live;
  $('lab-offline').textContent = state ? 'Server offline — showing saved results. Fresh calls are disabled until a new server update arrives.' : 'Connecting to the local server. Fresh calls will be enabled after the first update.';
  document.body.classList.toggle('server-offline', !live);
  const c = state?.connection;
  if (c) $('lab-connection').innerHTML = `<strong>${esc(c.generator_model)}</strong> ${c.generator_verified ? live ? 'last call verified' : 'saved verification' : 'awaiting fresh call'}<br>DeepEval → <strong>${esc(c.jev_model)}</strong> ${c.jev_verified ? live ? 'last call verified' : 'saved verification' : c.jev_configured ? 'key connected' : 'key required'}`;
  updateControls();
}
function renderState(next) {
  state = next; heartbeat = Date.now(); updateConnection();
  const c = state.connection;
  $('lab-counts').textContent = `Lab: ${state.counts.generator} Model responses · ${state.counts.jev} Jev results · ${state.counts.failed} call errors`;
  const evaluated = state.records.filter(r => r.status === 'completed' && Number.isFinite(r.quality)), applicable = evaluated.filter(r => r.applicable_count > 0), failed = applicable.filter(r => !r.passed).length;
  $('lab-answer-counts').innerHTML = `Recent saved evaluations: ${applicable.length-failed} pass · <strong class="${failed?'negative':'muted'}">${failed} fail</strong> · ${evaluated.length-applicable.length} with no applicable criteria. Each result uses its saved rubric and threshold.`;
  $('lab-versions').textContent = `DeepEval ${state.versions.deepeval} · TypeSafe SDK ${state.versions['typesafe-sdk']} · Jev ${c.jev_model}`;
  $('lab-progress').hidden = !state.active;
  if (state.active) {
    $('lab-phase').textContent = state.active.status === 'stopping' ? 'Stopping after the current call' : state.active.phase;
    $('lab-progress-copy').textContent = `${state.active.completed} / ${state.active.planned} ${state.active.kind === 'generate' ? 'answers generated' : 'evaluations complete'} · ${state.active.id}`;
    $('lab-progress-bar').max = state.active.planned; $('lab-progress-bar').value = state.active.completed;
  }
  renderHistory();
  const experiments = state.jobs.filter(j => j.kind === 'dataset').reverse();
  const previous = $('dataset-history').value;
  $('dataset-history').innerHTML = experiments.length ? experiments.map(j => `<option value="${esc(j.id)}">${esc(j.id)} · ${esc(j.status)}</option>`).join('') : '<option value="">No experiments yet</option>';
  if (experiments.some(j => j.id === (selectedDataset || previous))) $('dataset-history').value = selectedDataset || previous;
  if (!selectedDataset && experiments.length) selectedDataset = experiments[0].id;
  const experiment = experiments.find(j => j.id === selectedDataset);
  const revision = experiment ? `${experiment.id}:${experiment.status}:${experiment.completed}:${experiment.record_ids?.length}` : '';
  if (revision && revision !== datasetRevision) { datasetRevision = revision; safely(() => loadDataset(experiment.id))(); }
  if (watchedJob) {
    const job = state.jobs.find(j => j.id === watchedJob);
    if (job && !['running','stopping'].includes(job.status)) {
      watchedJob = null;
      if (job.kind !== 'dataset' && job.record_ids.length) safely(async () => {
        await loadRecord(job.record_ids.at(-1), true);
        notice(job.status === 'completed' ? job.kind === 'generate' ? 'Fresh Model answer received. Evaluate it with Jev next.' : 'Fresh Jev evaluation complete. Inspect the probabilities and exact request below.' : job.error || 'The operation did not complete; its evidence is retained.', job.status !== 'completed');
      })();
      else notice(job.status === 'completed' ? 'Dataset comparison complete. The native results and release gate are ready.' : job.error || 'Experiment stopped. Partial evidence is retained.', job.status === 'failed');
    }
  }
}
async function refreshState() { renderState(await api('/api/lab/state')); }
function renderHistory() {
  const rows = state.records.filter(r => r.kind !== 'dataset').reverse();
  $('case-history').innerHTML = rows.length ? rows.slice(0, 30).map(r => {
    const outcome = r.status === 'completed' ? r.applicable_count > 0 ? verdict(r.passed, `${r.passed?'Pass':'Fail'} · ${pct(r.quality)}`) : verdict(null, 'No applicable criteria') : r.status === 'generated' ? verdict(null, 'Generated · unscored') : verdict(r.status === 'failed' ? false : null, r.status === 'failed' ? 'Call error' : r.status);
    return `<button type="button" class="history-item" data-load-record="${esc(r.id)}"><span>${esc(r.input)}<small>${esc(r.kind)} · ${esc(scenario(r.scenario))} · ${esc(r.id)}</small></span><span class="history-outcome">${outcome}</span></button>`;
  }).join('') : 'No lab calls yet.';
}

function resultMarkup(row, prefix = '') {
  if (row.status !== 'completed' || !row.breakdown) return `<p class="result-meta">${esc(row.source)}</p><div class="dialog-answer">${esc(row.test_case?.actual_output || row.answer || 'No answer returned.')}</div>${row.error ? `<p class="audit-failed">${esc(row.error)}</p>` : '<p class="help">Model generation is separate from evaluation. Use Evaluate with Jev to score this answer.</p>'}${row.generator_response ? fold('Exact Model request', row.generator_request) + fold('Exact Model response', row.generator_response) : ''}`;
  const noApplicable = row.applicable_count === 0;
  const criteria = row.breakdown.map((b, i) => {
    const q = row.rubric.questions[i];
    let math;
    if (q.type === 'noul') math = `value = P(true) = ${num(b.value)}`;
    else if (q.type === 'score') math = `value = returned expected level / ${q.levels.length - 1} = ${num(b.value)}. The displayed probability table may be rounded.`;
    else {
      const na = Object.entries(q.options).filter(([, credit]) => credit === null).reduce((s, [k]) => s + (b.probabilities[k] || 0), 0);
      math = b.applicable ? `N/A mass = ${num(na)}. value = Σ P(option) × credit / applicable mass = ${num(b.value)}` : `N/A mass = ${num(na)} ≥ 0.5. Excluded from the weighted mean.`;
    }
    return `<div class="criterion-result"><div class="criterion-heading"><span class="type-tag ${q.type}">${{noul:'Noul',score:'Score',choice:'Choice'}[q.type]}</span><h3>${esc(q.label)}</h3><strong>${b.applicable ? pct(b.value) : 'N/A'}</strong></div>
      <p class="result-meta">Weight ${q.weight} · confidence ${pct(b.confidence)}${b.passed !== null && b.passed !== undefined ? ` · strict ${b.passed ? 'pass' : 'fail'}` : ''}</p>
      <div class="probability-list">${Object.entries(b.probabilities).map(([label,p]) => `<div class="probability"><span class="prob-label">${esc(label)}${q.type === 'choice' ? ` <small>· ${q.options[label] === null ? 'N/A' : `credit ${q.options[label]}`}</small>` : ''}</span><svg viewBox="0 0 100 7" preserveAspectRatio="none" aria-hidden="true"><rect class="track" width="100" height="7" rx="3"/><rect class="fill" width="${p * 100}" height="7" rx="3"/></svg><span class="prob-value">${pct(p,0)}</span></div>`).join('')}</div><p class="criterion-math">${esc(math)}</p></div>`;
  }).join('');
  const applicable = row.breakdown.filter(b => b.applicable);
  const equation = applicable.length ? `(${applicable.map(b => `${b.weight} × ${num(b.value)}`).join(' + ')}) / ${applicable.reduce((s,b) => s+b.weight, 0)} = ${num(row.weighted_score)}` : 'No applicable criteria → native empty score = 1.0';
  return `<p class="result-meta">${esc(row.source)}<br>${esc(row.id)} · ${esc(row.rubric.name)} · ${esc(row.finished_at)}</p>
    <div class="score-overview"><div><small>Native DeepEval score</small><strong>${pct(row.quality)}</strong></div><div><small>${noApplicable ? 'Applicability' : `Threshold ${pct(row.effective_threshold,0)}`}</small>${verdict(noApplicable ? null : row.passed, noApplicable ? 'No applicable criteria' : undefined)}</div><div><small>Jev evaluation</small><strong class="score-secondary">${ms(row.eval_ms)}</strong></div></div>
    ${noApplicable ? '<p class="help">DeepEval returns 1.0 when nothing applies. This is not evidence of answer quality. The dataset release gate treats this as incomplete.</p>' : ''}
    ${criteria}<div class="equation">${esc(equation)}${row.rubric.strict_mode ? `<br>Native strict score = ${row.strict_score}; every applicable question must pass.` : ''}</div>
    <p class="help">Metric confidence ${pct(row.confidence)} is the lowest question confidence. It is not a measured probability that the answer is correct.</p>
    ${fold('Fields actually sent to Jev', row.jev_request.state, prefix + 'sent-fields')}
    ${fold('Exact Jev request · typed questions', row.jev_request, prefix + 'exact-jev-request')}
    ${fold('Exact Jev response · provider probabilities', row.jev_response, prefix + 'exact-jev-response')}
    <details class="evidence-fold"><summary>DeepEval reason · formatted score summary</summary><p>${esc(row.reason)}</p><p>This is a deterministic summary of the typed outcomes, not a separately generated explanation.</p></details>
    ${fold('Integrity hashes & generation link', {config_sha256:row.config_sha256, jev_request_sha256:row.jev_request_sha256, jev_response_sha256:row.jev_response_sha256, generation_record_id:row.generation_record_id || (row.generator_response ? row.id : null), generator_response_sha256:row.generation_response_sha256 || row.generator_response_sha256 || null})}
    ${row.generator_response ? fold('Exact Model request', row.generator_request) + fold('Exact Model response', row.generator_response) : row.generation_record_id ? `<p class="help"><a href="${row.generation_record_id.startsWith('labreq-') ? '/api/lab/records/' : '/api/requests/'}${encodeURIComponent(row.generation_record_id)}" target="_blank" rel="noopener">Inspect the saved Model generation and raw response</a></p>` : ''}
    <details class="evidence-fold"><summary>Reproduce this metric in Python</summary><pre>${esc(row.python || '')}</pre><a href="/api/lab/code/${encodeURIComponent(row.id)}" download>Download runnable Python</a></details>
    <div class="evidence-links"><button type="button" class="text-button" data-inspect="${esc(row.id)}">Open full test case</button><a href="/api/lab/records/${encodeURIComponent(row.id)}" target="_blank" rel="noopener">Open saved JSON</a></div>`;
}
async function loadRecord(id, fillForm = false) {
  const revision = ++recordRevision;
  const row = await api(`/api/lab/records/${encodeURIComponent(id)}`);
  if (revision !== recordRevision) return;
  currentRecord = row;
  if (fillForm) {
    setCase(row.test_case, row.generator_response ? row.id : row.generation_record_id || null, row.source);
    setRubric(row.rubric);
    if (row.ticket_id && catalog.tickets.some(t => t.id === row.ticket_id)) $('ticket-select').value = row.ticket_id;
    $('generation-scenario').value = row.scenario || 'healthy';
  }
  $('result-source').textContent = row.status === 'completed' ? 'Saved native JevEval result' : row.status === 'generated' ? 'Fresh Model answer saved' : row.status;
  $('result-caption').textContent = row.status === 'completed' ? 'Saved probabilities and the native calculation for this test case.' : 'Generation and evaluation are separate steps.';
  $('case-result').innerHTML = resultMarkup(row);
  savedDraft = fillForm ? draftKey() : null;
  updateDraftStatus();
  $('reweight-panel').hidden = row.status !== 'completed';
  if (row.status === 'completed') {
    $('replay-weights').innerHTML = row.rubric.questions.map((q, i) => `<div class="replay-weight"><label for="weight-${i}">${esc(q.label)}</label><input id="weight-${i}" type="range" min=".1" max="10" step=".1" value="${q.weight}" data-replay-weight="${i}"><output for="weight-${i}">${q.weight.toFixed(1)}</output></div>`).join('');
    $('replay-threshold').value = row.rubric.threshold; $('replay-strict').checked = row.rubric.strict_mode;
    await recalculate();
  }
}
async function recalculate() {
  if (!currentRecord || currentRecord.status !== 'completed') return;
  const seq = ++replaySequence;
  const weights = [...document.querySelectorAll('[data-replay-weight]')].map(input => { input.nextElementSibling.value = Number(input.value).toFixed(1); return Number(input.value); });
  const strict = $('replay-strict').checked;
  $('replay-threshold').disabled = strict;
  $('replay-threshold-value').value = pct(strict ? 1 : Number($('replay-threshold').value), 0);
  const result = await api('/api/lab/replay', {record_id:currentRecord.id, weights, threshold:Number($('replay-threshold').value), strict_mode:strict});
  if (seq !== replaySequence) return;
  $('replay-result').innerHTML = `<div class="preview-score"><strong>${pct(result.quality)}</strong>${verdict(result.applicable_count ? result.passed : null, result.applicable_count ? undefined : 'No applicable criteria')}<span>${strict ? 'Native strict rule' : 'Weighted rule'} · no API call</span></div>${strict ? `<p class="help">${result.breakdown.map((b,i) => `${esc(currentRecord.rubric.questions[i].label)}: ${b.applicable ? b.passed ? 'pass' : 'fail' : 'not applicable'}`).join(' · ')}</p>` : ''}`;
}
async function inspectRecord(id) {
  const row = await api(`/api/lab/records/${encodeURIComponent(id)}`);
  openDialog(row.rubric?.name || 'Saved record', row.id,
    `<button type="button" class="quiet compact" data-load-case="${esc(row.id)}">Use this test case in the workbench</button><h3>Customer question</h3><p>${esc(row.test_case.input)}</p><h3>Actual answer</h3><div class="dialog-answer">${esc(row.test_case.actual_output || 'No answer yet')}</div>${fold('Complete LLMTestCase', row.test_case)}${resultMarkup(row, 'dialog-')}`);
}
function openDialog(title, id, html) {
  $('dialog-title').textContent = title; $('dialog-id').textContent = id; $('dialog-content').innerHTML = html;
  if (!$('lab-inspector').open) $('lab-inspector').showModal();
  $('lab-inspector').scrollTop = 0;
}

function datasetSetup() {
  const count = Number($('dataset-count').value);
  $('dataset-cost').textContent = `${count * 2} fresh Model calls + ${count * 2} fresh Jev evaluations. No cache.`;
  $('goldens-list').innerHTML = catalog.tickets.slice(0, count).map(t => `<div class="golden"><small>${esc(t.id)} · ${esc(t.category)}</small><strong>${esc(t.input)}</strong><p>Expected behavior: ${esc(t.expected_output)}</p></div>`).join('');
}
async function startDataset() {
  if (!isLive()) throw new Error('Server offline. Wait for a fresh connection before starting a call.');
  const rubric = clone(preset($('dataset-rubric').value).rubric); rubric.threshold = Number($('dataset-threshold').value);
  requestPending = true; updateControls(); notice('Starting a fresh paired dataset experiment. Both variants use the same goldens and metric.');
  try {
    const job = await api('/api/lab/dataset', {count:Number($('dataset-count').value), candidate:$('candidate-scenario').value, rubric,
      minimum_pass_rate:Number($('gate-pass').value), max_mean_drop:Number($('gate-drop').value)});
    selectedDataset = job.id; watchedJob = job.id; await refreshState();
  } finally { requestPending = false; updateControls(); }
}
async function loadDataset(id) {
  const job = await api(`/api/lab/jobs/${encodeURIComponent(id)}`);
  if (selectedDataset !== id) return;
  $('dataset-history').value = id;
  const rows = state.records.filter(r => r.job_id === id);
  const summary = job.summary;
  const pairs = summary?.pairs || (job.tickets || catalog.tickets.slice(0, job.options.count)).map(t => {
    const a = rows.find(r => r.ticket_id === t.id && r.variant === 'A'), b = rows.find(r => r.ticket_id === t.id && r.variant === 'B');
    return {ticket_id:t.id, input:t.input, category:t.category, a_id:a?.id, b_id:b?.id, a:a?.quality, b:b?.quality, delta:Number.isFinite(a?.quality) && Number.isFinite(b?.quality) ? b.quality-a.quality : null};
  });
  $('dataset-result-caption').textContent = `${job.id} · ${job.status} · ${job.completed}/${job.planned} evaluations complete`;
  const dotplot = p => Number.isFinite(p.a) && Number.isFinite(p.b) ? `<svg class="paired-chart" viewBox="0 0 100 24" role="img" aria-label="A ${pct(p.a)}; B ${pct(p.b)}"><line x1="${5+p.a*90}" x2="${5+p.b*90}" y1="12" y2="12"/><circle class="a" cx="${5+p.a*90}" cy="12" r="3.8"/><circle class="b" cx="${5+p.b*90}" cy="12" r="3.8"/></svg>` : '—';
  $('dataset-results').innerHTML = `${summary ? `<div class="experiment-summary"><div><small>A · reference mean</small><strong class="reference">${pct(summary.a_mean)}</strong></div><div><small>B · candidate mean</small><strong class="current">${pct(summary.b_mean)}</strong></div><div><small>Candidate pass rate</small><strong>${pct(summary.candidate_pass_rate,0)}</strong></div></div>
    <div class="gate-result">${verdict(summary.release_gate === 'incomplete' ? null : summary.release_gate === 'pass', summary.release_gate === 'pass' ? 'Gate passes' : summary.release_gate === 'block' ? 'Release blocked' : 'Incomplete')}<strong>${summary.complete_pairs}/${pairs.length} usable pairs</strong><p>App-defined release rule</p></div>
    <table class="gate-table"><caption class="sr-only">Release conditions</caption><tbody>${summary.gates.map((g,i) => `<tr><td>${esc(g.name)}</td><td>${i === 0 ? `${g.value}/${g.limit}` : i === 1 ? `${pct(g.value,0)} ≥ ${pct(g.limit,0)}` : `${pp(g.value)} ≤ ${pp(g.limit)}`} · ${g.passed === null ? 'pending' : g.passed ? 'pass' : 'fail'}</td></tr>`).join('')}</tbody></table>` : `<p class="help">${esc(job.phase)}. Generation is followed by one native evaluate() run for each variant.</p>`}
    ${job.error ? `<p class="audit-failed">${esc(job.error)}</p>` : ''}
    <div class="paired-table-wrap"><table class="paired-table"><caption class="sr-only">Each golden and its paired Model answers scored by Jev</caption><thead><tr><th>Same input · same reference</th><th>A</th><th>B</th><th>Shift</th><th>Δ B−A</th></tr></thead><tbody>${pairs.map(p => `<tr><td><p>${esc(p.input)}</p><small>${esc(p.ticket_id)} · ${esc(p.category)}</small></td><td>${p.a_id ? `<button type="button" data-inspect="${esc(p.a_id)}" aria-label="Inspect reference ${esc(p.ticket_id)}">${pct(p.a,0)}</button>` : '—'}</td><td>${p.b_id ? `<button type="button" data-inspect="${esc(p.b_id)}" aria-label="Inspect candidate ${esc(p.ticket_id)}">${pct(p.b,0)}</button>` : '—'}</td><td>${dotplot(p)}</td><td class="paired-delta ${p.delta < 0 ? 'negative' : 'positive'}">${pp(p.delta)}</td></tr>`).join('')}</tbody></table></div>
    <p class="help">${job.options.count} explicitly synthetic tickets. Small suites expose these cases; they do not estimate production accuracy. Inspect a score to see both the answer and judge evidence.</p>
    ${fold('Shared metric & experiment settings', job.options)}${fold('Golden dataset & version hashes', {dataset_version:job.dataset_version, dataset_sha256:job.dataset_sha256, policy_version:job.policy_version, goldens:job.goldens})}
    ${fold('Native DeepEval evaluate() return objects', job.native_results || {}, 'native-dataset-results')}
    <details class="evidence-fold"><summary>How this dataset executes</summary><pre>goldens = [Golden(input=..., expected_output=..., context=[policy]), ...]
cases = [LLMTestCase(input=g.input, actual_output=call_generator(g.input),
                     expected_output=g.expected_output, context=g.context), ...]
dataset = EvaluationDataset(goldens=goldens)
dataset.test_cases = cases
result = evaluate(test_cases=dataset.test_cases, metrics=[jev_metric],
                  async_config=AsyncConfig(run_async=False),
                  cache_config=CacheConfig(use_cache=False, write_cache=False))</pre><p class="help">Illustrative API structure; the exact executed metric is available in each result’s Python download. The app captures each provider response and reconciles it with the native EvaluationResult.</p></details>`;
}

async function loadDrift(reset = false) {
  const sequence = ++driftSequence;
  const body = {source:$('drift-evidence').value, window:Number($('drift-window').value), threshold:Number($('drift-pass').value), watch_drop:Number($('drift-watch').value), alert_drop:Number($('drift-alert').value)};
  if (driftResult && !reset) body.cutoff = Number($('drift-cutoff').value);
  $('watch-value').value = `${Math.round(body.watch_drop*100)} pp`; $('alert-value').value = `${Math.round(body.alert_drop*100)} pp`; $('drift-pass-value').value = pct(body.threshold,0);
  const value = await api('/api/lab/drift', body);
  if (sequence !== driftSequence) return;
  driftResult = value; renderDrift();
}
function renderDrift() {
  const d = driftResult;
  $('drift-source').textContent = `${d.source_id || 'No saved reference'} · ${d.reference_count} reference + ${d.total} subsequent evaluations`;
  $('drift-cutoff').max = d.total; $('drift-cutoff').value = d.cutoff; $('drift-cutoff-label').value = `${d.cutoff} / ${d.total}`;
  $('play-drift').disabled = !d.total;
  const w=920, h=300, p={l:50,r:24,t:22,b:38}, x=n=>p.l+(n/Math.max(1,d.total))*(w-p.l-p.r), y=v=>p.t+(1-v)*(h-p.t-p.b);
  let chart = '';
  for (const tick of [0,.25,.5,.75,1]) chart += `<line class="grid" x1="${p.l}" x2="${w-p.r}" y1="${y(tick)}" y2="${y(tick)}"/><text x="${p.l-10}" y="${y(tick)+4}" text-anchor="end">${pct(tick,0)}</text>`;
  const ticks = [...new Set([0, Math.round(d.total*.25), Math.round(d.total*.5), Math.round(d.total*.75), d.total])];
  for (const n of ticks) chart += `<text x="${x(n)}" y="${h-12}" text-anchor="middle">${n}</text>`;
  chart += `<rect class="window" x="${x(Math.max(0,d.cutoff-d.options.window))}" y="${p.t}" width="${x(d.cutoff)-x(Math.max(0,d.cutoff-d.options.window))}" height="${h-p.t-p.b}"/>`;
  if (Number.isFinite(d.reference_mean)) chart += `<line class="baseline" x1="${p.l}" x2="${w-p.r}" y1="${y(d.reference_mean)}" y2="${y(d.reference_mean)}"/><line class="boundary" x1="${p.l}" x2="${w-p.r}" y1="${y(Math.max(0,d.reference_mean-d.options.alert_drop))}" y2="${y(Math.max(0,d.reference_mean-d.options.alert_drop))}"/>`;
  chart += `<polyline class="rolling" points="${d.rolling.filter(r=>r.index<=d.cutoff).map(r=>`${x(r.index)},${y(r.quality)}`).join(' ')}"/>`;
  chart += d.timeline.map((r,i) => `<circle class="${r.scenario === 'stale_policy' ? 'fault-point' : ''}" cx="${x(i+1)}" cy="${y(r.quality)}" r="3.6" opacity="${i<d.cutoff?1:.22}" role="button" tabindex="0" data-drift-index="${i+1}" aria-label="Request ${i+1}, ${esc(scenario(r.scenario))}, score ${pct(r.quality)}"><title>${esc(r.id)} · ${pct(r.quality)} · ${esc(r.category)}</title></circle>`).join('');
  chart += `<line class="cursor" x1="${x(d.cutoff)}" x2="${x(d.cutoff)}" y1="${p.t}" y2="${h-p.b}"/>`;
  $('replay-chart').innerHTML = d.total ? `<svg viewBox="0 0 ${w} ${h}" role="group" aria-label="Saved quality scores, rolling mean, and alert boundary. Select a point to move the replay.">${chart}</svg>` : '<div class="empty-state"><h3>No saved cohort yet.</h3><p>Run a proof in the live monitor to create genuine evidence for this replay.</p></div>';
  const signal = (key) => verdict(d[key] === 'insufficient' ? null : d[key] === 'stable', d[key] === 'insufficient' ? 'Need more samples' : d[key][0].toUpperCase()+d[key].slice(1));
  $('drift-results').innerHTML = `<div><h3>Current quality</h3><strong>${pct(d.current_mean)}</strong>${signal('quality_status')}<p>${d.current_count}/${d.options.window} current samples · reference ${pct(d.reference_mean)}<br>Mean drop ${pp(d.quality_drop)}</p></div><div><h3>Ticket mix · JS divergence</h3><strong>${num(d.mix_js)}</strong>${signal('mix_status')}<p>Watch ≥ 0.10 · Alert ≥ 0.20<br>Separate from answer quality</p></div><div><h3>Current pass rate</h3><strong>${pct(d.current_pass_rate,0)}</strong><p>Score ≥ ${pct(d.options.threshold,0)}<br>${d.excluded_errors} incomplete requests excluded</p></div><div><h3>First quality alert so far</h3><strong>${d.first_alert ? `#${d.first_alert.index}` : '—'}</strong><p>${d.first_alert ? esc(d.first_alert.finished_at) : d.quality_status === 'insufficient' ? `Requires ${d.options.window} current samples` : 'No alert before this replay position'}<br>KS distance ${num(d.ks_distance)}</p></div>`;
  $('drift-membership').textContent = JSON.stringify({source:d.source, source_id:d.source_id, cutoff:d.cutoff, options:d.options, baseline_ids:d.baseline_ids, current_ids:d.current_ids, current_requests:d.timeline.slice(Math.max(0,d.cutoff-d.options.window), d.cutoff), method:d.method}, null, 2);
}
function stopPlayback() { clearTimeout(playTimer); playTimer = null; $('play-drift').textContent = 'Play replay'; }
async function advanceReplay() {
  if (Number($('drift-cutoff').value) >= Number($('drift-cutoff').max)) { stopPlayback(); return; }
  $('drift-cutoff').value = Number($('drift-cutoff').value)+1;
  try { await loadDrift(); if ($('play-drift').textContent === 'Pause replay') playTimer = setTimeout(advanceReplay, 650); }
  catch(e) { stopPlayback(); notice(e.message, true); }
}

const guide = {
  architecture: {title:'Three layers, three responsibilities.', content:`<p>The application answers fictional store-support questions: returns, shipping, billing, account access, and warranty. You can change the knowledge-base policy to create a controlled failure.</p><div class="concept-path"><div><strong>1 · Model answers</strong><span>A real request through the configured generator connection supplies actual_output.</span></div><div><strong>2 · Jev judges</strong><span>Typed Noul, Score, and Choice questions produce probabilities through TypeSafe.</span></div><div><strong>3 · DeepEval scores</strong><span>JevEval normalizes and combines the outcomes, then applies its threshold.</span></div></div><p>The app records this evidence, streams completed results, compares datasets, and calculates drift signals. <strong>The release gate and drift detector are application logic.</strong> DeepEval supplies the metric and dataset execution; it does not infer a production incident for us.</p><h3>Try the whole path</h3><ol><li>Choose a synthetic ticket and Current policy.</li><li>Generate answer, then Evaluate with Jev.</li><li>Open the exact request and response. Compare the question probabilities with the weighted score.</li><li>Change the policy, repeat the calls, and inspect which criterion changed.</li></ol><button class="button quiet" data-jump="workbench">Open the workbench</button>`},
  testcase: {title:'A metric can only see what you send.', content:`<p><code>LLMTestCase</code> holds the application input, its real output, and optional reference material. <code>evaluation_params</code> determines which of those fields Jev receives for this metric.</p><table class="concept-table"><thead><tr><th>Field</th><th>Meaning in this lab</th></tr></thead><tbody><tr><td>input</td><td>The customer’s question.</td></tr><tr><td>actual_output</td><td>Model’s generated answer, or explicitly labeled user-supplied text.</td></tr><tr><td>expected_output</td><td>The desired behavior for this fixture. It is a reference, not a model prediction.</td></tr><tr><td>context</td><td>The authoritative current support policy.</td></tr><tr><td>retrieval_context</td><td>The document actually supplied to the model. It may be outdated or missing.</td></tr></tbody></table><p>A populated field can still be absent from the evaluator request. Expand <strong>Fields actually sent to Jev</strong> to verify what crossed that boundary. The lab requires input and actual_output, but lets you include or exclude the other fields.</p><h3>An evidence-ablation experiment</h3><p>Evaluate an answer with context selected. Then deselect context and make a fresh Jev call. Keep the answer fixed and inspect the new request. A rubric that asks about a policy without receiving that policy is underspecified; a confident outcome would not repair that missing evidence.</p><p>Saved answers can be re-evaluated with a new rubric. Their generation provenance is linked only if input and answer still match the saved Model record.</p>`},
  primitives: {title:'Typed questions make the rubric inspectable.', content:`<table class="concept-table"><thead><tr><th>Primitive</th><th>Jev returns</th><th>DeepEval uses</th></tr></thead><tbody><tr><td>Noul</td><td>Probability of a statement being true.</td><td><code>value = P(true)</code>. Useful for one clearly stated criterion.</td></tr><tr><td>Score</td><td>Probabilities over ordered levels and an expected level.</td><td><code>value = expected level / (levels − 1)</code>. Levels must run from worst to best.</td></tr><tr><td>Choice</td><td>Probabilities over named, unordered options.</td><td>Expected option credit, renormalized over applicable options. Credits are configured locally.</td></tr></tbody></table><h3>Not applicable is a first-class outcome</h3><p>Give a Choice option a <code>null</code> credit. If the combined probability of such options reaches 0.5, DeepEval excludes that question from the mean. Otherwise it renormalizes over the remaining probability mass before applying credits.</p><p><strong>If every question is inapplicable, native JevEval returns 1.0.</strong> The lab labels this “No applicable criteria,” and the dataset gate will not use it as proof of quality.</p><h3>Try all three</h3><p>Select “Three Jev primitives” for a return request. Inspect the binary distribution, ordered relevance levels, and escalation options. Next use “Escalation only” on a question asking how long standard shipping takes; the information-only option may be inapplicable.</p><p>The Score API may round its expected value and probability table independently. The lab preserves both; it does not silently replace the native expected level with arithmetic on rounded display values.</p>`},
  strict: {title:'A score and a decision are different things.', content:`<p>The normal score is <code>Σ(weight × value) / Σ(weight)</code> over applicable questions. A weight controls how much a criterion affects the mean. A threshold controls the pass/fail decision; it does not alter the probability estimates.</p><h3>Native strict mode in this pinned version</h3><table class="concept-table"><thead><tr><th>Question</th><th>Strict passing rule</th></tr></thead><tbody><tr><td>Noul</td><td>P(true) is at least 0.5.</td></tr><tr><td>Score</td><td>The most likely ordinal level is the highest level.</td></tr><tr><td>Choice</td><td>The most likely applicable option has full credit, 1.0.</td></tr></tbody></table><p>Every applicable criterion must satisfy its rule. The metric score is then 1 or 0, and the effective pass threshold becomes 1. Strict mode uses these discrete decisions; it does not require all returned probabilities to be 1.</p><h3>Explore without a provider call</h3><p>After a fresh evaluation, adjust the saved-result sliders. Watch a threshold flip the verdict while the score stays fixed. Change a weight to move the weighted score. Toggle strict mode to expose each per-question rule.</p><p>Changing question text, options, or evidence requires a <strong>fresh Jev evaluation</strong>. Reweighting existing outcomes is only arithmetic and is explicitly labeled as such.</p>`},
  rag: {title:'Faithful to a bad document is still wrong.', content:`<p>This lab models retrieval by explicitly supplying a current, outdated, or missing document. It does not run a vector database. That controlled setup lets you separate the generation and evaluation references.</p><table class="concept-table"><thead><tr><th>Custom Jev criterion</th><th>Comparison</th><th>What it reveals</th></tr></thead><tbody><tr><td>Faithfulness</td><td>actual_output vs. retrieval_context</td><td>Did the answer stay grounded in the document it received?</td></tr><tr><td>Correctness</td><td>actual_output vs. context and expected_output</td><td>Does the answer agree with the current policy?</td></tr><tr><td>Retrieval adequacy</td><td>retrieval_context vs. context and expected_output</td><td>Did the supplied document contain the needed correct rules?</td></tr></tbody></table><h3>Diagnose an outdated policy</h3><ol><li>Choose the kettle-return ticket and Outdated policy.</li><li>Generate answer. The answer may faithfully follow the obsolete 90-day policy.</li><li>Select RAG diagnosis and evaluate with Jev.</li><li>Inspect all three outcomes. Strong faithfulness together with weak correctness and retrieval adequacy suggests the supplied document is the problem.</li></ol><p>These are <strong>custom JevEval rubric questions</strong>, not executions of DeepEval’s separate FaithfulnessMetric or ContextualRelevancyMetric. Their values are model judgments and can be wrong. The visible answer and source documents remain the evidence for human review.</p>`},
  dataset: {title:'From one test case to a regression suite.', content:`<p>A <code>Golden</code> describes an input, expected behavior, and reference context. The app executes Model to obtain actual_output, builds <code>LLMTestCase</code> objects, and runs them through <code>EvaluationDataset</code> and native <code>deepeval.evaluate()</code>.</p><div class="concept-path"><div><strong>Shared test inputs</strong><span>Identical ticket IDs, current reference policy, rubric, and threshold for A and B.</span></div><div><strong>Controlled candidate</strong><span>Only the document supplied to variant B changes. Both variants make fresh Model calls.</span></div><div><strong>Paired evidence</strong><span>Every score links to its answer, typed judge response, and native test result.</span></div></div><p>This implementation evaluates serially with caching disabled, so each completed case has newly captured Jev evidence. The native result objects are retained and reconciled against the capture.</p><h3>Native success vs. the release gate</h3><p>DeepEval decides whether each case reaches the metric threshold. The app additionally requires all pairs to be complete and applicable, candidate pass rate above the chosen minimum, and average score drop below the chosen maximum. Failed or stopped runs produce an incomplete gate.</p><p>Inspect paired deltas before trusting the average. An improvement in one category can conceal a regression in another. A five-case teaching suite is useful for controlled failures, but is too small to establish production performance.</p><button class="button quiet" data-jump="experiments">Run a paired experiment</button>`},
  confidence: {title:'Confidence is not a calibration certificate.', content:`<p>Jev returns confidence information with its typed outcomes. DeepEval exposes those values and reports the <strong>lowest question confidence</strong> as metric confidence. For Noul, confidence is <code>|2P(true) − 1|</code>.</p><p>A statement with a very low probability of being true can have high confidence. Confidence describes decisiveness; it is not the quality score and it is not independently verified accuracy.</p><h3>How to validate a judge</h3><ol><li>Create a separate set of representative answers with human labels and an explicit rubric.</li><li>Check whether your acceptable and unacceptable examples separate; review false passes and false failures.</li><li>Compare predictions with human labels by category and difficulty. Measure precision, recall, and calibration only on an appropriate held-out set.</li><li>Choose thresholds using the costs of missed defects and unnecessary blocks. Recheck after changing the judge, rubric, model, or traffic.</li></ol><p>The fixed correct/error examples in the workbench are sensitivity controls. They show how Jev reacts to known edits. <strong>This demo does not claim a human calibration study.</strong></p><h3>Read the reason carefully</h3><p>JevEval’s reason here is a deterministic summary of question outcomes. It is helpful for reading the calculation, but it is not a new explanatory model call or proof that the judgment is correct.</p>`},
  drift: {title:'Offline experiments and live monitoring answer different questions.', content:`<p>A paired dataset asks whether a candidate changed behavior on fixed cases. Monitoring asks whether completed observations differ from a frozen reference as traffic arrives.</p><table class="concept-table"><thead><tr><th>Signal</th><th>Calculation in this app</th><th>Interpretation</th></tr></thead><tbody><tr><td>Quality change</td><td>Reference mean minus current Jev mean.</td><td>A drop can reflect changed answers, inputs, documents, or judge behavior.</td></tr><tr><td>Ticket-mix drift</td><td>Base-2 Jensen–Shannon divergence of category proportions.</td><td>A change in input composition; it can happen with stable quality.</td></tr><tr><td>KS distance</td><td>Maximum gap between empirical score distributions.</td><td>A descriptive distribution distance, not a p-value here.</td></tr></tbody></table><p>The live monitor compares a frozen 20+ sample reference with the most recent 20 completed results. It publishes a change after each evaluation finishes. Provider latency means this is request-level live monitoring, not instantaneous token-level scoring.</p><h3>Why the window matters</h3><p>A short window fills sooner and responds faster, but is more sensitive to a few answers. A long window averages more history and can delay detection or recovery. In the replay, the minimum current sample count equals your selected window.</p><p>The replay uses previously saved genuine calls and can be paused or scrubbed. <strong>It makes no fresh provider calls.</strong> Operational thresholds are visible; the app does not estimate statistical significance or establish a causal diagnosis.</p><button class="button quiet" data-jump="drift">Explore detection timing</button>`},
  proof: {title:'Verify the chain, then review the judgment.', content:`<p>Use Verify evidence to run an independent standard-library audit over the current saved lab export. It checks captured provider payloads, hashes, selected fields, probabilities, normalization, strict outcomes, threshold decisions, generation links, and native dataset result consistency.</p><h3>What this proves</h3><p>The recorded provider evidence reconciles with the score and decision shown in the app. A raw response, its content hash, the rubric, and a runnable Python example are available for each completed evaluation.</p><h3>What it does not prove</h3><p>Hashes detect inconsistent stored content; they are not provider signatures. Passing arithmetic checks does not establish human-rated judge accuracy. Real call evidence and correct calculations still need representative test cases and human review.</p><h3>A useful team walkthrough</h3><ol><li>Generate and evaluate a current-policy answer.</li><li>Inspect Jev’s three primitives and the fields it received.</li><li>Show a false guarantee control and a not-applicable Choice.</li><li>Change threshold, weights, and strict mode on saved evidence.</li><li>Generate an outdated-policy answer and diagnose it using RAG reference fields.</li><li>Run the paired dataset and open native results.</li><li>Replay fault and recovery with different windows; finish with the independent audit.</li></ol><p>Lab experiments remain separate from the monitor reference, so changing a teaching rubric cannot silently shift its live cohort.</p>`}
};
function showTopic(topic) {
  if (!guide[topic]) topic = 'architecture';
  document.querySelectorAll('[data-topic]').forEach(b => { b.classList.toggle('selected', b.dataset.topic === topic); b.setAttribute('aria-current', b.dataset.topic === topic ? 'true' : 'false'); });
  $('guide-content').innerHTML = `<h2>${guide[topic].title}</h2>${guide[topic].content}<p class="guide-source">Behavior checked against the installed DeepEval version. <a href="https://deepeval.com/docs/metrics-jev-eval" target="_blank" rel="noopener">JevEval documentation ↗</a><a href="https://docs.typesafe.ai" target="_blank" rel="noopener">TypeSafe documentation ↗</a></p>`;
}
async function verifyLab() {
  notice('Auditing the current lab evidence and calculations…');
  const report = await api('/api/lab/verify');
  openDialog('Independent evidence audit', report.verified_at || '', `<h3 class="${report.status === 'passed' ? 'audit-passed' : 'audit-failed'}">${report.status === 'passed' ? 'Evidence reconciles' : report.status === 'empty' ? 'No completed evidence yet' : 'A check failed'}</h3><p>${report.checks || 0} assertions · ${report.counts?.evaluations || 0} Jev evaluations · ${report.counts?.generator || 0} Model generations · ${report.counts?.datasets || 0} completed datasets</p><p class="help">${esc(report.scope || '')}</p><p class="help">${esc(report.limitations || '')}</p>${fold('Complete verification report', report)}<a class="button quiet" href="/api/lab/verify" target="_blank" rel="noopener">Open report JSON</a>`);
  notice(report.status === 'passed' ? 'Independent audit passed for the current saved lab evidence.' : report.error || 'Make a fresh evaluation before auditing.', report.status === 'failed');
}

async function initialize() {
  catalog = await api('/api/lab/catalog');
  $('ticket-select').innerHTML = catalog.tickets.map(t => `<option value="${t.id}">${esc(t.id)} · ${esc(t.category)} · ${esc(t.input)}</option>`).join('');
  for (const id of ['rubric-preset','dataset-rubric']) $(id).innerHTML = catalog.presets.map(p => `<option value="${p.id}">${esc(p.rubric.name)}</option>`).join('');
  $('monitor-trace').innerHTML = catalog.trace_options.slice().reverse().map(t => `<option value="${esc(t.id)}">${pct(t.quality,0)} · ${esc(scenario(t.scenario))} · ${esc(t.input)}</option>`).join('');
  $('import-trace').disabled = !catalog.trace_options.length;
  loadTicket(); loadRubric('support'); datasetSetup(); showTopic('architecture');
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
  document.querySelector('.lab-nav').addEventListener('keydown', e => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
    e.preventDefault(); const tabs = [...document.querySelectorAll('.lab-nav [data-view]')], i = tabs.indexOf(document.activeElement);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length-1 : (i+(e.key === 'ArrowRight' ? 1 : -1)+tabs.length)%tabs.length;
    showView(tabs[next].dataset.view, true);
  });
  $('ticket-select').addEventListener('change', loadTicket);
  $('rubric-preset').addEventListener('change', () => { if ($('rubric-preset').value !== 'saved') loadRubric($('rubric-preset').value); });
  $('eval-strict').addEventListener('change', strictHelp);
  for (const editor of document.querySelectorAll('.case-editor, .rubric-editor')) {
    editor.addEventListener('input', updateDraftStatus);
    editor.addEventListener('change', updateDraftStatus);
  }
  $('generation-scenario').addEventListener('change', () => notice('Knowledge base selected for the next Model generation. Generate answer, then Evaluate with Jev. The saved answer and score are unchanged.'));
  $('case-input').addEventListener('input', () => { originId = null; userEdited = true; $('answer-provenance').textContent = 'Edited test case · a new evaluation will record the exact text'; });
  $('case-output').addEventListener('input', () => { originId = null; userEdited = true; $('answer-provenance').textContent = 'User-supplied or edited answer · no Model provenance claimed'; });
  for (const kind of ['good','bad']) $(`${kind}-example`).addEventListener('click', () => {
    loadTicket(); $('generation-scenario').value = 'healthy';
    $('case-output').value = catalog.controls[$('ticket-select').value][kind];
    $('answer-provenance').textContent = `Fixed-text ${kind === 'good' ? 'reference' : 'policy-error'} control · not a model generation`;
    updateDraftStatus();
    notice(`${kind === 'good' ? 'Correct' : 'Policy-error'} control loaded. Click Evaluate with Jev for a fresh judge result. Generate answer would replace this control.`);
  });
  $('generate-case').addEventListener('click', safely(() => startCase('generate')));
  $('evaluate-case').addEventListener('click', safely(() => startCase('evaluate')));
  $('import-trace').addEventListener('click', safely(async () => { const r = await api(`/api/lab/import/${encodeURIComponent($('monitor-trace').value)}`); setCase(r.test_case, r.id, 'Saved Model monitor answer · a fresh Jev call is still required'); $('generation-scenario').value = r.scenario; if(catalog.tickets.some(t=>t.id===r.ticket_id)) $('ticket-select').value = r.ticket_id; notice('Saved Model answer imported with its generation reference.'); }));
  $('add-question').addEventListener('click', safely(() => {
    currentRubric = readRubric(); const type = $('new-question-type').value;
    const q = {key:`criterion_${Date.now()}`, label:`Custom ${type}`, type, question:'Does the answer satisfy this criterion?', weight:1};
    if (type === 'score') { q.question = 'How clearly does the answer explain the next step?'; q.levels = ['Unclear','Partly clear','Clear and actionable']; }
    if (type === 'choice') { q.question = 'Classify whether the answer offers the required next step.'; q.options = {'Meets requirement':1,'Misses requirement':0,'Not applicable':null}; }
    currentRubric.questions.push(q); renderQuestions();
    updateDraftStatus();
    document.querySelector('.question-edit:last-child details').open = true;
  }));
  $('question-editor').addEventListener('click', safely(e => { const button=e.target.closest('[data-remove]'); if(!button)return; currentRubric=readRubric();currentRubric.questions.splice(Number(button.dataset.remove),1);renderQuestions();updateDraftStatus(); }));
  $('reweight-panel').addEventListener('input', () => { clearTimeout(replayTimer); replayTimer=setTimeout(safely(recalculate),120); });
  $('case-history').addEventListener('click', safely(e => { const button=e.target.closest('[data-load-record]'); if(button)return loadRecord(button.dataset.loadRecord,true); }));
  document.body.addEventListener('click', safely(async e => { const use=e.target.closest('[data-load-case]'); if(use){$('lab-inspector').close();await loadRecord(use.dataset.loadCase,true);showView('workbench',true);notice('Saved test case loaded. Evaluate with Jev makes a fresh call.');return;} const inspect=e.target.closest('[data-inspect]'); if(inspect)return inspectRecord(inspect.dataset.inspect); const topic=e.target.closest('[data-topic]'); if(topic)showTopic(topic.dataset.topic); const jump=e.target.closest('[data-jump]'); if(jump)showView(jump.dataset.jump,true); }));
  $('close-lab-inspector').addEventListener('click', () => $('lab-inspector').close());
  $('lab-inspector').addEventListener('click', e => { if(e.target===$('lab-inspector')) {const b=e.target.getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)e.target.close();} });
  $('dataset-count').addEventListener('change', datasetSetup);
  $('run-dataset').addEventListener('click', safely(startDataset));
  $('dataset-history').addEventListener('change', safely(() => { selectedDataset=$('dataset-history').value; return loadDataset(selectedDataset); }));
  $('stop-lab').addEventListener('click', safely(async () => { const r=await api('/api/lab/stop',{}); notice(r.message); await refreshState(); }));
  $('verify-lab').addEventListener('click', safely(verifyLab));
  for(const id of ['drift-window','drift-evidence']) $(id).addEventListener('change', safely(() => {stopPlayback();return loadDrift(id==='drift-evidence');}));
  for(const id of ['drift-watch','drift-alert','drift-pass','drift-cutoff']) $(id).addEventListener('input', () => {
    stopPlayback();
    if(id==='drift-watch'&&Number($('drift-alert').value)<Number($('drift-watch').value))$('drift-alert').value=$('drift-watch').value;
    if(id==='drift-alert'&&Number($('drift-watch').value)>Number($('drift-alert').value))$('drift-watch').value=$('drift-alert').value;
    clearTimeout(driftTimer);driftTimer=setTimeout(safely(()=>loadDrift()),80);
  });
  $('replay-chart').addEventListener('click', safely(e => { const point=e.target.closest('[data-drift-index]'); if(!point)return;stopPlayback();$('drift-cutoff').value=point.dataset.driftIndex;return loadDrift(); }));
  $('replay-chart').addEventListener('keydown', e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
  $('play-drift').addEventListener('click', safely(async () => {
    if($('play-drift').textContent==='Pause replay'){stopPlayback();return;}
    if(Number($('drift-cutoff').value)>=Number($('drift-cutoff').max))$('drift-cutoff').value=0;
    $('play-drift').textContent='Pause replay';await advanceReplay();
  }));
  await refreshState();
  const latest = state.records.filter(r=>r.kind!=='dataset'&&r.status==='completed').at(-1);
  if(latest && !userEdited) await loadRecord(latest.id,true);
  showView(location.hash.slice(1));
  const stream=new EventSource('/api/lab/events');
  stream.addEventListener('open',()=>{connectionOpen=true;streamStateCurrent=false;updateConnection();});
  stream.addEventListener('state',e=>{try{const next=JSON.parse(e.data);connectionOpen=true;streamStateCurrent=true;renderState(next);}catch(error){streamStateCurrent=false;updateConnection();notice('A lab update could not be rendered. Reload to reconnect.',true);console.error(error);}});
  stream.addEventListener('heartbeat',()=>{heartbeat=Date.now();updateConnection();});
  stream.addEventListener('error',()=>{connectionOpen=false;streamStateCurrent=false;updateConnection();});
}
updateConnection();
setInterval(updateConnection,5000);
safely(initialize)();
