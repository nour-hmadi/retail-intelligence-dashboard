-- ============================================================
-- Retail & Logistics Intelligence Dashboard — PostgreSQL DDL
-- Galaxy schema: one fact table per business process,
-- sharing conformed dimensions.
--
-- Naming: the logical model (dbdiagram) uses PascalCase;
-- the physical model uses snake_case, which is PostgreSQL
-- convention. The mapping is one-to-one.
-- ============================================================

DROP VIEW IF EXISTS vw_basket_profile          CASCADE;
DROP VIEW IF EXISTS vw_supplier_performance    CASCADE;
DROP VIEW IF EXISTS vw_transfer_loss           CASCADE;
DROP VIEW IF EXISTS vw_dead_stock_diagnostic   CASCADE;
DROP VIEW IF EXISTS vw_stock_on_hand           CASCADE;
DROP VIEW IF EXISTS vw_daily_sales             CASCADE;
DROP VIEW IF EXISTS vw_stock_movement          CASCADE;

DROP TABLE IF EXISTS fact_adjustment            CASCADE;
DROP TABLE IF EXISTS fact_transfer_line         CASCADE;
DROP TABLE IF EXISTS fact_transfer              CASCADE;
DROP TABLE IF EXISTS fact_goods_receipt_line    CASCADE;
DROP TABLE IF EXISTS fact_goods_receipt         CASCADE;
DROP TABLE IF EXISTS fact_purchase_order_line   CASCADE;
DROP TABLE IF EXISTS fact_purchase_order        CASCADE;
DROP TABLE IF EXISTS fact_receipt_line          CASCADE;
DROP TABLE IF EXISTS fact_receipt               CASCADE;
DROP TABLE IF EXISTS dim_staff                  CASCADE;
DROP TABLE IF EXISTS dim_product                CASCADE;
DROP TABLE IF EXISTS dim_vendor                 CASCADE;
DROP TABLE IF EXISTS dim_store                  CASCADE;
DROP TABLE IF EXISTS dim_date                   CASCADE;

-- ------------------------------------------------------------
-- CONFORMED DIMENSIONS
-- ------------------------------------------------------------
CREATE TABLE dim_date (
    date_id     INTEGER PRIMARY KEY,          -- yyyymmdd
    date        DATE    NOT NULL,
    month       SMALLINT NOT NULL,
    quarter     SMALLINT NOT NULL,
    year        SMALLINT NOT NULL
);

CREATE TABLE dim_store (
    store_id    INTEGER PRIMARY KEY,
    store_code  VARCHAR(10),
    store_name  VARCHAR(100),
    store_type  VARCHAR(30),                  -- Hypermarket / Supermarket / Express
    store_size  VARCHAR(10),                  -- Large / Medium / Small
    region      VARCHAR(40)
);

CREATE TABLE dim_vendor (
    vendor_id    INTEGER PRIMARY KEY,
    vendor_name  VARCHAR(120),
    supply_model VARCHAR(30)                  -- ordered | vendor_managed | direct_store_delivery
);
COMMENT ON COLUMN dim_vendor.supply_model IS
  'VMI/DSD vendors set their own quantities and deliver with no prior PO. '
  'EXCLUDE them from lead-time analysis: order and delivery are the same event.';

CREATE TABLE dim_product (
    item_number     INTEGER PRIMARY KEY,
    barcode         VARCHAR(20),
    description_en  VARCHAR(150),
    description_ar  VARCHAR(150),
    brand           VARCHAR(60),
    category        VARCHAR(60),
    pack_size       VARCHAR(20),              -- format: 330ml, 1L, 500g
    uom             VARCHAR(20),
    vendor_id       INTEGER REFERENCES dim_vendor(vendor_id),
    unit_cost       NUMERIC(12,2),            -- what the chain pays the supplier
    unit_price      NUMERIC(12,2),            -- what the customer pays
    season          VARCHAR(20)
);

CREATE TABLE dim_staff (
    staff_id   INTEGER PRIMARY KEY,
    staff_name VARCHAR(80),
    store_id   INTEGER REFERENCES dim_store(store_id),
    role       VARCHAR(30)                    -- Cashier / Senior Cashier / Shift Lead / Buyer
);

-- ------------------------------------------------------------
-- PROCESS 1 — SELLING
-- ------------------------------------------------------------
CREATE TABLE fact_receipt (
    receipt_id       INTEGER PRIMARY KEY,
    receipt_number   VARCHAR(30),
    store_id         INTEGER REFERENCES dim_store(store_id),
    date_id          INTEGER REFERENCES dim_date(date_id),
    receipt_datetime TIMESTAMP,
    staff_id         INTEGER REFERENCES dim_staff(staff_id),
    total_amount     NUMERIC(12,2),           -- basket value
    total_items      INTEGER                  -- basket size by count
);

CREATE TABLE fact_receipt_line (
    receipt_line_id BIGINT PRIMARY KEY,
    receipt_id      INTEGER REFERENCES fact_receipt(receipt_id),
    item_number     INTEGER REFERENCES dim_product(item_number),
    store_id        INTEGER REFERENCES dim_store(store_id),
    date_id         INTEGER REFERENCES dim_date(date_id),
    quantity        INTEGER,                  -- positive = sale, negative = customer return
    amount          NUMERIC(12,2)
);

