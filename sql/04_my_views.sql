SELECT grl.item_number,
       grl.store_id,
       ...,          -- posting_date, from the header
       ...,          -- date_id, from the line
       CASE WHEN ... THEN ... ELSE ... END AS movement_type,
       CASE WHEN ... THEN ... ELSE ... END AS qty_in,
       CASE WHEN ... THEN ... ELSE ... END AS qty_out,
       ...           -- document_number, from the header
FROM   fact_goods_receipt_line grl
JOIN   fact_goods_receipt      gr USING (goods_receipt_id)
LIMIT 5;
