// Public replay arithmetic. Inputs are recorded Jev payloads; no model is called.
export const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
const top = entries => entries.reduce((best, item) => item[1] > best[1] ? item : best)[0];
const nearOrAbove = (value, threshold) => value >= threshold - 1e-12;

export function scoreRecord(record, {weights, threshold = record.rubric.threshold, strict = record.rubric.strict_mode} = {}) {
  const questions = record.rubric.questions;
  const selectedWeights = weights ?? questions.map(q => q.weight);
  if (selectedWeights.length !== questions.length || selectedWeights.some(w => !Number.isFinite(w) || w <= 0)) throw new Error('Invalid weights');
  if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) throw new Error('Invalid threshold');
  const outcomes = questions.map((q, i) => {
    const raw = record.jev_response.answers[`q_${i}`];
    let value, probabilities, strictPass, applicable = true;
    if (q.type === 'noul') {
      value = raw.noul;
      probabilities = {true: value, false: 1 - value};
      strictPass = value >= .5;
    } else if (q.type === 'score') {
      value = Math.min(1, Math.max(0, raw.score / (q.levels.length - 1)));
      probabilities = Object.fromEntries(q.levels.map((level, n) => [level, raw.probabilities[String(n)]]));
      strictPass = top(Object.entries(probabilities)) === q.levels.at(-1);
    } else if (q.type === 'choice') {
      probabilities = Object.fromEntries(Object.keys(q.options).map(name => [name, raw.probabilities[name]]));
      const credits = Object.entries(q.options).filter(([, credit]) => credit !== null);
      const excludedMass = Object.entries(q.options).filter(([, credit]) => credit === null).reduce((s, [name]) => s + probabilities[name], 0);
      const mass = credits.reduce((s, [name]) => s + probabilities[name], 0);
      applicable = excludedMass < .5 && mass > 0;
      value = applicable ? credits.reduce((s, [name, credit]) => s + probabilities[name] * credit, 0) / mass : null;
      strictPass = applicable ? q.options[top(credits.map(([name]) => [name, probabilities[name]]))] >= 1 : null;
    } else throw new Error('Unknown question type');
    return {value, applicable, probabilities, strictPass, weight: selectedWeights[i], question: q};
  });
  const applicable = outcomes.filter(o => o.applicable);
  const weighted = applicable.length ? applicable.reduce((s, o) => s + o.value * o.weight, 0) / applicable.reduce((s, o) => s + o.weight, 0) : 1;
  const strictScore = applicable.every(o => o.strictPass) ? 1 : 0;
  const score = strict ? strictScore : weighted;
  const effectiveThreshold = strict ? 1 : threshold;
  return {score, weighted, strictScore, effectiveThreshold, applicableCount: applicable.length, outcomes,
    verdict: !applicable.length ? 'inapplicable' : score >= effectiveThreshold ? 'pass' : 'fail',
    nativePassed: score >= effectiveThreshold};
}

export function pairedComparison(pairs, records, {threshold = .8, minPassRate = .9, maxDrop = .05, omitLast = false} = {}) {
  const byId = new Map(records.map(r => [r.id, r]));
  const result = pairs.map((p, i) => {
    const a = byId.get(p.reference_id), b = omitLast && i === pairs.length - 1 ? null : byId.get(p.candidate_id);
    const complete = Boolean(a && b && a.status === 'completed' && b.status === 'completed' && a.applicable_count > 0 && b.applicable_count > 0);
    return {...p, a, b, complete, delta: complete ? b.quality - a.quality : null};
  });
  const complete = result.filter(p => p.complete);
  const referenceMean = mean(complete.map(p => p.a.quality)), candidateMean = mean(complete.map(p => p.b.quality));
  const passRate = mean(complete.map(p => Number(p.b.quality >= threshold)));
  const drop = complete.length ? referenceMean - candidateMean : null;
  const ready = pairs.length > 0 && complete.length === pairs.length;
  return {pairs: result, complete: complete.length, referenceMean, candidateMean, passRate, drop,
    verdict: !ready ? 'incomplete' : passRate >= minPassRate && drop <= maxDrop + 1e-12 ? 'pass' : 'block'};
}

export function jsDivergence(a, b) {
  if (!a.length || !b.length) return null;
  let result = 0;
  for (const key of new Set([...a, ...b])) {
    const p = a.filter(x => x === key).length / a.length, q = b.filter(x => x === key).length / b.length, m = (p + q) / 2;
    if (p) result += .5 * p * Math.log2(p / m);
    if (q) result += .5 * q * Math.log2(q / m);
  }
  return Math.min(1, Math.max(0, result));
}

export function ksDistance(a, b) {
  if (!a.length || !b.length) return null;
  return Math.max(...[...new Set([...a, ...b])].map(x => Math.abs(a.filter(v => v <= x).length / a.length - b.filter(v => v <= x).length / b.length)));
}

export function drift(reference, events, {window = 5, cutoff = events.length, watch = .08, alert = .15} = {}) {
  const valid = r => r && r.status === 'completed' && r.applicable_count > 0 && Number.isFinite(r.quality);
  const ref = reference.filter(valid), received = events.slice(0, cutoff), usable = received.filter(valid), current = usable.slice(-window);
  const enough = ref.length >= 5 && current.length === window;
  const referenceMean = mean(ref.map(r => r.quality)), currentMean = mean(current.map(r => r.quality));
  const drop = enough ? referenceMean - currentMean : null;
  const mix = enough ? jsDivergence(ref.map(r => r.category), current.map(r => r.category)) : null;
  const qualityStatus = !enough ? 'warming' : nearOrAbove(drop, alert) ? 'alert' : nearOrAbove(drop, watch) ? 'watch' : 'stable';
  const mixStatus = !enough ? 'warming' : nearOrAbove(mix, .2) ? 'alert' : nearOrAbove(mix, .1) ? 'watch' : 'stable';
  return {reference: ref, current, referenceMean, currentMean, drop, mix, qualityStatus, mixStatus,
    excluded: received.length - usable.length, ks: enough ? ksDistance(ref.map(r => r.quality), current.map(r => r.quality)) : null};
}

export function auditScores(records) {
  if (!records.length) throw new Error('The evidence bundle is empty');
  const close = (a, b) => Math.abs(a - b) < 1e-9;
  for (const r of records) {
    const s = scoreRecord(r);
    if (!close(s.score, r.quality) || !close(s.weighted, r.weighted_score) || !close(s.strictScore, r.strict_score)
      || s.applicableCount !== r.applicable_count || s.nativePassed !== r.passed || !close(s.effectiveThreshold, r.effective_threshold)) {
      throw new Error(`Recorded score does not reconcile: ${r.id}`);
    }
    s.outcomes.forEach((o, i) => {
      const b = r.breakdown[i];
      if (o.applicable !== b.applicable || (o.value === null ? b.value !== null : !close(o.value, b.value))) throw new Error(`Recorded breakdown does not reconcile: ${r.id}`);
    });
  }
  return records.length;
}
