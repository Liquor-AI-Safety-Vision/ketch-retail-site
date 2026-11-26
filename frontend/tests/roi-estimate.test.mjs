import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  BENCHMARKS,
  DEFAULT_RECOVERY,
  factoryEstimate,
  fmtUsd,
  retailEstimate,
} from '../src/lib/roi.ts';

const closeTo = (actual, expected, epsilon = 1e-6) =>
  assert.ok(Math.abs(actual - expected) < epsilon, `${actual} ≈ ${expected}`);

test('retail estimate matches the documented illustrative model', () => {
  // Monthly revenue $100,000 · 1.5% benchmark shrink · 30% assumed avoidable
  // share → $18,000 estimated annual shrink → $5,400 illustrative avoidable loss.
  const { annualShrink, recoverable } = retailEstimate(100_000, 0.3);
  closeTo(annualShrink, 18_000);
  closeTo(recoverable, 5_400);
});

test('the model pins the published benchmark and the default assumption', () => {
  assert.equal(BENCHMARKS.retail.shrinkRate, 0.015);
  assert.equal(DEFAULT_RECOVERY, 0.3);
  // The avoidable-share slider allows 10%–50%; the default must stay inside it.
  assert.ok(DEFAULT_RECOVERY >= 0.1 && DEFAULT_RECOVERY <= 0.5);
});

test('recoverable is the assumed share of TOTAL estimated shrink, never more', () => {
  const { annualShrink, recoverable } = retailEstimate(250_000, 0.5);
  closeTo(annualShrink, 250_000 * 12 * 0.015);
  closeTo(recoverable, annualShrink * 0.5);
  assert.ok(recoverable <= annualShrink);
});

test('edge inputs behave linearly without inventing new bounds', () => {
  assert.deepEqual(retailEstimate(0, 0.3), { annualShrink: 0, recoverable: 0 });
  closeTo(retailEstimate(100_000, 0).recoverable, 0);
  const full = retailEstimate(100_000, 1);
  closeTo(full.recoverable, full.annualShrink);
  // Scaling revenue scales both outputs linearly (no hidden fixed costs).
  const a = retailEstimate(50_000, 0.3);
  const b = retailEstimate(100_000, 0.3);
  closeTo(b.annualShrink, a.annualShrink * 2);
  closeTo(b.recoverable, a.recoverable * 2);
});

test('factory estimate spans the published 1%–3.5% shrink band', () => {
  const low = factoryEstimate(1_000, 0.3);
  closeTo(low.shrinkLow, 120);
  closeTo(low.shrinkHigh, 420);
  closeTo(low.recoverableLow, 36);
  closeTo(low.recoverableHigh, 126);
  assert.ok(low.shrinkLow < low.shrinkHigh);
  assert.ok(low.recoverableLow < low.recoverableHigh);
});

test('currency formatting rounds to whole units', () => {
  assert.equal(fmtUsd(17_999.4), '$17,999');
  assert.equal(fmtUsd(17_999.6), '$18,000');
  assert.equal(fmtUsd(18_000), '$18,000');
});
