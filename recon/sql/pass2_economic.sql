-- Pass 2 (P2_ECON): economic match for records without a usable reference.
-- Same ISIN / side / currency / counterparty, equal quantity, settle date within the business-day window,
-- amount within the candidate band. Ambiguity resolved by mutual-best ranking with deterministic tie-breaks,
-- so the same input always yields the same pairs. Run iteratively until no new pairs appear.
WITH candidates AS (
  SELECT i.int_trade_id, e.ext_trade_id,
         ABS(i.amount - e.amount)        AS amt_diff,
         ABS(e.settle_bd - i.settle_bd)  AS day_diff
  FROM   int_unmatched i
  JOIN   ext_unmatched e
    ON   i.isin = e.isin AND i.side = e.side
   AND   i.currency = e.currency AND i.counterparty = e.counterparty
   AND   i.quantity = e.quantity
   AND   ABS(e.settle_bd - i.settle_bd) <= $settle_window_bd
   AND   ABS(i.amount - e.amount) <= $max_candidate_diff_pct * ABS(i.amount)
),
ranked AS (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY int_trade_id ORDER BY day_diff, amt_diff, ext_trade_id) AS rn_i,
    ROW_NUMBER() OVER (PARTITION BY ext_trade_id ORDER BY day_diff, amt_diff, int_trade_id) AS rn_e
  FROM candidates
)
SELECT int_trade_id, ext_trade_id FROM ranked WHERE rn_i = 1 AND rn_e = 1;