-- ------------------------------------------------------------
-- PROCESS 2a — ORDERING (no stock effect)
-- ------------------------------------------------------------
CREATE TABLE fact_purchase_order (
    purchase_order_id INTEGER PRIMARY KEY,
    po_number         VARCHAR(20),
    vendor_id         INTEGER REFERENCES dim_vendor(vendor_id),
    store_id          INTEGER REFERENCES dim_store(store_id),
    buyer_staff_id    INTEGER REFERENCES dim_staff(staff_id),
    order_date        DATE,
    date_id           INTEGER REFERENCES dim_date(date_id),
    total_amount      NUMERIC(14,2)
);

CREATE TABLE fact_purchase_order_line (
    po_line_id        BIGINT PRIMARY KEY,
    purchase_order_id INTEGER REFERENCES fact_purchase_order(purchase_order_id),
    item_number       INTEGER REFERENCES dim_product(item_number),
    store_id          INTEGER REFERENCES dim_store(store_id),
    date_id           INTEGER REFERENCES dim_date(date_id),
    quantity_ordered  INTEGER,
    amount            NUMERIC(12,2)
);

-- ------------------------------------------------------------
-- PROCESS 2b — RECEIVING (stock changes here)
-- ------------------------------------------------------------
CREATE TABLE fact_goods_receipt (
    goods_receipt_id        INTEGER PRIMARY KEY,
    document_number         VARCHAR(20),
    purchase_order_id       INTEGER REFERENCES fact_purchase_order(purchase_order_id),  -- NULL for VMI/DSD
    supplier_invoice_number VARCHAR(30),      -- supplier's own number: NOT globally unique
    vendor_id               INTEGER REFERENCES dim_vendor(vendor_id),
    store_id                INTEGER REFERENCES dim_store(store_id),
    posting_date            DATE,
    date_id                 INTEGER REFERENCES dim_date(date_id),
    is_supplier_return      BOOLEAN,          -- true = goods going back (stock OUT)
    total_amount            NUMERIC(14,2)
);

CREATE TABLE fact_goods_receipt_line (
    gr_line_id        BIGINT PRIMARY KEY,
    goods_receipt_id  INTEGER REFERENCES fact_goods_receipt(goods_receipt_id),
    po_line_id        BIGINT REFERENCES fact_purchase_order_line(po_line_id),  -- NULL for VMI/DSD
    item_number       INTEGER REFERENCES dim_product(item_number),
    store_id          INTEGER REFERENCES dim_store(store_id),
    date_id           INTEGER REFERENCES dim_date(date_id),
    quantity_received INTEGER,
    amount            NUMERIC(12,2)
);

-- ------------------------------------------------------------
-- PROCESS 3 — TRANSFERRING (two stores per document)
-- ------------------------------------------------------------
CREATE TABLE fact_transfer (
    transfer_id     INTEGER PRIMARY KEY,
    transfer_number VARCHAR(20),
    from_store_id   INTEGER REFERENCES dim_store(store_id),
    to_store_id     INTEGER REFERENCES dim_store(store_id),
    shipment_date   DATE,
    posting_date    DATE,
    date_id         INTEGER REFERENCES dim_date(date_id),
    total_amount    NUMERIC(14,2)
);

CREATE TABLE fact_transfer_line (
    transfer_line_id  BIGINT PRIMARY KEY,
    transfer_id       INTEGER REFERENCES fact_transfer(transfer_id),
    item_number       INTEGER REFERENCES dim_product(item_number),
    date_id           INTEGER REFERENCES dim_date(date_id),
    quantity_shipped  INTEGER,
    quantity_received INTEGER,                -- shipped - received = in-transit loss
    amount            NUMERIC(12,2)
);

-- ------------------------------------------------------------
-- PROCESS 4 — CORRECTING (no document)
-- ------------------------------------------------------------
CREATE TABLE fact_adjustment (
    adjustment_id   INTEGER PRIMARY KEY,
    item_number     INTEGER REFERENCES dim_product(item_number),
    store_id        INTEGER REFERENCES dim_store(store_id),
    date_id         INTEGER REFERENCES dim_date(date_id),
    posting_date    DATE,
    adjustment_type VARCHAR(30),              -- shrinkage / stock_count / damage / expiry
    quantity        INTEGER,                  -- signed: negative = stock out, positive = stock in
    amount          NUMERIC(12,2),
    reason          VARCHAR(100)
);

-- ------------------------------------------------------------
-- INDEXES on the columns every analytical query joins or filters on
-- ------------------------------------------------------------
CREATE INDEX idx_rl_item_store  ON fact_receipt_line      (item_number, store_id);
CREATE INDEX idx_rl_date        ON fact_receipt_line      (date_id);
CREATE INDEX idx_grl_item_store ON fact_goods_receipt_line(item_number, store_id);
CREATE INDEX idx_grl_gr         ON fact_goods_receipt_line(goods_receipt_id);
CREATE INDEX idx_pol_po         ON fact_purchase_order_line(purchase_order_id);
CREATE INDEX idx_tl_transfer    ON fact_transfer_line     (transfer_id);
CREATE INDEX idx_adj_item_store ON fact_adjustment        (item_number, store_id);
CREATE INDEX idx_receipt_store  ON fact_receipt           (store_id, date_id);
