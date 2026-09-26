import {scoreRecord, pairedComparison, drift, auditScores} from './math.js';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = value => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
const pp = value => value == null ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)} pp`;
const badge = (value, text = value) => `<span class="badge ${escape(value)}">${escape(text.toUpperCase())}</span>`;
const jsonBlock = value => `<pre><code>${escape(JSON.stringify(value, null, 2))}</code></pre>`;
const fold = (title, content) => `<details><summary>${title}</summary>${content}</details>`;
let evidence, manifest, selected, weights, timer, toastTimer;
const ids = new Map();

function toast(message) {
  clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}

function stopPlayback() { clearInterval(timer); timer = null; $('play-drift').textContent = 'Play sequence'; }

function showView(name, changeHash = true) {
  if (!['workbench','compare','drift','adopt'].includes(name)) name = 'workbench';
  document.querySelectorAll('.view').forEach(el => { el.hidden = el.id !== name; });
  document.querySelectorAll('[data-tab]').forEach(el => {
    if (el.dataset.tab === name) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  if (name !== 'drift') stopPlayback();
  if (changeHash) history.replaceState(null, '', '#' + name);
}

function selectCase(id) {
  selected = ids.get(id) ?? ids.get('shipping-bad');
  $('case-select').value = selected.id;
  weights = selected.rubric.questions.map(q => q.weight);
  $('threshold').value = selected.rubric.threshold;
  $('strict').checked = selected.rubric.strict_mode;
  document.querySelectorAll('[data-case]').forEach(button => button.classList.toggle('selected', button.dataset.case === selected.id));
  const c = selected.test_case;
  const isBad = selected.applicable_count > 0 && !selected.passed;
  $('case-content').innerHTML = `<p class="field-label">INPUT · ${escape(selected.category.toUpperCase())}</p><p class="question-text">${escape(c.input)}</p>
    <p class="field-label">ACTUAL_OUTPUT</p><blockquote class="answer-box ${isBad ? 'bad' : ''}">${escape(c.actual_output)}</blockquote><p class="source-note">${escape(selected.answer_source)}</p>
    ${fold('Expected behavior', `<p>${escape(c.expected_output)}</p>`)}
    ${fold('Authoritative policy · context', `<pre class="policy-text">${escape(c.context?.join('\n\n'))}</pre>`)}
    ${fold('Retrieved policy · retrieval_context', `<pre class="policy-text">${escape(c.retrieval_context?.join('\n\n'))}</pre>`)}
    <p class="small muted" style="margin-top:17px;margin-bottom:0">Jev received: ${selected.rubric.evaluation_params.map(p => `<code>${escape(p)}</code>`).join(', ')}.</p>`;
  $('weights').innerHTML = `<p class="field-label" style="margin-top:19px">CRITERION WEIGHTS</p>` + selected.rubric.questions.map((q, i) => `<label class="weight-row" for="weight-${i}"><span>${escape(q.label)}</span><input id="weight-${i}" data-weight="${i}" type="range" min=".1" max="10" step=".1" value="${q.weight}"><output id="weight-output-${i}">${q.weight.toFixed(1)}</output></label>`).join('');
  document.querySelectorAll('[data-weight]').forEach(input => input.addEventListener('input', () => {
    weights[Number(input.dataset.weight)] = Number(input.value);
    $(`weight-output-${input.dataset.weight}`).textContent = Number(input.value).toFixed(1);
    renderVerdict();
  }));
  renderVerdict(); renderEvidence();
}

function renderVerdict() {
  const strict = $('strict').checked, threshold = Number($('threshold').value);
  const result = scoreRecord(selected, {weights, threshold, strict});
  $('threshold').disabled = strict;
  $('threshold-output').textContent = `${Math.round(result.effectiveThreshold * 100)}%${strict ? ' · forced by strict mode' : ''}`;
  const changed = strict !== selected.rubric.strict_mode || threshold !== selected.rubric.threshold || weights.some((w, i) => w !== selected.rubric.questions[i].weight);
  $('verdict-panel').className = `verdict-panel ${result.verdict}`;
  const message = result.verdict === 'inapplicable' ? 'No applicable criterion. Excluded from release decisions.' : result.verdict === 'fail' ? 'This answer does not meet the selected decision rule.' : 'This answer meets the selected decision rule.';
  $('verdict').innerHTML = `<div class="verdict-top"><span>${changed ? 'RECALCULATED FROM SAVED PROBABILITIES' : 'RECORDED NATIVE RESULT'}</span>${badge(result.verdict)}</div><div class="score-line"><strong>${result.verdict === 'inapplicable' ? 'N/A' : `${(result.score * 100).toFixed(1)}<span>%</span>`}</strong><p>${message}</p></div><div class="verdict-bottom"><span>${result.applicableCount} / ${selected.rubric.questions.length} applicable · threshold ${pct(result.effectiveThreshold)}</span><span>Original native score: ${pct(selected.quality)}</span></div>`;
  $('rule-explanation').textContent = result.verdict === 'inapplicable'
    ? 'DeepEval returns a native score of 1 when every question is inapplicable. This app presents N/A and refuses to treat it as evidence of quality.'
    : strict ? 'Strict mode returns 0 or 1 from the native per-question decisions. Weights have no effect in strict mode.'
    : `Weighted mean = sum(weight × value) / sum(applicable weights). ${changed ? 'Only the local decision changed; no new call was made.' : 'This matches the recorded configuration.'}`;
  $('breakdown').innerHTML = result.outcomes.map(o => {
    const q = o.question;
    const description = q.type === 'noul' ? 'Noul · probability that the proposition is true.' : q.type === 'score' ? 'Score · expected ordinal level divided by the highest level.' : 'Choice · expected local credit, excluding non-applicable mass.';
    let equation = q.type === 'noul' ? `P(true) = ${o.value.toFixed(3)}` : q.type === 'score' ? `Returned expectation ${selected.jev_response.answers[`q_${result.outcomes.indexOf(o)}`].score} / ${q.levels.length - 1} = ${o.value.toFixed(4)}` : o.applicable ? `Expected credit = ${o.value.toFixed(4)}` : 'Null-credit probability ≥ 0.5 → inapplicable';
    if (strict) equation += ` · strict: ${o.applicable ? o.strictPass ? 'pass' : 'fail' : 'excluded'}`;
    return `<section class="primitive"><div class="primitive-heading"><span class="code-tag">${escape(q.type)}</span><h4>${escape(q.label)}</h4><strong class="${o.applicable && o.value < .5 ? 'bad-text' : 'good-text'}">${o.applicable ? pct(o.value) : 'N/A'}</strong></div><p>${description}</p>
      ${Object.entries(o.probabilities).map(([label, value]) => `<div class="probability-row ${q.options?.[label] === null ? 'excluded' : ''}"><span>${escape(label)}${q.type === 'choice' ? ` <span class="muted">(credit ${q.options[label] ?? 'N/A'})</span>` : ''}</span><div class="bar"><i style="width:${value * 100}%"></i></div><strong>${Math.round(value * 100)}%</strong></div>`).join('')}
      <div class="primitive-equation">${escape(equation)}</div>${fold('Exact question', `<p>${escape(q.question)}</p>`)}</section>`;
  }).join('');
  const insights = {
    'shipping-bad': ['Fluent, relevant, and still wrong.', 'This answer invents tracking access and guarantees next-day delivery. Compare it with “Correct answer”; the real captured scores separate 15.1% from 95.5%. A lower threshold can make a bad answer pass without improving it.'],
    'shipping-good': ['The positive control matters too.', 'This answer states the actual 3–5 business day rule and avoids an exact guarantee. Raise the threshold above its score: the verdict changes while Jev’s probabilities stay fixed.'],
    'primitives-review': ['Three primitives, three different rules.', 'Noul tests a proposition, Score uses ordered levels, and Choice maps named outcomes to local credits. This call used native strict mode. Turn it off to inspect the weighted score. Strict does not mean every probability must equal 1.'],
    'not-applicable': ['A native 100% can be a non-answer.', 'The information-only shipping question needs no escalation. Jev puts all probability on the null-credit option. There are zero applicable questions; the app labels this N/A and excludes it from the release gate.'],
    'rag-stale': ['Faithfulness alone can reward the wrong answer.', 'The answer agrees with the retrieved obsolete policy (87%) but contradicts the authoritative reference (1%). These are custom Jev questions using DeepEval fields, not the separate built-in RAG metric classes.'],
  };
  const insight = insights[selected.id] ?? ['Review the individual criteria.', 'A combined score can hide the reason for a failure. Inspect the actual answer and reference, then compare the Noul and Score outcomes. Confidence describes judge decisiveness; it is not measured accuracy.'];
  $('case-insight').innerHTML = `<strong>${insight[0]}</strong><p>${insight[1]}</p>`;
}

function reproductionCode(record) {
  const string = value => `json.loads(${JSON.stringify(JSON.stringify(value))})`;
  return `import json, os\nfrom deepeval.metrics import JevEval\nfrom deepeval.metrics.jev_eval import Noul, Score, Choice\nfrom deepeval.models.system_one.typesafe_model import TypeSafeModel\nfrom deepeval.test_case import LLMTestCase, SingleTurnParams\n\nconfig = ${string(record.rubric)}\ncase = LLMTestCase(**${string(record.test_case)})\nquestions = []\nfor q in config["questions"]:\n    extra = {"weight": q["weight"]}\n    if q["type"] == "score": extra["levels"] = q["levels"]\n    if q["type"] == "choice": extra["options"] = q["options"]\n    cls = {"noul": Noul, "score": Score, "choice": Choice}[q["type"]]\n    questions.append(cls(q["question"], **extra))\nmetric = JevEval(\n    name=config["name"], questions=questions,\n    evaluation_params=[SingleTurnParams(p) for p in config["evaluation_params"]],\n    system_one_model=TypeSafeModel(model="jev-1.13.0", api_key=os.environ["TYPESAFE_API_KEY"]),\n    threshold=config["threshold"], strict_mode=config["strict_mode"], async_mode=False,\n)\nmetric.measure(case)  # Fresh API call; requires your own key.\nprint(metric.score, metric.is_successful(), metric.score_breakdown)`;
}

