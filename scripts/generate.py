#!/usr/bin/env python3
# ============================================================
# Retail & Logistics Intelligence Dashboard — synthetic data generator
#
# GALAXY SCHEMA, one fact table per business process:
#   selling      -> Fact_Receipt       / Fact_ReceiptLine
#   ordering     -> Fact_PurchaseOrder / Fact_PurchaseOrderLine   (no stock effect)
#   receiving    -> Fact_GoodsReceipt  / Fact_GoodsReceiptLine    (stock changes here)
#   transferring -> Fact_Transfer      / Fact_TransferLine
#   correcting   -> Fact_Adjustment
#
# Deliberately plants: dead stock, phantom stock, negative inventory,
# seasonal dead stock, format demand / cannibalization, supplier lead
# time, fill-rate shortfalls, and in-transit transfer loss.
# ============================================================
import numpy as np
import pandas as pd
import datetime as dt
import argparse, os, gc

rng = np.random.default_rng(42)

PROFILES = {
    "sample": dict(n_items=300,   n_stores=3,  days=21,  base_receipts=40),
    "full":   dict(n_items=10000, n_stores=18, days=365, base_receipts=45),
}

# ------------------------------------------------------------
# REFERENCE DATA — curated Lebanese grocery (EN + AR)
# category -> (arabic_noun, [brands], [pack formats], season)
# ------------------------------------------------------------
CATALOG = {
    "Fresh Bread & Bakery": ("خبز",       ["Wooden Bakery","Pandor","TMC","Baalbaki","Ghandour"],      ["Small","Large","Family"],            "All-Year"),
    "Dairy & Eggs":         ("ألبان",     ["Taanayel","Dairy Khoury","Massaya","Liban Lait","Balade"], ["200g","500g","1kg","6pcs","12pcs"],  "All-Year"),
    "Beverages":            ("مشروبات",   ["Pepsi","7Up","Coca-Cola","Miranda","Buzz"],                ["330ml","500ml","1L","1.5L","2.25L"], "All-Year"),
    "Water":                ("مياه",      ["Sohat","Sannine","Tannourine","Rim","Nestle"],             ["500ml","1.5L","2L","6x1.5L"],        "Summer"),
    "Snacks & Confectionery":("حلويات",   ["Gandour","Master","Poppins","Unica","Tarboush"],           ["Single","Multipack","Family"],       "All-Year"),
    "Canned & Packaged":    ("معلبات",    ["Cortas","Chtaura","Al Wadi","California Garden","Hana"],   ["200g","400g","800g"],                "All-Year"),
    "Rice, Pasta & Grains": ("حبوب",      ["Abu Kass","Panzani","Barilla","Basmati Gold","Indomie"],   ["500g","1kg","5kg"],                  "All-Year"),
    "Cooking Oil & Ghee":   ("زيوت",      ["Crystal","Lesieur","Afia","Sultan","Zwan"],                ["500ml","1L","1.8L","4L"],            "All-Year"),
    "Cleaning & Household": ("تنظيف",     ["Diva","Ariel","Persil","Clorox","Pril"],                   ["500ml","1L","2L","4L"],              "All-Year"),
    "Personal Care":        ("عناية",     ["Sanita","Fine","Nivea","Head&Shoulders","Signal"],         ["Small","Medium","Large"],            "All-Year"),
    "Frozen Foods":         ("مجمدات",    ["Sea Sweet","Emborg","Sadia","Doux","Green Land"],          ["400g","800g","1kg"],                 "All-Year"),
    "Coffee & Tea":         ("قهوة وشاي", ["Najjar","Cafe Najjar","Lipton","Ahmad Tea","Nescafe"],     ["200g","450g","900g"],                "Winter"),
    "Ice Cream":            ("بوظة",      ["Bonjus","Cortina","Hawa Chicken","Unica","Gandour"],       ["Cup","500ml","1L","2L"],             "Summer"),
    "Soups & Winter":       ("شوربات",    ["Maggi","Knorr","Telma","Continental","Hana"],              ["Single","4pack","Family"],           "Winter"),
}

