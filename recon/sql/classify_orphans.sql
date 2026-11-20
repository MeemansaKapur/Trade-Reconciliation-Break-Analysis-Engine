-- Classify records no pass could pair. Priority order (documented in docs/break_taxonomy.md):
--   1. DUPLICATE        internal row identical to an earlier internal row except its ID
--   2. diagnostic near-match (same ISIN/side/ccy/counterparty, same qty OR same price, date within window)
--        -> QTY_MISMATCH / TIMING / AMOUNT_MISMATCH depending on what differs
--   3. no candidate     -> MISSING_EXTERNAL / MISSING_INTERNAL
-- Duplicates are checked first: checked after "missing", every duplicate would be mislabelled MISSING_EXTERNAL.
-- The diagnostic join is NOT a match: it only suggests a counterpart for the controller.
CREATE OR REPLACE TABLE int_orph AS SELECT * FROM int_unmatched;
CREATE OR REPLACE TABLE ext_orph AS SELECT * FROM ext_unmatched;

CREATE OR REPLACE TABLE dup AS
SELECT o.int_trade_id, MIN(x.int_trade_id) AS dup_of
FROM   int_orph o
JOIN   int_n x
  ON   x.int_trade_id < o.int_trade_id
 AND   x.isin = o.isin AND x.side = o.side AND x.quantity = o.quantity AND x.price = o.price
 AND   x.currency = o.currency AND x.trade_date = o.trade_date AND x.settle_date = o.settle_date
 AND   x.counterparty = o.counterparty AND x.account = o.account
GROUP  BY o.int_trade_id;

CREATE OR REPLACE TABLE diag AS
WITH cand AS (
  SELECT i.int_trade_id, e.ext_trade_id,
         (i.quantity = e.quantity)              AS same_qty,
         ABS(e.settle_bd - i.settle_bd)         AS day_diff,
         ABS(e.amount - i.amount)               AS amt_diff,
         e.quantity - i.quantity                AS qty_diff,
         e.settle_bd - i.settle_bd              AS signed_day_diff,
         e.amount - i.amount                    AS amount_diff
  FROM   int_orph i
  JOIN   ext_orph e
    ON   i.isin = e.isin AND i.side = e.side AND i.currency = e.currency AND i.counterparty = e.counterparty
  WHERE  i.int_trade_id NOT IN (SELECT int_trade_id FROM dup)
    AND  ABS(e.settle_bd - i.settle_bd) <= $diag_settle_window_bd
    AND  (i.quantity = e.quantity OR ABS(i.price - e.price) < 0.00001)
),
ranked AS (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY int_trade_id ORDER BY same_qty DESC, day_diff, amt_diff, ext_trade_id) AS rn_i,
    ROW_NUMBER() OVER (PARTITION BY ext_trade_id ORDER BY same_qty DESC, day_diff, amt_diff, int_trade_id) AS rn_e
  FROM cand
)
SELECT int_trade_id, ext_trade_id, qty_diff, signed_day_diff AS day_diff, amount_diff,
       CASE WHEN qty_diff <> 0 THEN 'QTY_MISMATCH'
            WHEN signed_day_diff <> 0 THEN 'TIMING'
            ELSE 'AMOUNT_MISMATCH' END AS break_type,
       CASE WHEN qty_diff <> 0 THEN 'CLS_DIAG_QTY'
            WHEN signed_day_diff <> 0 THEN 'CLS_DIAG_TIMING'
            ELSE 'CLS_DIAG_AMOUNT' END AS rule_id
FROM ranked WHERE rn_i = 1 AND rn_e = 1;
