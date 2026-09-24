#!/usr/bin/env python3
"""Render an ER diagram straight from the live PostgreSQL schema,
so the picture can never drift from the database."""
import subprocess, textwrap, html

DB = "retail_intelligence"

def q(sql):
    out = subprocess.run(["su","postgres","-c",
            f'psql -d {DB} -At -F"|" -c "{sql}"'],
            capture_output=True, text=True)
    return [l.split("|") for l in out.stdout.strip().splitlines() if l]

cols = q("""SELECT table_name, column_name, data_type, ordinal_position
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name NOT LIKE 'vw_%'
            ORDER BY table_name, ordinal_position""")

pks = {(t,c) for t,c in q("""
    SELECT tc.table_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name=tc.constraint_name
    WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public'""")}

fks_raw = q("""
    SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name=tc.constraint_name
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name=tc.constraint_name
    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'""")
fk_cols = {(t,c) for t,c,_,_ in fks_raw}

SHORT = {"integer":"int","bigint":"bigint","character varying":"varchar",
         "numeric":"decimal","timestamp without time zone":"datetime",
         "date":"date","boolean":"bool","smallint":"smallint"}

# process grouping -> header colour
PROCESS = {
    "dim_date":"dim","dim_store":"dim","dim_product":"dim","dim_vendor":"dim","dim_staff":"dim",
    "fact_receipt":"sell","fact_receipt_line":"sell",
    "fact_purchase_order":"order","fact_purchase_order_line":"order",
    "fact_goods_receipt":"recv","fact_goods_receipt_line":"recv",
    "fact_transfer":"move","fact_transfer_line":"move",
    "fact_adjustment":"fix",
}
COLOR = {"dim":"#3F5C78","sell":"#2E7D6F","order":"#8A6A3D","recv":"#7A4B63",
         "move":"#4A5A8A","fix":"#6B5B7B"}
LABEL = {"dim":"CONFORMED DIMENSION","sell":"SELLING","order":"ORDERING (no stock effect)",
         "recv":"RECEIVING (stock moves here)","move":"TRANSFERRING","fix":"CORRECTING"}

by_table = {}
for t,c,d,_ in cols: by_table.setdefault(t,[]).append((c,d))

def node(t):
    grp = PROCESS.get(t,"dim"); col = COLOR[grp]
    rows = []
    for c,d in by_table[t]:
        key = "PK" if (t,c) in pks else ("FK" if (t,c) in fk_cols else "")
        kb  = f'<FONT COLOR="#C9A227"><B>{key}</B></FONT>' if key=="PK" else \
              (f'<FONT COLOR="#8FA8C8">{key}</FONT>' if key else "")
        name = f"<B>{html.escape(c)}</B>" if key=="PK" else html.escape(c)
        kcell = (f'<TD ALIGN="RIGHT" BGCOLOR="#FBFBFD"><FONT POINT-SIZE="8">{kb}</FONT></TD>'
                 if kb else '<TD ALIGN="RIGHT" BGCOLOR="#FBFBFD"> </TD>')
        rows.append(
            f'<TR><TD ALIGN="LEFT" PORT="{c}" BGCOLOR="#FBFBFD">'
            f'<FONT POINT-SIZE="10">{name}</FONT></TD>'
            f'<TD ALIGN="LEFT" BGCOLOR="#FBFBFD">'
            f'<FONT POINT-SIZE="9" COLOR="#7A7A8C">{SHORT.get(d,d)}</FONT></TD>'
            + kcell + '</TR>')
    return (f'{t} [label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" '
            f'CELLPADDING="4" BGCOLOR="#FFFFFF">'
            f'<TR><TD COLSPAN="3" BGCOLOR="{col}">'
            f'<FONT COLOR="#FFFFFF" POINT-SIZE="13"><B>{t}</B></FONT></TD></TR>'
            + "".join(rows) + '</TABLE>>];')

GROUPS = [
    ("cluster_sell",  "SELLING",                    "sell",  ["fact_receipt","fact_receipt_line"]),
    ("cluster_order", "ORDERING  (no stock effect)","order", ["fact_purchase_order","fact_purchase_order_line"]),
    ("cluster_recv",  "RECEIVING  (stock moves here)","recv",["fact_goods_receipt","fact_goods_receipt_line"]),
    ("cluster_move",  "TRANSFERRING",               "move",  ["fact_transfer","fact_transfer_line"]),
    ("cluster_fix",   "CORRECTING",                 "fix",   ["fact_adjustment"]),
]
DIMS = ["dim_product","dim_store","dim_date","dim_vendor","dim_staff"]

def cluster(cid, title, key, tables, fill):
    inner = "\n".join("    " + node(t) for t in tables)
    return (f'  subgraph {cid} {{\n'
            f'    label=<<FONT POINT-SIZE="15" COLOR="{COLOR[key]}"><B>{title}</B></FONT>>;\n'
            f'    labeljust=l; style="rounded,filled"; fillcolor="{fill}";\n'
            f'    color="{COLOR[key]}"; penwidth=1.4; margin=16;\n{inner}\n  }}')

fact_clusters = "\n".join(
    cluster(cid, title, key, tables, "#FFFFFF") for cid,title,key,tables in GROUPS)
dim_cluster = cluster("cluster_dims","CONFORMED DIMENSIONS  (shared by every process)","dim",DIMS,"#FFFFFF")

