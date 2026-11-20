-- Pass 3 (P3_GROUP): one internal trade = several external fills.
-- Group unmatched external rows by economic key; a group matches when its legs' quantities sum exactly
-- to the internal quantity and the summed amount is within tolerance.
WITH g AS (
  SELECT isin, side, currency, counterparty, settle_bd,
         COUNT(*) AS n_legs, SUM(quantity) AS q, SUM(amount) AS a,
         LIST(ext_trade_id ORDER BY ext_trade_id) AS ids
  FROM   ext_unmatched
  GROUP  BY isin, side, currency, counterparty, settle_bd
  HAVING COUNT(*) BETWEEN 2 AND $max_legs
),
cand AS (
  SELECT i.int_trade_id, g.ids, ABS(i.amount - g.a) AS amt_diff,
         g.isin, g.side, g.currency, g.counterparty, g.settle_bd
  FROM   int_unmatched i
  JOIN   g ON i.isin = g.isin AND i.side = g.side AND i.currency = g.currency
          AND i.counterparty = g.counterparty AND i.settle_bd = g.settle_bd
  WHERE  i.quantity = g.q
    AND  ABS(i.amount - g.a) <= tol_limit(i.currency, i.amount) + 1e-9
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY isin, side, currency, counterparty, settle_bd
                               ORDER BY amt_diff, int_trade_id) AS rn_g
  FROM cand
)
SELECT int_trade_id, UNNEST(ids) AS ext_trade_id FROM ranked WHERE rn_g = 1;
