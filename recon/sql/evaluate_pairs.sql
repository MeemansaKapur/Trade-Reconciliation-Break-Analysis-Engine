-- Tier every pair/group produced by the passes. Order matters: a quantity difference is reported
-- before a date difference, and both before any amount judgement.
CREATE OR REPLACE TABLE pairs AS
WITH agg AS (
  SELECT l.int_trade_id,
         ANY_VALUE(l.pass) AS pass, ANY_VALUE(l.rule_id) AS rule_id, COUNT(*) AS n_legs,
         SUM(e.quantity) AS ext_qty, SUM(e.amount) AS ext_amount,
         MAX(e.settle_bd) AS ext_settle_bd_max, MIN(e.settle_bd) AS ext_settle_bd_min,
         STRING_AGG(e.ext_trade_id, ',' ORDER BY e.ext_trade_id) AS ext_trade_ids
  FROM links l JOIN ext_n e USING (ext_trade_id)
  GROUP BY l.int_trade_id
)
SELECT 'M-' || i.int_trade_id AS match_id, i.int_trade_id, a.ext_trade_ids, a.n_legs, a.pass, a.rule_id,
       i.isin, i.currency, i.counterparty, i.account, i.amount AS int_amount, a.ext_amount,
       a.ext_amount - i.amount              AS amount_diff,
       a.ext_qty - i.quantity               AS qty_diff,
       a.ext_settle_bd_max - i.settle_bd    AS day_diff,
       CASE
         WHEN a.ext_qty <> i.quantity                                         THEN 'QTY_MISMATCH'
         WHEN a.ext_settle_bd_max <> i.settle_bd
           OR a.ext_settle_bd_min <> i.settle_bd                             THEN 'TIMING'
         WHEN ABS(a.ext_amount - i.amount) <= $matched_abs + 1e-9            THEN 'MATCHED'
         WHEN ABS(a.ext_amount - i.amount) <= tol_limit(i.currency, i.amount) + 1e-9 THEN 'TOLERATED'
         WHEN i.currency <> $base_currency
          AND ABS(a.ext_amount - i.amount) / ABS(i.amount) BETWEEN $fx_band_min_pct AND $fx_band_pct
                                                                              THEN 'FX_ROUNDING'
         ELSE 'AMOUNT_MISMATCH'
       END AS outcome
FROM agg a JOIN int_n i USING (int_trade_id);
