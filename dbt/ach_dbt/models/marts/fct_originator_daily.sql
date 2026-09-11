-- The detector's input and the data-health dashboard's source. One row per
-- originator per effective date with rates already computed, plus the
-- trailing-14-day baseline the detector compares against (computed here in
-- SQL so the dashboard and the detector cannot disagree about the baseline).
--
-- Baselines come in two flavours on purpose:
--   * baseline_*_rate  -- mean of the daily rates; what a human reads on a chart
--   * baseline_*_sum   -- pooled counts; what the detector uses, because a
--                         pooled rate over ~2,800 entries is far more stable
--                         than the mean of fourteen noisy daily rates.
with daily as (
    select * from {{ ref('int_originator_daily') }}
),

rates as (
    select
        *,
        unauthorized_return_count * 1.0 / nullif(entry_count, 0)       as unauthorized_return_rate,
        admin_return_count * 1.0 / nullif(entry_count, 0)              as admin_return_rate,
        nsf_return_count * 1.0 / nullif(entry_count, 0)                as nsf_return_rate,
        return_count * 1.0 / nullif(entry_count, 0)                    as overall_return_rate,
        duplicate_entry_count * 1.0 / nullif(entry_count, 0)           as duplicate_ratio
    from daily
),

with_baseline as (
    select
        *,
        -- Trailing window excludes the current row (rows between 14 preceding
        -- and 1 preceding) so a spike can't inflate its own baseline.
        count(*) over w                                                as baseline_days,
        avg(entry_count) over w                                        as baseline_entry_count,
        avg(unauthorized_return_rate) over w                           as baseline_unauthorized_return_rate,
        avg(admin_return_rate) over w                                  as baseline_admin_return_rate,
        avg(nsf_return_rate) over w                                    as baseline_nsf_return_rate,
        avg(avg_settlement_lag_days) over w                            as baseline_settlement_lag_days,
        sum(entry_count) over w                                        as baseline_entry_sum,
        sum(unauthorized_return_count) over w                          as baseline_unauthorized_sum,
        sum(admin_return_count) over w                                 as baseline_admin_sum,
        sum(nsf_return_count) over w                                   as baseline_nsf_sum
    from rates
    window w as (
        partition by company_id
        order by effective_entry_date
        rows between 14 preceding and 1 preceding
    )
)

select
    company_id,
    effective_entry_date,
    entry_count,
    file_count,
    debit_count,
    credit_count,
    amount_cents,
    debit_amount_cents,
    credit_amount_cents,
    return_count,
    unauthorized_return_count,
    admin_return_count,
    nsf_return_count,
    other_return_count,
    duplicate_entry_count,
    avg_settlement_lag_days,
    max_settlement_lag_days,
    unauthorized_return_rate,
    admin_return_rate,
    nsf_return_rate,
    overall_return_rate,
    duplicate_ratio,
    baseline_days,
    baseline_entry_count,
    baseline_unauthorized_return_rate,
    baseline_admin_return_rate,
    baseline_nsf_return_rate,
    baseline_settlement_lag_days,
    baseline_entry_sum,
    baseline_unauthorized_sum,
    baseline_admin_sum,
    baseline_nsf_sum,
    -- Public NACHA thresholds applied per day, for the dashboard's "days over
    -- threshold" panel only. A single day is too small a denominator to act
    -- on; the official rolling-60-day computation lives in fct_return_rate_60d.
    unauthorized_return_rate > 0.005                                   as over_unauthorized_threshold,
    admin_return_rate > 0.03                                           as over_admin_threshold,
    overall_return_rate > 0.15                                         as over_overall_threshold
from with_baseline
