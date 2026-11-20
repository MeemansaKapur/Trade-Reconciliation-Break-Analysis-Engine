-- Pass 1 (P1_REF): exact reference match.
-- Internal ext_ref = custodian ext_trade_id, both sides still unmatched. Strictly one-to-one.
SELECT i.int_trade_id, e.ext_trade_id
FROM   int_unmatched i
JOIN   ext_unmatched e ON i.ext_ref = e.ext_trade_id
QUALIFY ROW_NUMBER() OVER (PARTITION BY e.ext_trade_id ORDER BY i.int_trade_id) = 1;