EN_NOUN = {
    "Fresh Bread & Bakery":"Bread","Dairy & Eggs":"Dairy","Beverages":"Soft Drink","Water":"Water",
    "Snacks & Confectionery":"Snack","Canned & Packaged":"Canned","Rice, Pasta & Grains":"Grains",
    "Cooking Oil & Ghee":"Oil","Cleaning & Household":"Detergent","Personal Care":"Care",
    "Frozen Foods":"Frozen","Coffee & Tea":"Coffee","Ice Cream":"Ice Cream","Soups & Winter":"Soup",
}

PRICE_BANDS = {
    "Fresh Bread & Bakery":(0.4,2.2), "Dairy & Eggs":(1.0,6.0), "Beverages":(0.4,2.6),
    "Water":(0.3,2.4), "Snacks & Confectionery":(0.3,3.0), "Canned & Packaged":(0.8,4.0),
    "Rice, Pasta & Grains":(0.8,9.0), "Cooking Oil & Ghee":(1.5,12.0), "Cleaning & Household":(1.0,8.0),
    "Personal Care":(1.0,7.0), "Frozen Foods":(2.0,9.0), "Coffee & Tea":(2.0,10.0),
    "Ice Cream":(0.5,6.0), "Soups & Winter":(0.4,3.0),
}

# categories typically delivered by the supplier's own van, with no prior PO
VMI_CATEGORIES = {"Fresh Bread & Bakery","Dairy & Eggs","Beverages"}

REGIONS = ["Beirut","Mount Lebanon","North","South","Bekaa","Nabatieh"]
STAFF_ROLES = ["Cashier","Senior Cashier","Shift Lead"]
FIRST = ["Ali","Rami","Nour","Maya","Hadi","Sara","Karim","Lea","Georges","Hassan",
         "Rana","Elie","Jad","Yara","Fadi","Dana","Marwan","Lynn","Tarek","Joelle"]
LAST  = ["Khoury","Haddad","Nassar","Aoun","Saad","Fares","Rizk","Chamoun","Ghanem","Sleiman"]

ADJ_TYPES = ["shrinkage","stock_count","damage","expiry"]
ADJ_REASON = {"shrinkage":"Unexplained loss","stock_count":"Physical count correction",
              "damage":"Damaged in store","expiry":"Expired - removed from shelf"}


