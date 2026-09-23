# Retail & Logistics Intelligence Dashboard

A dead-stock diagnostic for a multi-branch discount grocery chain: 18 stores,
10,000 items, one year of trading. Built end to end — data model, synthetic
data generation, PostgreSQL analytical layer, Power BI dashboard.

## The problem

The business already has a 90-day zero-sales report. It identifies unsold items
but doesn't measure the financial impact, explain the cause, or guide action —
so it gets ignored and the problem persists.

This project turns that descriptive report into a **diagnostic** one: it
quantifies the trapped capital, separates the root causes, and produces an
actionable watchlist per store.

**Headline finding:** most dead stock is not dead because customers rejected it.
Of $662k in trapped capital, **54% is explained by fixable data, process and
timing failures** — phantom stock, negative inventory blocking replenishment,
and seasonal items misread as dead — rather than genuine lack of demand.

## Data model

A **galaxy schema** (fact constellation): one fact table per business process,
all sharing **conformed dimensions**.

| Process | Fact tables | Stock effect |
|---|---|---|
| Selling | `fact_receipt` / `fact_receipt_line` | out (sale), in (customer return) |
| Ordering | `fact_purchase_order` / `fact_purchase_order_line` | **none** — a PO doesn't move stock |
| Receiving | `fact_goods_receipt` / `fact_goods_receipt_line` | in (out if supplier return) |
| Transferring | `fact_transfer` / `fact_transfer_line` | out at origin, **in at destination** |
| Correcting | `fact_adjustment` | out (shrinkage, counts, damage, expiry) |

Conformed dimensions: `dim_product`, `dim_store`, `dim_date`, `dim_vendor`, `dim_staff`.

### Design decisions worth defending

**Stock is never stored — it is derived.** There is no "current stock" column
anywhere. Stock is reconstructed as a **running balance** over the movement
ledger using a window function. `vw_daily_stock_balance` is validated to match
`vw_stock_on_hand` exactly across all 89,478 item-store pairs.

**Sales are stored once, at receipt grain.** They are never duplicated into a
movement table. `vw_stock_movement` UNIONs the four stock-affecting processes
into one complete ledger, so the two grains can never disagree.

**Ordering is separated from receiving.** One purchase order can produce several
deliveries (partial delivery), each with its own supplier invoice. Keeping
`quantity_ordered` and `quantity_received` on the same row would silently assume
one order equals one delivery. Separating them makes **lead time**
(`posting_date − order_date`) and **fill rate** correct rather than approximate.

**A transfer carries two stores.** `from_store_id` and `to_store_id`, plus
`quantity_shipped` vs `quantity_received`. One transfer line becomes **two rows**
in the movement view — an OUT at the origin and an IN at the destination. This
also exposes **in-transit loss**, which a single-store movement row cannot express.

**Vendor supply models are distinguished.** Vendor-managed inventory and direct
store delivery suppliers set their own quantities and deliver with no prior PO.
Their order and delivery are the same event, so lead time is undefined — they are
explicitly excluded from lead-time analysis rather than silently averaged in.

**Negative stock is clamped to zero for valuation** (you cannot hold negative
capital on a shelf) and surfaced separately as its own watchlist, measured
against the company's threshold of 100 negative items per store.

## Contents

```
generate.py            synthetic data generator (pandas + numpy, seeded)
sql/01_schema.sql      DDL: 14 tables, keys, foreign keys, indexes
sql/02_load.sql        COPY load script
sql/03_views.sql       analytical layer (10 views)
schema.dbml            the model, for dbdiagram.io
```

## Running it

```bash
python3 generate.py --profile full        # ~2 min, writes ./data_full
createdb tawfeer
psql -d tawfeer -f sql/01_schema.sql
cd data_full && psql -d tawfeer -f ../sql/02_load.sql
psql -d tawfeer -f sql/03_views.sql
```

`--profile sample` generates a small dataset (3 stores, 300 items, 21 days) for
quick iteration.

## The data

Synthetic, generated with a fixed seed, using real Lebanese retail content
(brands, bilingual English/Arabic descriptions, discount-grocery price bands,
Lebanese regions). No real company data is used.

| Table | Rows |
|---|---|
| `fact_receipt_line` | 3,851,963 |
| `fact_goods_receipt_line` | 1,215,279 |
| `fact_purchase_order_line` | 841,268 |
| `fact_receipt` | 668,046 |
| `fact_adjustment` | 28,886 |
| `fact_goods_receipt` | 18,844 |
| `fact_transfer_line` | 14,353 |
| `fact_purchase_order` | 12,168 |
| `fact_transfer` | 5,078 |
| `dim_product` | 10,000 |

Five problems are deliberately planted so the diagnostic has something real to
find: genuine no-demand items, phantom stock, negative inventory, seasonal
items, and format-level cannibalization (a mid-size pack suppressed by the
large one). Supplier lead times, fill-rate shortfalls and in-transit transfer
losses are generated with realistic variation rather than fixed rules, so the
analysis discovers patterns rather than confirming planted ones.

## Key views

| View | Answers |
|---|---|
| `vw_stock_movement` | the unified movement ledger across all four processes |
| `vw_daily_stock_balance` | stock reconstructed day by day (running balance) |
| `vw_stock_on_hand` | closing stock per item / store |
| `vw_dead_stock_diagnostic` | trapped capital + root cause per dead item |
| `vw_negative_inventory` | per-store negative counts vs the 100-item threshold |
| `vw_supplier_performance` | lead time and fill rate, VMI excluded |
| `vw_transfer_loss` | stock that left one store and never arrived |
| `vw_basket_profile` | basket size by count and by value, per store |
| `vw_format_demand` | does pack size predict which items die? |
| `vw_daily_sales` | daily sales rolled up from receipt lines |

## Results

| Measure | Value |
|---|---|
| Total stock value | $3.6M |
| Trapped capital (90-day zero sales) | **$662k (18.4%)** |
| — genuine no demand | $305k (46%) |
| — out of season | $254k (38%) |
| — phantom stock | $103k (16%) |
| Negative inventory | 4,006 item-store pairs; **all 18 stores breach** the 100-item threshold |
| Supplier lead time (ordered vendors) | 12.9 days average |
| Supplier fill rate | 88.4% |
| In-transit transfer loss | 2,402 units / $8,175 |

## Next

Power BI dashboard over the view layer: trapped-capital overview, root-cause
breakdown, per-store negative-inventory watchlist, supplier scorecard, and
basket profile.
