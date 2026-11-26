/**
 * ROI / loss-cost model — shared by the retail and factory landing calculators.
 *
 * HONESTY RULE (project §Ground rules): every constant here is an *industry
 * benchmark* with a named source, NOT a claim about any specific store/factory.
 * The calculators apply these to a number the owner types in, and label the
 * result as a benchmark their pilot turns into real figures. We never invent a
 * store-specific loss number.
 *
 * Sources (verified 2026-09-04):
 *  - NACS preliminary 2025 data: $1,703 merchandise shrink/store/month
 *    ($20,436 annualized here for display).
 *  - NRF 2022 National Retail Security Survey: 1.4% average FY2021 retail
 *    shrink and a 1.5% five-year average. The calculator uses the latter.
 *  - Avg dishonest-employee case $1,137 — Jack L. Hayes Int'l 35th Annual Retail
 *    Theft Survey (FY2022).
 *  - 6.6% of US robberies occur at convenience stores — FBI Crime in the U.S.,
 *    2019 (c-stores are low-cash by design — the cost is frequency + violence,
 *    not register size).
 *  - VN theft/vandalism loss ≈ 3.5% of annual sales for *affected* firms
 *    (0.4% across all firms) — World Bank Enterprise Survey, Vietnam 2015. We
 *    use a conservative 1%–3.5% band for the calculator.
 *  - 5–20% of plant productivity lost to unplanned downtime — ISA / Deloitte
 *    (2017). (Distinct from Siemens' "11% of turnover" cost figure, 2024.)
 *  - USD≈26,300 VND — mid-market, June 2026.
 */

export const BENCHMARKS = {
  retail: {
    shrinkRate: 0.015,
    avgEmployeeCaseUsd: 1137,
    cstoreShrinkPerStoreUsd: 1703 * 12,
    robberyShareAtCstores: 0.066,
  },
  factory: {
    shrinkLow: 0.01,
    shrinkHigh: 0.035,
    productivityLossLow: 0.05,
    productivityLossHigh: 0.2,
  },
  usdVnd: 26300,
} as const

/** Default for the "assumed avoidable share of total estimated shrink" slider.
 *  Owner-adjustable assumption with no published source — the UI must present
 *  the result as an illustrative scenario, never a prediction or guarantee. */
export const DEFAULT_RECOVERY = 0.3

export interface RetailEstimate {
  annualShrink: number
  recoverable: number
}

/**
 * Illustrative scenario, not a business claim.
 *  annualShrink = monthlyRevenue (USD) × 12 × 1.5% benchmark shrink
 *  recoverable  = annualShrink × recovery, where recovery is the OWNER'S
 *                 assumed avoidable share of that total estimated shrink
 *                 (no verified industry rate exists). Values are returned
 *                 unvalidated; the slider constrains recovery to [0.1, 0.5].
 */
export function retailEstimate(monthlyRevenue: number, recovery: number): RetailEstimate {
  const annualShrink = monthlyRevenue * 12 * BENCHMARKS.retail.shrinkRate
  return { annualShrink, recoverable: annualShrink * recovery }
}

export interface FactoryEstimate {
  shrinkLow: number
  shrinkHigh: number
  recoverableLow: number
  recoverableHigh: number
}

/** monthlyValue in VND (monthly inventory/output value), recovery in [0,1]. */
export function factoryEstimate(monthlyValue: number, recovery: number): FactoryEstimate {
  const annual = monthlyValue * 12
  const shrinkLow = annual * BENCHMARKS.factory.shrinkLow
  const shrinkHigh = annual * BENCHMARKS.factory.shrinkHigh
  return {
    shrinkLow,
    shrinkHigh,
    recoverableLow: shrinkLow * recovery,
    recoverableHigh: shrinkHigh * recovery,
  }
}

const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})
const VND = new Intl.NumberFormat('vi-VN', {
  style: 'currency',
  currency: 'VND',
  maximumFractionDigits: 0,
})

export const fmtUsd = (n: number) => USD.format(Math.round(n))
export const fmtVnd = (n: number) => VND.format(Math.round(n))