# ============================================================
def build_dimensions(P):
    # ---- Vendors: one per brand, each with a supply model + lead time ----
    brand_cats = {}
    for cat,(_,bs,_,_) in CATALOG.items():
        for b in bs: brand_cats.setdefault(b,set()).add(cat)
    brands = sorted(brand_cats)
    rows=[]
    for i,b in enumerate(brands, start=1):
        # brands selling mostly VMI categories deliver by van, without a PO
        vmi = len(brand_cats[b] & VMI_CATEGORIES) >= max(1, len(brand_cats[b])/2)
        if vmi:
            model = str(rng.choice(["vendor_managed","direct_store_delivery"]))
            lead  = 0
        else:
            model = "ordered"
            lead  = int(rng.integers(3,22))          # days between order and delivery
        rows.append((i, f"{b} Distribution s.a.l.", model, lead))
    vendors = pd.DataFrame(rows, columns=["VendorID","VendorName","SupplyModel","_LeadDays"])
    brand_to_vendor = dict(zip(brands, vendors.VendorID))

    # ---- Stores ----
    n = P["n_stores"]
    sizes = rng.choice(["Large","Medium","Small"], size=n, p=[0.28,0.39,0.33])
    stype = np.where(sizes=="Large","Hypermarket", np.where(sizes=="Medium","Supermarket","Express"))
    stores = pd.DataFrame({
        "StoreID": range(1,n+1),
        "StoreCode":[f"BR{100+i}" for i in range(n)],
        "StoreName":[f"{REGIONS[i%len(REGIONS)]} {i+1}" for i in range(n)],
        "StoreType":stype, "StoreSize":sizes,
        "Region":[REGIONS[i%len(REGIONS)] for i in range(n)],
    })

    # ---- Staff: cashiers per store, plus a small buying team ----
    rows=[]; sid=1
    for st in stores.StoreID:
        for _ in range(int(rng.integers(3,6))):
            rows.append((sid,f"{rng.choice(FIRST)} {rng.choice(LAST)}",int(st),
                         str(rng.choice(STAFF_ROLES,p=[0.6,0.3,0.1])))); sid+=1
    buyers=[]
    for _ in range(6):                                   # head-office buyers
        rows.append((sid,f"{rng.choice(FIRST)} {rng.choice(LAST)}",1,"Buyer"))
        buyers.append(sid); sid+=1
    staff = pd.DataFrame(rows,columns=["StaffID","StaffName","StoreID","Role"])

    # ---- Dates ----
    start = dt.date(2025,9,1)
    dates = [start+dt.timedelta(days=i) for i in range(P["days"])]
    dim_date = pd.DataFrame({"DateID":[int(d.strftime("%Y%m%d")) for d in dates],
                             "Date":[d.isoformat() for d in dates],
                             "Month":[d.month for d in dates],
                             "Quarter":[(d.month-1)//3+1 for d in dates],
                             "Year":[d.year for d in dates]})
    return vendors, brand_to_vendor, stores, staff, buyers, dim_date, dates


# ============================================================
def build_products(P, brand_to_vendor):
    cats=list(CATALOG.keys())
    format_rule={c:int(rng.choice([1,-1,0])) for c in cats}   # +1 large-favoured, -1 small, 0 neutral
    rows=[]
    for item in range(1,P["n_items"]+1):
        cat=str(rng.choice(cats)); ar,brands,formats,season=CATALOG[cat]
        brand=str(rng.choice(brands)); fmt=str(rng.choice(formats))
        lo,hi=PRICE_BANDS[cat]
        frac=formats.index(fmt)/(len(formats)-1) if len(formats)>1 else 0.5
        price=float(np.round((lo+(hi-lo)*(0.3+0.7*frac))*rng.uniform(0.9,1.1),2))
        cost =float(np.round(price*rng.uniform(0.72,0.88),2))
        rows.append(dict(ItemNumber=item, Barcode=f"52810{item:07d}",
            DescriptionEN=f"{brand} {EN_NOUN[cat]} {fmt}", DescriptionAR=f"{ar} {brand} {fmt}",
            Brand=brand, Category=cat, PackSize=fmt, UOM="each",
            VendorID=brand_to_vendor[brand], UnitCost=cost, UnitPrice=price, Season=season,
            _fmt_idx=formats.index(fmt), _n_fmt=len(formats), _rule=format_rule[cat]))
    df=pd.DataFrame(rows)

    tier=rng.choice(["fast","normal","slow","dead"],size=len(df),p=[0.08,0.34,0.38,0.20])
    df["_tier"]=tier
    w=df._tier.map({"fast":100.0,"normal":25.0,"slow":4.0,"dead":0.05}).to_numpy()

    # format demand varies by category; mid sizes sometimes cannibalized by the large one
    pos=df._fmt_idx.to_numpy(); nf=df._n_fmt.to_numpy(); rule=df._rule.to_numpy()
    frac=np.where(nf>1,pos/np.maximum(nf-1,1),0.5)
    fmul=np.where(rule==1,0.5+frac,np.where(rule==-1,1.5-frac,1.0))
    mid=(pos>0)&(pos<nf-1); cannib=mid&(rng.random(len(df))<0.35)
    fmul=np.where(cannib,fmul*0.25,fmul)
    w=w*fmul

    normalish=np.isin(df._tier,["fast","normal"])
    phantom=normalish&(rng.random(len(df))<0.05)     # looks sellable, shelf is empty
    df["_range_w"]=w              # ranging uses PRE-phantom demand: to the buyer a
                                  # phantom item still looks like a normal seller
    w=np.where(phantom,0.0,w)

    df["_weight"]=w; df["_phantom"]=phantom
    df["_neg_inv"]=rng.random(len(df))<0.04
    df["_cannib"]=cannib
    return df


def season_factor(season, month):
    summer={5,6,7,8,9}; winter={11,12,1,2,3}
    if season=="Summer": return 1.6 if month in summer else 0.15
    if season=="Winter": return 1.6 if month in winter else 0.15
    return 1.0


# ============================================================
def generate(P, outdir):
    os.makedirs(outdir, exist_ok=True)
    W = lambda df,name: df.to_csv(os.path.join(outdir,name), index=False)

    vendors, b2v, stores, staff, buyers, dim_date, dates = build_dimensions(P)
    prod = build_products(P, b2v)

    store_ids   = stores.StoreID.to_numpy()
    store_size  = dict(zip(stores.StoreID, stores.StoreSize))
    price       = dict(zip(prod.ItemNumber, prod.UnitPrice))
    cost        = dict(zip(prod.ItemNumber, prod.UnitCost))
    weight      = dict(zip(prod.ItemNumber, prod._weight))
    season      = dict(zip(prod.ItemNumber, prod.Season))
    tier_of     = dict(zip(prod.ItemNumber, prod._tier))
    vendor_of   = dict(zip(prod.ItemNumber, prod.VendorID))
    negflag     = dict(zip(prod.ItemNumber, prod._neg_inv))
    phantom_of  = dict(zip(prod.ItemNumber, prod._phantom))
    v_model     = dict(zip(vendors.VendorID, vendors.SupplyModel))
    v_lead      = dict(zip(vendors.VendorID, vendors._LeadDays))

    # each item is carried by a subset of stores (better sellers are ranged wider)
    n_store=P["n_stores"]
    carry=np.clip((prod._range_w.rank(pct=True)*n_store).round().astype(int),1,n_store)
    item_stores={int(i):rng.choice(store_ids,size=int(k),replace=False)
                 for i,k in zip(prod.ItemNumber,carry)}
    store_items={int(s):np.array([i for i,ss in item_stores.items() if s in ss]) for s in store_ids}
    staff_by_store={int(s):staff[(staff.StoreID==s)&(staff.Role!="Buyer")].StaffID.to_numpy() for s in store_ids}

    # =========================================================
    # PROCESS 1 — SELLING
    # =========================================================
    basket_mu={"Large":7.0,"Medium":5.0,"Small":3.5}
    recv_mu  ={"Large":P["base_receipts"]*3,"Medium":P["base_receipts"]*2,"Small":P["base_receipts"]}
    _seq = {}
    def docnum(prefix, d):
        k = (prefix, d.year)
        _seq[k] = _seq.get(k, 0) + 1
        return f"{prefix}-{d.year % 100:02d}{_seq[k]:08d}"

    _rseq = {}
    def receiptnum(store, d):
        k = (store, d)
        _rseq[k] = _rseq.get(k, 0) + 1
        return f"{store:02d}-{d.strftime('%y%m%d')}-{_rseq[k]:04d}"

    receipts=[]; rlines=[]; rid=1; rlid=1; pcache={}
    for d in dates:
        did=int(d.strftime("%Y%m%d")); mo=d.month
        wk=1.0 if d.weekday()<5 else 1.35
        for s in store_ids:
            items=store_items[int(s)]
            if len(items)==0: continue
            key=(int(s),mo)
            if key not in pcache:
                wv=np.array([weight[i]*season_factor(season[i],mo) for i in items])
                pcache[key]=None if wv.sum()<=0 else wv/wv.sum()
            probs=pcache[key]
            if probs is None: continue
            sids=staff_by_store[int(s)]
            for _ in range(max(1,int(rng.poisson(recv_mu[store_size[s]]*wk)))):
                nb=max(1,int(rng.poisson(basket_mu[store_size[s]])))
                picks=rng.choice(items,size=nb,p=probs)
                hh=int(np.clip(rng.normal(15,3),8,22)); mm=int(rng.integers(0,60))
                ts=dt.datetime(d.year,d.month,d.day,hh,mm)
                tot=0.0; cnt=0
                for it in np.unique(picks):
                    q=int((picks==it).sum())
                    if rng.random()<0.01: q=-int(rng.integers(1,3))   # customer return
                    amt=round(q*price[int(it)],2)
                    rlines.append((rlid,rid,int(it),int(s),did,q,amt)); rlid+=1
                    tot+=amt; cnt+=q
                receipts.append((rid,receiptnum(int(s),d),int(s),did,ts.isoformat(sep=' '),int(rng.choice(sids)),
                                 round(tot,2),cnt)); rid+=1

    W(pd.DataFrame(receipts,columns=["ReceiptID","ReceiptNumber","StoreID","DateID","ReceiptDatetime",
                                     "StaffID","TotalAmount","TotalItems"]),"fact_receipt.csv")
    n_receipts=len(receipts); del receipts; gc.collect()

    net_out={}                       # units leaving stock through the till
    for (_l,_r,it,s,_d,q,_a) in rlines: net_out[(it,s)]=net_out.get((it,s),0)+q
    W(pd.DataFrame(rlines,columns=["ReceiptLineID","ReceiptID","ItemNumber","StoreID",
                                   "DateID","Quantity","Amount"]),"fact_receiptline.csv")
    n_rlines=len(rlines); del rlines; gc.collect()

    # =========================================================
    # PROCESS 4 — TRANSFERRING  (two stores; shipped vs received = in-transit loss)
    # =========================================================
    transfers=[]; tlines=[]; tid=1; tlid=1
    tr_delta={}                                        # (item,store) -> net units from transfers
    n_transfers=int(len(dates)*P["n_stores"]*0.8)
    movable=[i for i in item_stores if len(item_stores[i])>1]
    for _ in range(n_transfers):
        if not movable: break
        d=dates[int(rng.integers(0,len(dates)))]
        ship=d; post=d+dt.timedelta(days=int(rng.integers(0,3)))
        if post>dates[-1]: post=dates[-1]
        f,t=rng.choice(store_ids,size=2,replace=False)
        cand=[i for i in rng.choice(movable,size=min(8,len(movable)),replace=False)
              if f in item_stores[i] and t in item_stores[i]]
        if not cand: continue
        tot=0.0
        for it in cand:
            sh=int(rng.integers(2,40))
            rc=sh if rng.random()>0.08 else max(0,sh-int(rng.integers(1,4)))   # in-transit loss
            amt=round(sh*cost[int(it)],2); tot+=amt
            tlines.append((tlid,tid,int(it),int(post.strftime("%Y%m%d")),sh,rc,amt)); tlid+=1
            tr_delta[(int(it),int(f))]=tr_delta.get((int(it),int(f)),0)-sh
            tr_delta[(int(it),int(t))]=tr_delta.get((int(it),int(t)),0)+rc
        transfers.append((tid,docnum("TO",post),int(f),int(t),ship.isoformat(),post.isoformat(),
                          int(post.strftime("%Y%m%d")),round(tot,2))); tid+=1
    W(pd.DataFrame(transfers,columns=["TransferID","TransferNumber","FromStoreID","ToStoreID",
        "ShipmentDate","PostingDate","DateID","TotalAmount"]),"fact_transfer.csv")
    W(pd.DataFrame(tlines,columns=["TransferLineID","TransferID","ItemNumber","DateID",
        "QuantityShipped","QuantityReceived","Amount"]),"fact_transferline.csv")
    n_tr=len(transfers); n_trl=len(tlines); del transfers,tlines; gc.collect()

    # =========================================================
    # PROCESS 5 — CORRECTING  (shrinkage / counts / damage / expiry)
    # =========================================================
    adjustments=[]; aid=1; adj_out={}
    for it,ss in item_stores.items():
        for s in ss:
            if rng.random()<0.30:
                d=dates[int(rng.integers(0,len(dates)))]
                q=int(rng.integers(1,6)); at=str(rng.choice(ADJ_TYPES,p=[0.45,0.25,0.15,0.15]))
                qs = q if (at=="stock_count" and rng.random()<0.40) else -q
                adjustments.append((aid,int(it),int(s),int(d.strftime("%Y%m%d")),d.isoformat(),
                                    at,qs,round(qs*cost[int(it)],2),ADJ_REASON[at])); aid+=1
                adj_out[(int(it),int(s))]=adj_out.get((int(it),int(s)),0)-qs

    # =========================================================
    # PROCESS 2 — ORDERING + RECEIVING
    # Orders are grouped per (vendor, store, cycle), as a real PO is.
    # VMI / direct-store-delivery vendors arrive with NO purchase order.
    # =========================================================
    cycles=dates[::30]
    vs_items={}
    for it,ss in item_stores.items():
        v=int(vendor_of[it])
        for s in ss: vs_items.setdefault((v,int(s)),[]).append(int(it))

    # target intake per item-store: cover what leaves, plus ~1 month of closing cover
    need={}
    for it,ss in item_stores.items():
        for s in ss:
            k=(int(it),int(s))
            out=net_out.get(k,0)+adj_out.get(k,0)-tr_delta.get(k,0)
            if out>0:
                # cover what leaves, plus closing cover; buffer absorbs supplier shortfall
                need[k]=out+max(10.0,out*0.15)
            elif phantom_of[int(it)]:
                # PHANTOM: system stock looks healthy, so the buyer keeps ordering
                # normally — stock piles up while the shelf stays empty.
                need[k]=float(rng.integers(60,250))
            else:
                # dead: the buy that went wrong — ordered once, never sold again
                need[k]=float(rng.integers(8,45)) if rng.random()<0.85 else 0.0

    pos=[]; polines=[]; grs=[]; grlines=[]; poline_of={}
    poid=1; polid=1; grid_=1; grlid=1
    for (v,s),items in vs_items.items():
        model=v_model[v]; lead=int(v_lead[v]); ordered_model=(model=="ordered")
        for ci,d0 in enumerate(cycles):
            lines=[(it,need[(it,s)]/len(cycles)) for it in items if need.get((it,s),0)>0]
            # dead items are bought only in the first cycle or two, then never again
            lines=[(it,q) for it,q in lines
                   if net_out.get((it,s),0)>0 or ci<int(rng.integers(1,3))]
            if not lines: continue
            if not ordered_model:
                lines=[(it,q*len(cycles)/len(cycles)) for it,q in lines]

            po_id=None; po_no=None
            if ordered_model:
                tot=0.0; po_id=poid; po_no=docnum("PO",d0)
                for it,q in lines:
                    qo=int(max(1,rng.normal(q/0.85,max(1.0,q*0.15))))   # order allowing for shortfall
                    amt=round(qo*cost[it],2); tot+=amt
                    polines.append((polid,po_id,it,int(s),int(d0.strftime("%Y%m%d")),qo,amt))
                    poline_of[(po_id,it)]=polid; polid+=1
                pos.append((po_id,po_no,v,int(s),int(rng.choice(buyers)),
                            d0.isoformat(),int(d0.strftime("%Y%m%d")),round(tot,2))); poid+=1

            # ---- delivery/ies ----
            post=d0+dt.timedelta(days=lead) if ordered_model else d0
            if post>dates[-1]: post=dates[-1]
            splits=[1.0] if rng.random()>0.18 else [0.6,0.4]            # partial delivery
            for si,share in enumerate(splits):
                pd_=post+dt.timedelta(days=0 if si==0 else int(rng.integers(2,10)))
                if pd_>dates[-1]: pd_=dates[-1]
                tot=0.0; gr=grid_
                for it,q in lines:
                    fill=1.0 if rng.random()>0.15 else rng.uniform(0.70,0.95)  # supplier shortfall
                    qr=int(max(0,round(q*share*fill)))
                    if qr<=0: continue
                    amt=round(qr*cost[it],2); tot+=amt
                    grlines.append((grlid,gr,poline_of.get((po_id,it)),it,int(s),
                                    int(pd_.strftime("%Y%m%d")),qr,amt))
                    grlid+=1
                if tot<=0: continue
                gr_no = f"{po_no}-{si+1:02d}" if po_no else docnum("DD",pd_)
                grs.append((gr,gr_no,po_id,
                            f"INV-{int(rng.integers(1000,99999))}",v,int(s),
                            pd_.isoformat(),int(pd_.strftime("%Y%m%d")),False,round(tot,2)))
                grid_+=1

    # supplier returns: a small share of deliveries sent back
    for _ in range(max(10,int(grid_*0.02))):
        v=int(rng.choice(vendors.VendorID)); s=int(rng.choice(store_ids))
        items=vs_items.get((v,s))
        if not items: continue
        d=dates[int(rng.integers(0,len(dates)))]; tot=0.0; gr=grid_
        for it in rng.choice(items,size=min(3,len(items)),replace=False):
            q=int(rng.integers(1,10)); amt=round(q*cost[int(it)],2); tot+=amt
            grlines.append((grlid,gr,None,int(it),s,int(d.strftime("%Y%m%d")),q,amt)); grlid+=1
        grs.append((gr,docnum("RO",d),None,f"CN-{int(rng.integers(1000,99999))}",v,s,
                    d.isoformat(),int(d.strftime("%Y%m%d")),True,round(tot,2))); grid_+=1

    # ---- received totals, for the negative-inventory injection ----
    recv_tot={}
    for (_i,_g,_p,it,s,_d,qr,_a) in grlines: recv_tot[(it,s)]=recv_tot.get((it,s),0)+qr
    ret_tot={}
    ret_ids={g[0] for g in grs if g[8]}
    for (_i,g,_p,it,s,_d,qr,_a) in grlines:
        if g in ret_ids: ret_tot[(it,s)]=ret_tot.get((it,s),0)+qr

    # =========================================================
    # NEGATIVE INVENTORY — sized against the ACTUAL closing balance
    # =========================================================
    for it,ss in item_stores.items():
        if not negflag[it]: continue
        for s in ss:
            if rng.random()>=0.5: continue
            k=(int(it),int(s))
            closing=(recv_tot.get(k,0)-2*ret_tot.get(k,0)+tr_delta.get(k,0)
                     -net_out.get(k,0)-adj_out.get(k,0))
            q=int(closing+int(rng.integers(1,40)))
            if q<=0: continue
            d=dates[int(len(dates)*0.75)]
            adjustments.append((aid,int(it),int(s),int(d.strftime("%Y%m%d")),d.isoformat(),
                                "stock_count",-q,0.0,"Physical count correction")); aid+=1
            adj_out[k]=adj_out.get(k,0)+q

    # =========================================================
    # WRITE
    # =========================================================
    W(vendors.drop(columns=["_LeadDays"]),"dim_vendor.csv")
    W(stores,"dim_store.csv"); W(staff,"dim_staff.csv"); W(dim_date,"dim_date.csv")
    W(prod[[c for c in prod.columns if not c.startswith('_')]],"dim_product.csv")
    W(pd.DataFrame(pos,columns=["PurchaseOrderID","PONumber","VendorID","StoreID","BuyerStaffID",
        "OrderDate","DateID","TotalAmount"]),"fact_purchaseorder.csv")
    W(pd.DataFrame(polines,columns=["POLineID","PurchaseOrderID","ItemNumber","StoreID","DateID",
        "QuantityOrdered","Amount"]),"fact_purchaseorderline.csv")
    # nullable FKs must stay integers: plain float columns would emit "1.0",
    # which PostgreSQL rejects for an INTEGER column.
    _gr=pd.DataFrame(grs,columns=["GoodsReceiptID","DocumentNumber","PurchaseOrderID","SupplierInvoiceNumber",
        "VendorID","StoreID","PostingDate","DateID","IsSupplierReturn","TotalAmount"])
    _gr["PurchaseOrderID"]=_gr.PurchaseOrderID.astype("Int64")
    W(_gr,"fact_goodsreceipt.csv")
    _grl=pd.DataFrame(grlines,columns=["GRLineID","GoodsReceiptID","POLineID","ItemNumber","StoreID",
        "DateID","QuantityReceived","Amount"])
    _grl["POLineID"]=_grl.POLineID.astype("Int64")
    W(_grl,"fact_goodsreceiptline.csv")
    W(pd.DataFrame(adjustments,columns=["AdjustmentID","ItemNumber","StoreID","DateID","PostingDate",
        "AdjustmentType","Quantity","Amount","Reason"]),"fact_adjustment.csv")

    print(f"PROFILE items={P['n_items']} stores={P['n_stores']} days={P['days']}")
    print(f"  receipts            : {n_receipts:,}")
    print(f"  receipt lines       : {n_rlines:,}")
    print(f"  purchase orders     : {len(pos):,}   PO lines: {len(polines):,}")
    print(f"  goods receipts      : {len(grs):,}   GR lines: {len(grlines):,}")
    print(f"  transfers           : {n_tr:,}   transfer lines: {n_trl:,}")
    print(f"  adjustments         : {len(adjustments):,}")
    print(f"  VMI/DSD vendors     : {(vendors.SupplyModel!='ordered').sum()} of {len(vendors)}")
    print(f"  phantom items       : {int(prod._phantom.sum()):,}")
    print(f"  neg-inv flagged     : {int(prod._neg_inv.sum()):,}")
    print(f"  cannibalized        : {int(prod._cannib.sum()):,}")


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--profile",default="sample",choices=list(PROFILES))
    ap.add_argument("--outdir",default=None)
    a=ap.parse_args()
    generate(PROFILES[a.profile], a.outdir or f"data_{a.profile}")