function renderEvidence() {
  const r = selected;
  $('raw-evidence').innerHTML = `<div class="evidence-meta"><span>PUBLIC ID <code>${escape(r.id)}</code></span><span>CAPTURED ${escape(r.captured_date)}</span><span>JUDGE <code>${escape(r.judge_model)}</code></span><span>RECORDED LATENCY ${(r.eval_ms / 1000).toFixed(2)}s</span><span>TOKENS ${r.jev_usage.input_tokens} in / ${r.jev_usage.output_tokens} out</span></div>
    ${fold('1 · Exact request sent to Jev', jsonBlock(r.jev_request))}
    ${fold('2 · Raw Jev response', jsonBlock(r.jev_response))}
    ${fold('3 · Rubric and local score breakdown', jsonBlock({rubric: r.rubric, breakdown: r.breakdown, weighted_score: r.weighted_score, strict_score: r.strict_score, native_score: r.quality, confidence: r.confidence}))}
    ${fold('4 · Reproduce this case with native DeepEval', `<p>A fresh provider call may produce different probabilities. This code uses the recorded configuration. The repository also includes an executable CLI.</p><pre><code>${escape(reproductionCode(r))}</code></pre><p><code>python scripts/evaluate_example.py --case ${escape(r.id)}</code></p>`)}
    ${fold('5 · Integrity and verification limits', `<p>Build-time audit: ${manifest.audit_checks.toLocaleString()} assertions over ${manifest.records} records. This browser also verifies the evidence file’s SHA-256 and recomputes every captured score. Hashes establish consistency, not provider authenticity or human accuracy.</p><p class="hash">Jev request SHA-256: ${escape(r.jev_request_sha256)}<br>Jev response SHA-256: ${escape(r.jev_response_sha256)}<br>Rubric SHA-256: ${escape(r.config_sha256)}</p><p>Score histograms are rounded by the provider; the native metric uses the returned expected ordinal score. Confidence is decisiveness, not calibration against human judgments.</p>`)}
    <p class="footnote">Evidence is curated for teaching. Private generator configuration and original local identifiers are excluded. The public dataset is not a benchmark of the generating model.</p>`;
}