dot = f"""digraph schema {{
  graph [rankdir=LR, splines=spline, newrank=true, compound=true,
         nodesep=0.40, ranksep=3.2, bgcolor="#F4F4F7", fontname="Helvetica",
         pad=0.5, concentrate=false,
         label=<<FONT POINT-SIZE="26"><B>Retail &amp; Logistics Intelligence — Galaxy Schema</B></FONT><BR/>
                <FONT POINT-SIZE="14" COLOR="#5A5A6E">One fact table per business process, sharing conformed dimensions.<BR ALIGN="CENTER"/>Stock is derived from movements, never stored. Sales are stored once, at receipt grain.</FONT><BR/> >,
         labelloc=t, fontsize=14];
  node [shape=plaintext, fontname="Helvetica"];
  edge [color="#A8AEC2", arrowsize=0.6, penwidth=0.9];

{fact_clusters}

{dim_cluster}

{chr(10).join(f'  {s_}:{sc} -> {tt}:{tc};' for s_,sc,tt,tc in fks_raw)}
}}
"""
open("/home/claude/schema.dot","w").write(dot)
for fmt in ("png","svg"):
    r=subprocess.run(["dot",f"-T{fmt}","-Gdpi=150","/home/claude/schema.dot",
                      "-o",f"/home/claude/schema_diagram.{fmt}"],capture_output=True,text=True)
    print(fmt, "OK" if r.returncode==0 else r.stderr[:300])
print(f"tables={len(by_table)} foreign_keys={len(fks_raw)}")

# ============================================================
# Also emit DBML for dbdiagram.io, generated from the SAME live
# schema, so the two renderings can never disagree.
# ============================================================
DBML_TYPE = {"integer":"int","bigint":"bigint","character varying":"varchar",
             "numeric":"decimal","timestamp without time zone":"datetime",
             "date":"date","boolean":"boolean","smallint":"smallint"}

TABLE_NOTE = {
 "dim_product":"Conformed dimension. Brand, category, pack_size and vendor are four independent attributes, not hierarchy levels.",
 "dim_store":"Conformed dimension shared by every process.",
 "dim_date":"Conformed date dimension, keyed yyyymmdd.",
 "dim_vendor":"supply_model marks VMI / direct-store-delivery vendors, which deliver with no prior PO. Exclude them from lead-time analysis.",
 "dim_staff":"Cashiers and head-office buyers.",
 "fact_receipt":"SELLING. Transaction header. total_amount and total_items are the basket measures.",
 "fact_receipt_line":"SELLING. Source of truth for sales and customer returns. Positive quantity = sale, negative = customer return. Daily sales are a VIEW over this, never a stored table.",
 "fact_purchase_order":"ORDERING. Raising a PO does NOT move stock. Excluded from vw_stock_movement.",
 "fact_purchase_order_line":"ORDERING. quantity_ordered only. Compare against goods received for fill rate.",
 "fact_goods_receipt":"RECEIVING. Stock changes HERE, not at ordering. purchase_order_id is nullable for VMI / direct delivery. The supplier invoice belongs to the delivery, not the order.",
 "fact_goods_receipt_line":"RECEIVING. One PO line can have several of these (partial delivery). Aggregate before joining or fill rate fans out.",
 "fact_transfer":"TRANSFERRING. Two stores per document. shipment_date vs posting_date.",
 "fact_transfer_line":"TRANSFERRING. quantity_shipped - quantity_received = in-transit loss. Becomes TWO rows in vw_stock_movement: OUT at origin, IN at destination.",
 "fact_adjustment":"CORRECTING. Shrinkage, stock counts, damage, expiry. No document, so no header/line split.",
}
GROUP_OF = {"dim":"conformed_dimensions","sell":"selling","order":"ordering_no_stock_effect",
            "recv":"receiving_stock_moves_here","move":"transferring","fix":"correcting"}

fk_target = {(t,c):(tt,tc) for t,c,tt,tc in fks_raw}
lines = ["// ============================================================",
         "// Retail & Logistics Intelligence Dashboard",
         "// GALAXY SCHEMA - one fact table per business process,",
         "// sharing conformed dimensions.",
         "//",
         "// Generated from the live PostgreSQL schema by make_diagram.py,",
         "// so this file can never drift from the real database.",
         "// ============================================================",""]
for t in sorted(by_table):
    lines.append(f"Table {t} {{")
    for c,d in by_table[t]:
        attrs=[]
        if (t,c) in pks: attrs.append("pk")
        if (t,c) in fk_target:
            tt,tc = fk_target[(t,c)]; attrs.append(f"ref: > {tt}.{tc}")
        a = f" [{', '.join(attrs)}]" if attrs else ""
        lines.append(f"  {c} {DBML_TYPE.get(d,d)}{a}")
    if t in TABLE_NOTE:
        lines.append(f"  Note: '{TABLE_NOTE[t]}'")
    lines.append("}"); lines.append("")

groups={}
for t in by_table: groups.setdefault(GROUP_OF[PROCESS.get(t,"dim")],[]).append(t)
for g in ["conformed_dimensions","selling","ordering_no_stock_effect",
          "receiving_stock_moves_here","transferring","correcting"]:
    lines.append(f"TableGroup {g} {{")
    for t in sorted(groups.get(g,[])): lines.append(f"  {t}")
    lines.append("}"); lines.append("")

open("/home/claude/schema.dbml","w").write("\n".join(lines))
print(f"schema.dbml regenerated from live DB: {len(by_table)} tables, {len(fks_raw)} refs")
