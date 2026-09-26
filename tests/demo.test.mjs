import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {auditScores, scoreRecord, pairedComparison, drift, jsDivergence, ksDistance} from '../demo/math.js';

const raw = readFileSync(new URL('../demo/evidence.json', import.meta.url));
const data = JSON.parse(raw);
const manifest = JSON.parse(readFileSync(new URL('../demo/manifest.json', import.meta.url)));
const byId = new Map(data.records.map(r => [r.id, r]));
const ref = data.replay.reference_ids.map(id => byId.get(id));
const events = [...data.replay.fault_ids, ...data.replay.recovery_ids].map(id => byId.get(id));
const close = (a, b) => assert.ok(Math.abs(a-b) < 1e-10, `${a} differs from ${b}`);

test('all real captured scores and exact evidence bytes reconcile', () => {
  assert.equal(auditScores(data.records), 17);
  assert.equal(createHash('sha256').update(raw).digest('hex'), manifest.evidence_sha256);
  assert.equal(manifest.records, 17);
});

test('positive and negative controls separate without a simulated provider', () => {
  const good = scoreRecord(byId.get('shipping-good')), bad = scoreRecord(byId.get('shipping-bad'));
  assert.equal(good.verdict, 'pass'); assert.equal(bad.verdict, 'fail');
  close(good.score, .9553333333333333); close(bad.score, .15066666666666667);
  const newRule = scoreRecord(byId.get('shipping-bad'), {threshold: .1});
  assert.equal(newRule.verdict, 'pass'); close(newRule.score, bad.score);
});

test('strict mode, weighting and N/A are different from probability equals one', () => {
  const r = byId.get('primitives-review'), before = JSON.stringify(r);
  const strict = scoreRecord(r);
  assert.equal(strict.score, 1); assert.equal(strict.effectiveThreshold, 1);
  assert.ok(scoreRecord(r, {strict:false}).score < 1);
  assert.equal(scoreRecord(r, {weights:[10,.1,.1]}).score, 1);
  const na = scoreRecord(byId.get('not-applicable'));
  assert.equal(na.score, 1); assert.equal(na.nativePassed, true);
  assert.equal(na.verdict, 'inapplicable'); assert.equal(na.applicableCount, 0);
  assert.equal(JSON.stringify(r), before);
});

test('Choice null mass of exactly .5 excludes the question', () => {
  const r = structuredClone(byId.get('not-applicable'));
  const q = r.rubric.questions[0], names = Object.keys(q.options);
  q.options = {good:1,bad:0,na:null};
  r.jev_response.answers.q_0.probabilities = {good:.4,bad:.1,na:.5};
  assert.equal(scoreRecord(r).verdict, 'inapplicable');
  r.jev_response.answers.q_0.probabilities = {good:.4,bad:.11,na:.49};
  close(scoreRecord(r).score, .4/.51);
});

test('recorded RAG response is faithful to stale retrieval and wrong against reference', () => {
  const values = scoreRecord(byId.get('rag-stale')).outcomes.map(o => o.value);
  assert.deepEqual(values, [.87,.01,.01]);
});

test('native ordinal expectation is used even when histogram rounding differs', () => {
  const r = byId.get('shipping-good'), score = r.jev_response.answers.q_1;
  const normalized = score.score / 3;
  close(scoreRecord(r).outcomes[1].value, normalized);
  const rounded = Object.entries(score.probabilities).reduce((s,[i,p]) => s+Number(i)*p,0);
  assert.ok(Math.abs(score.score-rounded) <= .035000001);
});

test('release gate blocks the real candidate, and missing or N/A pairs remain incomplete', () => {
  const result = pairedComparison(data.pairs, data.records);
  assert.equal(result.verdict, 'block'); assert.equal(result.complete, 5);
  close(result.referenceMean, .9509333333333333); close(result.candidateMean, .2584);
  assert.equal(result.passRate, 0);
  assert.equal(pairedComparison(data.pairs, data.records, {omitLast:true,minPassRate:0,maxDrop:1}).verdict,'incomplete');
  const pairs = structuredClone(data.pairs); pairs[1].candidate_id = 'not-applicable';
  assert.equal(pairedComparison(pairs,data.records,{minPassRate:0,maxDrop:1}).verdict,'incomplete');
  assert.equal(pairedComparison(data.pairs,data.records,{minPassRate:0,maxDrop:1}).verdict,'pass');
  assert.equal(pairedComparison([],data.records).verdict,'incomplete');
});

test('warm-up, alert and reused-reference recovery depend on the actual rolling window', () => {
  const warm = drift(ref,events,{cutoff:4});
  assert.equal(warm.qualityStatus,'warming'); assert.equal(warm.drop,null);
  const fault = drift(ref,events,{cutoff:5});
  assert.equal(fault.qualityStatus,'alert'); close(fault.drop,.6925333333333333);
  assert.equal(fault.mix,0); assert.equal(fault.ks,1);
  const recovered = drift(ref,events,{cutoff:10});
  assert.equal(recovered.qualityStatus,'stable'); close(recovered.drop,0);
  assert.deepEqual(recovered.current.map(r=>r.id),ref.map(r=>r.id));
  assert.equal(drift(ref,events,{cutoff:1,window:1}).qualityStatus,'alert');
});

test('mix-only playback separates composition from quality', () => {
  const mix = Array.from({length:10},(_,i)=>ref[i%2===0?0:2]);
  const result = drift(ref,mix);
  assert.equal(result.qualityStatus,'stable'); assert.equal(result.mixStatus,'alert');
  assert.ok(result.mix > .2);
  assert.equal(jsDivergence(['a'],['a']),0); assert.equal(jsDivergence(['a'],['b']),1);
  assert.equal(ksDistance([.1,.2],[.8,.9]),1);
});

test('errors and inapplicable results do not become quality observations', () => {
  const extra = [{status:'failed',quality:1,applicable_count:1},byId.get('not-applicable')];
  const result = drift(ref,[...events.slice(0,4),...extra]);
  assert.equal(result.excluded,2); assert.equal(result.current.length,4);
  assert.equal(result.qualityStatus,'warming');
});

test('score and breakdown corruption are rejected instead of rendered', () => {
  for (const mutate of [r=>{r.quality=.99;},r=>{r.breakdown[0].value=.99;},r=>{r.jev_response.answers.q_0.noul=.99;}]) {
    const records = structuredClone(data.records); mutate(records[1]);
    assert.throws(()=>auditScores(records),/does not reconcile/);
  }
  assert.throws(()=>scoreRecord(data.records[0],{weights:[0,1,1]}),/Invalid weights/);
  assert.throws(()=>scoreRecord(data.records[0],{threshold:NaN}),/Invalid threshold/);
});