function stat(label, number, note, cls = '') { return `<div class="stat"><span class="stat-label">${label}</span><strong class="stat-number ${cls}">${number}</strong><span class="stat-note">${note}</span></div>`; }

function renderComparison() {
  const minPassRate = Number($('gate-pass').value), maxDrop = Number($('gate-drop').value);
  $('gate-pass-output').textContent = `${Math.round(minPassRate * 100)}%`;
  $('gate-drop-output').textContent = `${Math.round(maxDrop * 100)} pp`;
  const c = pairedComparison(evidence.pairs, evidence.records, {minPassRate, maxDrop, omitLast: $('omit-pair').checked});
  $('comparison-stats').innerHTML = stat('Reference mean', pct(c.referenceMean), 'Current-policy answers', 'good-text') + stat('Candidate mean', pct(c.candidateMean), 'Obsolete-policy answers', 'bad-text') + stat('Candidate pass rate', pct(c.passRate), `${c.complete} / ${evidence.pairs.length} complete, applicable pairs`, c.passRate < minPassRate ? 'bad-text' : 'good-text');
  $('release-gate').className = `gate-banner ${c.verdict}`;
  $('release-gate').innerHTML = `${badge(c.verdict, c.verdict === 'block' ? 'Release blocked' : c.verdict === 'pass' ? 'Rule passes' : 'Incomplete')}
    <div><strong>${c.verdict === 'incomplete' ? 'Missing evidence prevents a release decision.' : c.verdict === 'block' ? 'The candidate fails the selected release conditions.' : 'The candidate meets your selected conditions.'}</strong><p>${c.complete} complete pairs · candidate pass rate ${pct(c.passRate)} ≥ ${pct(minPassRate)} required · mean drop ${pp(c.drop)} ≤ ${(maxDrop * 100).toFixed(0)} pp allowed</p></div>`;
  $('pair-table').innerHTML = c.pairs.map(p => `<tr><td><strong>${escape(p.category)}</strong><small>${escape(p.ticket_id)}</small></td><td><div class="pair-meter"><span class="mono">${pct(p.a?.quality)}</span><div class="bar"><i style="width:${(p.a?.quality ?? 0) * 100}%"></i></div></div></td><td><div class="pair-meter candidate"><span class="mono">${pct(p.b?.quality)}</span><div class="bar"><i style="width:${(p.b?.quality ?? 0) * 100}%"></i></div></div></td><td class="bad-text mono">${pp(p.delta)}</td><td>${p.complete ? badge(p.b.quality >= .8 ? 'pass' : 'fail') : badge('incomplete','missing')}</td><td>${p.b ? `<a href="#workbench" data-inspect="${escape(p.b.id)}">Inspect ↗</a>` : '<span class="muted">Withheld in replay</span>'}</td></tr>`).join('');
}

