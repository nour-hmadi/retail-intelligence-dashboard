-- ============================================================
-- Load the generated CSVs into PostgreSQL.
--
-- Run with psql from the folder holding the CSVs, e.g.
--     psql -d tawfeer -f 01_schema.sql
--     cd /path/to/data_full          <-- IMPORTANT: run from the data folder
--     psql -d tawfeer -f /path/to/02_load.sql
--
-- \copy (client-side) is used rather than COPY so you do NOT need
-- superuser rights and the path is resolved on YOUR machine.
-- Order matters: dimensions before facts, headers before lines.
-- ============================================================

\echo 'Loading dimensions...'
\copy dim_date            (date_id, date, month, quarter, year)                                  FROM 'dim_date.csv'                CSV HEADER
\copy dim_store           (store_id, store_code, store_name, store_type, store_size, region)     FROM 'dim_store.csv'               CSV HEADER
\copy dim_vendor          (vendor_id, vendor_name, supply_model)                                 FROM 'dim_vendor.csv'              CSV HEADER
\copy dim_product         (item_number, barcode, description_en, description_ar, brand, category, pack_size, uom, vendor_id, unit_cost, unit_price, season) FROM 'dim_product.csv' CSV HEADER
\copy dim_staff           (staff_id, staff_name, store_id, role)                                 FROM 'dim_staff.csv'               CSV HEADER

\echo 'Loading selling...'
\copy fact_receipt        (receipt_id, receipt_number, store_id, date_id, receipt_datetime, staff_id, total_amount, total_items) FROM 'fact_receipt.csv' CSV HEADER
\copy fact_receipt_line   (receipt_line_id, receipt_id, item_number, store_id, date_id, quantity, amount)         FROM 'fact_receiptline.csv' CSV HEADER

\echo 'Loading ordering...'
\copy fact_purchase_order      (purchase_order_id, po_number, vendor_id, store_id, buyer_staff_id, order_date, date_id, total_amount) FROM 'fact_purchaseorder.csv' CSV HEADER
\copy fact_purchase_order_line (po_line_id, purchase_order_id, item_number, store_id, date_id, quantity_ordered, amount)              FROM 'fact_purchaseorderline.csv' CSV HEADER

\echo 'Loading receiving...'
\copy fact_goods_receipt      (goods_receipt_id, document_number, purchase_order_id, supplier_invoice_number, vendor_id, store_id, posting_date, date_id, is_supplier_return, total_amount) FROM 'fact_goodsreceipt.csv' CSV HEADER
\copy fact_goods_receipt_line (gr_line_id, goods_receipt_id, po_line_id, item_number, store_id, date_id, quantity_received, amount)   FROM 'fact_goodsreceiptline.csv' CSV HEADER

\echo 'Loading transferring...'
\copy fact_transfer      (transfer_id, transfer_number, from_store_id, to_store_id, shipment_date, posting_date, date_id, total_amount) FROM 'fact_transfer.csv' CSV HEADER
\copy fact_transfer_line (transfer_line_id, transfer_id, item_number, date_id, quantity_shipped, quantity_received, amount)             FROM 'fact_transferline.csv' CSV HEADER

\echo 'Loading corrections...'
\copy fact_adjustment (adjustment_id, item_number, store_id, date_id, posting_date, adjustment_type, quantity, amount, reason) FROM 'fact_adjustment.csv' CSV HEADER

ANALYZE;

\echo 'Row counts:'
SELECT 'dim_product' t, count(*) FROM dim_product
UNION ALL SELECT 'fact_receipt',            count(*) FROM fact_receipt
UNION ALL SELECT 'fact_receipt_line',       count(*) FROM fact_receipt_line
UNION ALL SELECT 'fact_purchase_order',     count(*) FROM fact_purchase_order
UNION ALL SELECT 'fact_purchase_order_line',count(*) FROM fact_purchase_order_line
UNION ALL SELECT 'fact_goods_receipt',      count(*) FROM fact_goods_receipt
UNION ALL SELECT 'fact_goods_receipt_line', count(*) FROM fact_goods_receipt_line
UNION ALL SELECT 'fact_transfer',           count(*) FROM fact_transfer
UNION ALL SELECT 'fact_transfer_line',      count(*) FROM fact_transfer_line
UNION ALL SELECT 'fact_adjustment',         count(*) FROM fact_adjustment;
