-- ============================================================
-- Analytical layer.
--
-- Sales are stored ONCE, at receipt grain. They are never copied
-- into a movement table. vw_stock_movement UNIONs the four
-- stock-affecting processes into one complete ledger, so stock
-- can be reconstructed without duplicating anything.
--
-- Ordering (fact_purchase_order) is deliberately NOT in the ledger:
-- raising a PO does not move stock. Only receiving does.
-- ============================================================


-- Dropped first so this file can be re-run after a column list changes
-- (CREATE OR REPLACE VIEW cannot alter a view's columns).
DROP VIEW IF EXISTS vw_format_demand         CASCADE;
DROP VIEW IF EXISTS vw_basket_profile        CASCADE;
DROP VIEW IF EXISTS vw_transfer_loss         CASCADE;
DROP VIEW IF EXISTS vw_supplier_performance  CASCADE;
DROP VIEW IF EXISTS vw_negative_inventory    CASCADE;
DROP VIEW IF EXISTS vw_dead_stock_diagnostic CASCADE;
DROP VIEW IF EXISTS vw_daily_stock_balance   CASCADE;
DROP VIEW IF EXISTS vw_stock_on_hand         CASCADE;
DROP VIEW IF EXISTS vw_daily_sales           CASCADE;
DROP VIEW IF EXISTS vw_stock_movement        CASCADE;

-- ------------------------------------------------------------
-- THE UNIFIED MOVEMENT LEDGER
-- One transfer line becomes TWO rows: an OUT at the origin store
-- and an IN at the destination. A single-store movement table
-- could not express that — which is why this is a view.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_stock_movement AS
-- RECEIVING
SELECT grl.item_number,
       grl.store_id,
       gr.posting_date,
       grl.date_id,
       CASE WHEN gr.is_supplier_return THEN 'supplier_return' ELSE 'purchase' END AS movement_type,
       CASE WHEN gr.is_supplier_return THEN 0 ELSE grl.quantity_received END       AS qty_in,
       CASE WHEN gr.is_supplier_return THEN grl.quantity_received ELSE 0 END       AS qty_out,
       gr.gr_number AS document_number
FROM   fact_goods_receipt_line grl
JOIN   fact_goods_receipt      gr USING (goods_receipt_id)

UNION ALL
-- SELLING  (positive line = sale, negative line = customer return)
SELECT rl.item_number,
       rl.store_id,
       d.date,
       rl.date_id,
       CASE WHEN rl.quantity >= 0 THEN 'sale' ELSE 'customer_return' END,
       CASE WHEN rl.quantity <  0 THEN -rl.quantity ELSE 0 END,
       CASE WHEN rl.quantity >= 0 THEN  rl.quantity ELSE 0 END,
       'RCP' || rl.receipt_id
FROM   fact_receipt_line rl
JOIN   dim_date d ON d.date_id = rl.date_id

UNION ALL
-- TRANSFER OUT (origin store)
SELECT tl.item_number, t.from_store_id, t.posting_date, tl.date_id,
       'transfer_out', 0, tl.quantity_shipped, t.transfer_number
FROM   fact_transfer_line tl
JOIN   fact_transfer      t USING (transfer_id)

UNION ALL
-- TRANSFER IN (destination store)
SELECT tl.item_number, t.to_store_id, t.posting_date, tl.date_id,
       'transfer_in', tl.quantity_received, 0, t.transfer_number
FROM   fact_transfer_line tl
JOIN   fact_transfer      t USING (transfer_id)

UNION ALL
-- CORRECTING
SELECT a.item_number, a.store_id, a.posting_date, a.date_id,
       a.adjustment_type, 0, a.quantity, 'ADJ' || a.adjustment_id
FROM   fact_adjustment a;


-- ------------------------------------------------------------
-- DAILY SALES — a view over the receipt lines, never a stored table
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_daily_sales AS
SELECT rl.item_number,
       rl.store_id,
       rl.date_id,
       d.date,
       SUM(CASE WHEN rl.quantity > 0 THEN rl.quantity ELSE 0 END)   AS units_sold,
       SUM(CASE WHEN rl.quantity < 0 THEN -rl.quantity ELSE 0 END)  AS units_returned,
       SUM(rl.quantity)                                             AS net_units,
       SUM(rl.amount)                                               AS net_revenue
FROM   fact_receipt_line rl
JOIN   dim_date d ON d.date_id = rl.date_id
GROUP  BY rl.item_number, rl.store_id, rl.date_id, d.date;


-- ------------------------------------------------------------
-- RUNNING BALANCE — stock reconstructed day by day from movements.
-- This is the window-function centrepiece: stock is never stored.
-- One row per item / store / day on which something moved.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_daily_stock_balance AS
WITH daily AS (
    SELECT item_number, store_id, posting_date,
           SUM(qty_in) - SUM(qty_out) AS net_change
    FROM   vw_stock_movement
    GROUP  BY item_number, store_id, posting_date
)
SELECT item_number,
       store_id,
       posting_date,
       net_change,
       SUM(net_change) OVER (PARTITION BY item_number, store_id
                             ORDER BY posting_date
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS stock_on_hand
FROM   daily;


-- ------------------------------------------------------------
-- CURRENT STOCK ON HAND (the closing balance per item/store)
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_stock_on_hand AS
SELECT item_number,
       store_id,
       SUM(qty_in) - SUM(qty_out) AS stock_on_hand
FROM   vw_stock_movement
GROUP  BY item_number, store_id;


-- ------------------------------------------------------------
-- DEAD STOCK DIAGNOSTIC
-- Turns the existing 90-day zero-sales report into a diagnosis:
-- it quantifies the trapped capital AND separates the causes.
--
-- Negative stock is clamped to zero for the value calculation
-- (you cannot have negative capital on a shelf) and surfaced
-- separately as its own actionable watchlist.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_dead_stock_diagnostic AS
WITH bounds AS (
    SELECT MAX(date) AS as_of, MAX(date) - INTERVAL '90 days' AS cutoff FROM dim_date
),
last_sale AS (
    SELECT item_number, store_id, MAX(date) AS last_sale_date
    FROM   vw_daily_sales
    WHERE  units_sold > 0
    GROUP  BY item_number, store_id
),
soh AS (
    SELECT * FROM vw_stock_on_hand
)
SELECT s.item_number,
       s.store_id,
       p.description_en,
       p.description_ar,
       p.brand,
       p.category,
       p.pack_size,
       p.season,
       st.store_name,
       st.region,
       st.store_size,
       s.stock_on_hand,
       ls.last_sale_date,
       (SELECT as_of FROM bounds) - ls.last_sale_date       AS days_since_last_sale,
       GREATEST(s.stock_on_hand, 0) * p.unit_cost           AS trapped_capital,
       CASE
           WHEN s.stock_on_hand < 0
               THEN 'Negative inventory - blocks auto-replenishment'
           WHEN s.stock_on_hand > 20 AND ls.last_sale_date IS NULL
               THEN 'Phantom stock - system shows stock, shelf likely empty'
           WHEN p.season <> 'All-Year'
               THEN 'Out of season - do not discount, it will sell in season'
           ELSE 'No demand - genuine assortment failure'
       END                                                  AS root_cause
FROM   soh s
JOIN   dim_product p  ON p.item_number = s.item_number
JOIN   dim_store   st ON st.store_id   = s.store_id
LEFT   JOIN last_sale ls ON ls.item_number = s.item_number AND ls.store_id = s.store_id
WHERE  ls.last_sale_date IS NULL
   OR  ls.last_sale_date < (SELECT cutoff FROM bounds);


-- ------------------------------------------------------------
-- NEGATIVE INVENTORY WATCHLIST
-- Tawfeer's rule: no more than 100 negative items per store.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_negative_inventory AS
SELECT st.store_id, st.store_name, st.region,
       COUNT(*)                          AS negative_items,
       SUM(s.stock_on_hand)              AS total_negative_units,
       CASE WHEN COUNT(*) > 100 THEN 'BREACH' ELSE 'OK' END AS threshold_status
FROM   vw_stock_on_hand s
JOIN   dim_store st ON st.store_id = s.store_id
WHERE  s.stock_on_hand < 0
GROUP  BY st.store_id, st.store_name, st.region;


-- ------------------------------------------------------------
-- SUPPLIER PERFORMANCE — lead time and fill rate.
-- VMI / direct-store-delivery vendors are EXCLUDED from lead time:
-- their order and delivery are the same event, so it is undefined.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_supplier_performance AS
WITH lead AS (
    SELECT po.vendor_id,
           AVG(gr.posting_date - po.order_date)::NUMERIC(6,1) AS avg_lead_days,
           MAX(gr.posting_date - po.order_date)               AS max_lead_days,
           COUNT(*)                                           AS deliveries
    FROM   fact_goods_receipt gr
    JOIN   fact_purchase_order po ON po.purchase_order_id = gr.purchase_order_id
    GROUP  BY po.vendor_id
),
-- Receipts are collapsed to one row per PO line BEFORE joining, so a
-- part-delivered order is not counted twice. Joining the one-to-many
-- directly would fan out quantity_ordered and deflate the fill rate.
received_per_po_line AS (
    SELECT po_line_id, SUM(quantity_received) AS received
    FROM   fact_goods_receipt_line
    WHERE  po_line_id IS NOT NULL
    GROUP  BY po_line_id
),
fill AS (
    SELECT po.vendor_id,
           SUM(pol.quantity_ordered)        AS ordered,
           SUM(COALESCE(r.received, 0))     AS received
    FROM   fact_purchase_order_line pol
    JOIN   fact_purchase_order po ON po.purchase_order_id = pol.purchase_order_id
    LEFT   JOIN received_per_po_line r ON r.po_line_id = pol.po_line_id
    GROUP  BY po.vendor_id
)
SELECT v.vendor_id,
       v.vendor_name,
       v.supply_model,
       CASE WHEN v.supply_model = 'ordered' THEN l.avg_lead_days END AS avg_lead_days,
       CASE WHEN v.supply_model = 'ordered' THEN l.max_lead_days END AS max_lead_days,
       l.deliveries,
       f.ordered,
       f.received,
       ROUND(100.0 * f.received / NULLIF(f.ordered,0), 1)            AS fill_rate_pct
FROM   dim_vendor v
LEFT   JOIN lead l ON l.vendor_id = v.vendor_id
LEFT   JOIN fill f ON f.vendor_id = v.vendor_id;


-- ------------------------------------------------------------
-- IN-TRANSIT LOSS — stock that left one store and never arrived.
-- Only visible because a transfer carries two stores.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_transfer_loss AS
SELECT t.transfer_id, t.transfer_number,
       sf.store_name AS from_store, stt.store_name AS to_store,
       t.shipment_date, t.posting_date,
       t.posting_date - t.shipment_date        AS transit_days,
       tl.item_number, p.description_en,
       tl.quantity_shipped, tl.quantity_received,
       tl.quantity_shipped - tl.quantity_received                    AS units_lost,
       (tl.quantity_shipped - tl.quantity_received) * p.unit_cost    AS value_lost
FROM   fact_transfer_line tl
JOIN   fact_transfer t   ON t.transfer_id = tl.transfer_id
JOIN   dim_product   p   ON p.item_number = tl.item_number
JOIN   dim_store     sf  ON sf.store_id  = t.from_store_id
JOIN   dim_store     stt ON stt.store_id = t.to_store_id
WHERE  tl.quantity_shipped <> tl.quantity_received;


-- ------------------------------------------------------------
-- BASKET PROFILE — the receipt grain paying off.
-- Basket size measured BOTH ways, because item count and basket
-- value are correlated but NOT proportional: a small basket of
-- premium items is worth more than a large basket of cheap ones.
-- Return-only receipts are excluded from the sales-basket average.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_basket_profile AS
SELECT st.store_id, st.store_name, st.store_size, st.region,
       COUNT(*)                                   AS receipts,
       ROUND(AVG(r.total_items), 2)               AS avg_basket_items,
       ROUND(AVG(r.total_amount), 2)              AS avg_basket_value,
       ROUND(AVG(r.total_amount / NULLIF(r.total_items,0)), 2) AS avg_price_per_item
FROM   fact_receipt r
JOIN   dim_store st ON st.store_id = r.store_id
WHERE  r.total_items > 0
GROUP  BY st.store_id, st.store_name, st.store_size, st.region;


-- ------------------------------------------------------------
-- FORMAT DEMAND / CANNIBALIZATION — does pack size predict death?
-- Compares sell-through across the formats of the same brand+category.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_format_demand AS
SELECT p.category, p.brand, p.pack_size,
       COUNT(DISTINCT p.item_number)                        AS items,
       COALESCE(SUM(ds.units_sold), 0)                      AS units_sold,
       ROUND(AVG(GREATEST(soh.stock_on_hand,0) * p.unit_cost), 2) AS avg_stock_value
FROM   dim_product p
LEFT   JOIN vw_daily_sales  ds  ON ds.item_number  = p.item_number
LEFT   JOIN vw_stock_on_hand soh ON soh.item_number = p.item_number
GROUP  BY p.category, p.brand, p.pack_size;