function driftInputs() {
  const reference = evidence.replay.reference_ids.map(id => ids.get(id));
  const mix = $('drift-scenario').value === 'mix';
  const events = mix ? Array.from({length: 10}, (_, i) => reference[i % 2 === 0 ? 0 : 2]) : [...evidence.replay.fault_ids, ...evidence.replay.recovery_ids].map(id => ids.get(id));
  return {reference, events, mix};
}

function renderDrift() {
  const {reference, events, mix} = driftInputs(), cutoff = Number($('drift-position').value), window = Number($('drift-window').value);
  const d = drift(reference, events, {cutoff, window});
  const labels = {warming:'Warming up', stable:'Stable', watch:'Watch', alert:'Alert'};
  $('playback-position').textContent = `Event ${cutoff} / ${events.length}`;
  $('drift-stats').innerHTML = stat('Quality signal', labels[d.qualityStatus], `${d.current.length} / ${window} events in window`, d.qualityStatus === 'alert' ? 'bad-text' : d.qualityStatus === 'stable' ? 'good-text' : '')
    + stat('Quality drop', d.drop == null ? '—' : pp(d.drop), `Reference ${pct(d.referenceMean)} · current ${pct(d.currentMean)}`, d.drop >= .15 ? 'bad-text' : '')
    + stat('Ticket mix · JSD', d.mix == null ? '—' : d.mix.toFixed(3), labels[d.mixStatus], d.mixStatus === 'alert' ? 'bad-text' : '')
    + stat('Score distribution · KS', d.ks == null ? '—' : d.ks.toFixed(3), 'Descriptive distance; no p-value');
  const width = 1080, height = 296, left = 62, right = 1027, top = 42, bottom = 246;
  const x = index => left + index * (right - left) / events.length, y = value => bottom - value * (bottom - top);
  const points = Array.from({length: cutoff}, (_, i) => ({index: i + 1, ...drift(reference, events, {cutoff: i + 1, window})})).filter(p => p.currentMean !== null);
  const polyline = points.map(p => `${x(p.index)},${y(p.currentMean)}`).join(' ');
  const line = (value, color, dash = '') => `<line x1="${left}" y1="${y(value)}" x2="${right}" y2="${y(value)}" stroke="${color}" ${dash ? `stroke-dasharray="${dash}"` : ''}/>`;
  $('drift-chart').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Rolling Jev quality through ${cutoff} playback events. Current quality ${pct(d.currentMean)}. Quality signal ${labels[d.qualityStatus]}.">
    <rect x="${left}" y="${top}" width="${(right-left)/2}" height="${bottom-top}" fill="${mix ? 'var(--amber-soft)' : 'var(--red-soft)'}"/><rect x="${x(5)}" y="${top}" width="${(right-left)/2}" height="${bottom-top}" fill="${mix ? 'var(--amber-soft)' : 'var(--teal-soft)'}"/>
    <text x="${left+12}" y="24" fill="var(--muted)" font-size="11" font-family="Instrument, sans-serif">${mix ? 'RETURNS + BILLING RECORDS REUSED' : 'OUTDATED-POLICY RECORDS'}</text>${mix ? '' : `<text x="${x(5)+12}" y="24" fill="var(--muted)" font-size="11" font-family="Instrument, sans-serif">REFERENCE RECORDS REUSED AS RECOVERY</text>`}
    ${[0,.25,.5,.75,1].map(v => `${line(v,'var(--line)')}<text x="${left-12}" y="${y(v)+4}" fill="var(--muted)" text-anchor="end" font-size="10" font-family="monospace">${v*100}%</text>`).join('')}
    ${line(d.referenceMean, 'var(--chart-reference)', '6 5')}${line(d.referenceMean-.15, 'var(--red)', '4 5')}
    ${points.length > 1 ? `<polyline points="${polyline}" fill="none" stroke="var(--teal)" stroke-width="2.5" stroke-linejoin="round"/>` : ''}
    ${points.map(p => `<circle cx="${x(p.index)}" cy="${y(p.currentMean)}" r="${p.index===cutoff?5:3}" fill="${p.qualityStatus==='warming'?'var(--chart-warmup)':'var(--teal)'}"/>`).join('')}
    ${Array.from({length:11},(_,i)=>`<text x="${x(i)}" y="${bottom+23}" fill="var(--muted)" text-anchor="middle" font-size="10" font-family="monospace">${i}</text>`).join('')}
    <text x="${right}" y="${height-6}" fill="var(--muted)" text-anchor="end" font-size="10" font-family="Instrument, sans-serif">Playback event · constructed order</text></svg>`;
  const firstAlert = Array.from({length:cutoff},(_,i)=>i+1).find(index => drift(reference,events,{cutoff:index,window}).qualityStatus === 'alert');
  $('drift-caption').textContent = `Frozen reference: 5 actual records. ${firstAlert ? `First quality alert at event ${firstAlert}.` : 'No quality alert has fired at this position.'} Before the window fills, the partial mean is descriptive only.`;
  $('window-members').innerHTML = d.current.length ? d.current.map(r => `<div class="member"><a href="#workbench" data-inspect="${escape(r.id)}">${escape(r.title)}</a><span>${pct(r.quality)}</span>${badge(r.passed ? 'pass' : 'fail')}</div>`).join('') : '<p class="muted small">Press Play sequence or move the timeline. Each event points to an inspectable saved Jev call.</p>';
  $('drift-provenance').textContent = mix
    ? 'This composition experiment repeatedly reuses two current-policy records (Returns and Billing). Repetitions are playback events, not independent observations or new API calls. The local live monitor uses fresh completions and a 20-record window.'
    : `${evidence.replay.note} This sequence uses 10 unique captured calls; the five reference calls are reused as recovery. Playback speed is unrelated to provider latency. The local live monitor uses fresh completions and a 20-record window.`;
}

async function initialize() {
  const responses = await Promise.all([fetch('evidence.json'), fetch('manifest.json')]);
  if (responses.some(r => !r.ok)) throw new Error('Could not load the evidence files');
  const [bytes, metadata] = await Promise.all([responses[0].arrayBuffer(), responses[1].json()]);
  const hashBytes = await crypto.subtle.digest('SHA-256', bytes);
  const hash = Array.from(new Uint8Array(hashBytes), b => b.toString(16).padStart(2,'0')).join('');
  if (hash !== metadata.evidence_sha256) throw new Error('The evidence file does not match its published manifest');
  evidence = JSON.parse(new TextDecoder().decode(bytes)); manifest = metadata;
  if (evidence.mode !== 'recorded' || evidence.records.length !== manifest.records) throw new Error('Unexpected evidence manifest');
  const count = auditScores(evidence.records);
  evidence.records.forEach(r => ids.set(r.id, r));
  $('record-count').textContent = count;
  $('verification-status').textContent = `✓ File verified · all ${count} scores reconciled`;
  $('case-select').innerHTML = '<optgroup label="Concept examples">' + evidence.records.filter(r => !r.id.startsWith('reference-') && !r.id.startsWith('candidate-')).map(r => `<option value="${escape(r.id)}">${escape(r.title)}</option>`).join('') + '</optgroup><optgroup label="Paired dataset">' + evidence.records.filter(r => r.id.startsWith('reference-') || r.id.startsWith('candidate-')).map(r => `<option value="${escape(r.id)}">${escape(r.title)}</option>`).join('') + '</optgroup>';
  $('case-select').disabled = false;
  selectCase(new URLSearchParams(location.search).get('case') ?? 'shipping-bad');
  renderComparison(); renderDrift(); showView(location.hash.slice(1), false);
  $('case-select').addEventListener('change', event => selectCase(event.target.value));
  $('threshold').addEventListener('input', renderVerdict);
  $('strict').addEventListener('change', renderVerdict);
  $('reset-rule').addEventListener('click', () => selectCase(selected.id));
  $('verify-button').addEventListener('click', () => { const n = auditScores(evidence.records); toast(`All ${n} saved scores reconcile. The file hash matched at load; the Python build audit checked ${manifest.audit_checks.toLocaleString()} assertions.`); });
  ['gate-pass','gate-drop'].forEach(id => $(id).addEventListener('input', renderComparison));
  $('omit-pair').addEventListener('change', renderComparison);
  $('drift-position').addEventListener('input', () => { stopPlayback(); renderDrift(); });
  $('drift-window').addEventListener('change', renderDrift);
  $('drift-scenario').addEventListener('change', () => { stopPlayback(); $('drift-position').value = 0; renderDrift(); });
  $('reset-drift').addEventListener('click', () => { stopPlayback(); $('drift-position').value = 0; renderDrift(); });
  $('play-drift').addEventListener('click', () => {
    if (timer) { stopPlayback(); return; }
    if (Number($('drift-position').value) >= 10) $('drift-position').value = 0;
    $('play-drift').textContent = 'Pause sequence';
    timer = setInterval(() => { $('drift-position').value = Number($('drift-position').value) + 1; renderDrift(); if (Number($('drift-position').value) >= 10) stopPlayback(); }, 1000);
  });
  document.addEventListener('click', event => {
    const shortcut = event.target.closest('[data-case]');
    if (shortcut) selectCase(shortcut.dataset.case);
    const inspect = event.target.closest('[data-inspect]');
    if (inspect) { event.preventDefault(); selectCase(inspect.dataset.inspect); showView('workbench'); document.querySelector('.tabs').scrollIntoView({block:'start'}); }
    const nav = event.target.closest('a[href^="#"]');
    if (nav && !inspect && nav.getAttribute('href') !== '#main') { event.preventDefault(); showView(nav.getAttribute('href').slice(1)); }
  });
  window.addEventListener('hashchange', () => showView(location.hash.slice(1), false));
}

initialize().catch(error => {
  $('verification-status').textContent = 'Evidence verification failed';
  $('fatal').textContent = `${error.message}. Reload the page. No fallback scores have been shown.`;
  $('fatal').hidden = false;
  document.querySelectorAll('.view').forEach(el => { el.hidden = true; });
});
