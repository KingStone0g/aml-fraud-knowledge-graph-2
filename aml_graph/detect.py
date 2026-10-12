"""Graph-based AML / fraud detections that run offline with NetworkX.

These mirror the Cypher / Neo4j GDS queries in ``cypher/`` so the project can be
run and tested without a database. Each detector returns plain Python structures.

Detectors
---------
detect_rings              circular transfers inside a time window with similar amounts
detect_identity_clusters  customers linked by shared devices / addresses / phones
detect_structuring        repeated cash deposits just under the $10k threshold
detect_mule_hubs          accounts that collect from many senders and pass it on
communities / pagerank    network context used to prioritise investigations
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import networkx as nx

CASH_SOURCE = "EXT-CASH"

TABLES = ["customers", "accounts", "devices", "addresses", "phones",
          "customer_device", "customer_address", "customer_phone", "transactions"]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load_dataset(data_dir: str | Path) -> dict[str, list[dict]]:
    d = Path(data_dir)
    ds = {name: read_csv(d / f"{name}.csv") for name in TABLES}
    for t in ds["transactions"]:
        t["amount"] = float(t["amount"])
        t["ts_epoch"] = int(t["ts_epoch"])
    return ds


def load_truth(data_dir: str | Path) -> list[dict]:
    return read_csv(Path(data_dir) / "ground_truth.csv")


# ---------------------------------------------------------------------------
# 1. Circular transfers (layering)
# ---------------------------------------------------------------------------
def _find_chain(edge_lists, window, spread):
    """Find one transfer per edge, in time order, inside ``window`` seconds with
    amounts within ``spread`` (max/min). Returns [(ts, amount), ...] or None."""

    def search(i, prev_ts, start_ts, lo, hi, chain):
        if i == len(edge_lists):
            return chain
        for ts, amt in edge_lists[i]:
            if prev_ts < ts <= start_ts + window:
                nlo, nhi = min(lo, amt), max(hi, amt)
                if nhi / nlo <= spread:
                    found = search(i + 1, ts, start_ts, nlo, nhi, chain + [(ts, amt)])
                    if found:
                        return found
        return None

    for ts, amt in edge_lists[0]:
        found = search(1, ts, ts, amt, amt, [(ts, amt)])
        if found:
            return found
    return None


def detect_rings(txns, min_amount=1000.0, max_len=6, window_hours=72, max_spread=1.15):
    edges = defaultdict(list)
    for t in txns:
        if t["src_account"] == CASH_SOURCE or t["amount"] < min_amount:
            continue
        edges[(t["src_account"], t["dst_account"])].append((t["ts_epoch"], t["amount"]))
    graph = nx.DiGraph()
    graph.add_edges_from(edges)

    window = window_hours * 3600
    rings = []
    for cycle in nx.simple_cycles(graph, length_bound=max_len):
        k = len(cycle)
        if k < 3:
            continue
        for r in range(k):
            order = cycle[r:] + cycle[:r]
            edge_lists = [edges[(order[i], order[(i + 1) % k])] for i in range(k)]
            chain = _find_chain(edge_lists, window, max_spread)
            if chain:
                rings.append({
                    "accounts": order,
                    "hops": [{"src": order[i], "dst": order[(i + 1) % k], "ts_epoch": ts, "amount": amt}
                             for i, (ts, amt) in enumerate(chain)],
                    "duration_hours": round((chain[-1][0] - chain[0][0]) / 3600, 1),
                })
                break
    return rings


# ---------------------------------------------------------------------------
# 2. Shared identity clusters (mule networks)
# ---------------------------------------------------------------------------
def detect_identity_clusters(ds, min_customers=4):
    g = nx.Graph()
    for kind, table, key in (("D", "customer_device", "device_id"),
                             ("A", "customer_address", "address_id"),
                             ("P", "customer_phone", "phone_id")):
        for row in ds[table]:
            g.add_edge(("C", row["customer_id"]), (kind, row[key]))
    clusters = []
    for comp in nx.connected_components(g):
        customers = sorted(n[1] for n in comp if n[0] == "C")
        if len(customers) >= min_customers:
            clusters.append({
                "customers": customers,
                "devices": sorted(n[1] for n in comp if n[0] == "D"),
                "addresses": sorted(n[1] for n in comp if n[0] == "A"),
                "phones": sorted(n[1] for n in comp if n[0] == "P"),
            })
    clusters.sort(key=lambda c: -len(c["customers"]))
    return clusters


# ---------------------------------------------------------------------------
# 3. Structuring (smurfing)
# ---------------------------------------------------------------------------
def detect_structuring(txns, lo=9000.0, hi=10000.0, window_days=7, min_deposits=5):
    deposits = defaultdict(list)
    for t in txns:
        if t["src_account"] == CASH_SOURCE and lo <= t["amount"] < hi:
            deposits[t["dst_account"]].append((t["ts_epoch"], t["amount"]))
    window = window_days * 86400
    flagged = []
    for acct, rows in deposits.items():
        rows.sort()
        best = (0, 0.0)
        left = 0
        for right in range(len(rows)):
            while rows[right][0] - rows[left][0] > window:
                left += 1
            count = right - left + 1
            if count > best[0]:
                best = (count, sum(a for _, a in rows[left:right + 1]))
        if best[0] >= min_deposits:
            flagged.append({"account": acct, "deposits_in_window": best[0], "total": round(best[1], 2)})
    flagged.sort(key=lambda f: -f["deposits_in_window"])
    return flagged


# ---------------------------------------------------------------------------
# 4. Pass-through mule hubs
# ---------------------------------------------------------------------------
def detect_mule_hubs(txns, min_senders=10, min_pass_through=0.8, max_recipients=3):
    inflow = defaultdict(lambda: {"senders": set(), "amount": 0.0})
    outflow = defaultdict(lambda: {"recipients": set(), "amount": 0.0})
    for t in txns:
        if t["src_account"] == CASH_SOURCE:
            continue
        inflow[t["dst_account"]]["senders"].add(t["src_account"])
        inflow[t["dst_account"]]["amount"] += t["amount"]
        outflow[t["src_account"]]["recipients"].add(t["dst_account"])
        outflow[t["src_account"]]["amount"] += t["amount"]
    hubs = []
    for acct, i in inflow.items():
        if len(i["senders"]) < min_senders or acct not in outflow:
            continue
        o = outflow[acct]
        ratio = o["amount"] / i["amount"]
        if ratio >= min_pass_through and len(o["recipients"]) <= max_recipients:
            hubs.append({"account": acct, "senders": len(i["senders"]), "recipients": len(o["recipients"]),
                         "total_in": round(i["amount"], 2), "total_out": round(o["amount"], 2),
                         "pass_through": round(ratio, 2)})
    hubs.sort(key=lambda h: -h["senders"])
    return hubs


# ---------------------------------------------------------------------------
# 5. Network context: communities and centrality
# ---------------------------------------------------------------------------
def transfer_graph(txns):
    g = nx.DiGraph()
    for t in txns:
        if t["src_account"] == CASH_SOURCE:
            continue
        s, d = t["src_account"], t["dst_account"]
        if g.has_edge(s, d):
            g[s][d]["weight"] += t["amount"]
        else:
            g.add_edge(s, d, weight=t["amount"])
    return g


def communities(graph, seed=42):
    return [sorted(c) for c in nx.community.louvain_communities(graph.to_undirected(), weight="weight", seed=seed)]


def pagerank(graph):
    return nx.pagerank(graph, weight="weight")


# ---------------------------------------------------------------------------
# Scoring and evaluation
# ---------------------------------------------------------------------------
def risk_scores(ds, rings, clusters, structuring, hubs):
    owner = {a["account_id"]: a["customer_id"] for a in ds["accounts"]}
    accounts_of = defaultdict(list)
    for acct, cust in owner.items():
        accounts_of[cust].append(acct)
    rating = {c["customer_id"]: c["risk_rating"] for c in ds["customers"]}

    scores = defaultdict(lambda: {"score": 0, "reasons": []})

    def add(acct, points, reason):
        scores[acct]["score"] += points
        scores[acct]["reasons"].append(reason)

    for ring in rings:
        for acct in ring["accounts"]:
            add(acct, 50, "circular transfers")
    for row in structuring:
        add(row["account"], 40, "structuring")
    for hub in hubs:
        add(hub["account"], 40, "pass-through hub")
    for cluster in clusters:
        for cust in cluster["customers"]:
            for acct in accounts_of[cust]:
                add(acct, 30, "shared identity network")
    for acct in list(scores):
        if rating.get(owner.get(acct)) == "high":
            add(acct, 5, "high-risk customer")

    rows = [{"account_id": a, "customer_id": owner.get(a, ""), "score": s["score"],
             "reasons": "; ".join(dict.fromkeys(s["reasons"]))} for a, s in scores.items()]
    rows.sort(key=lambda r: (-r["score"], r["account_id"]))
    return rows


def _prf(predicted: set, actual: set) -> dict:
    tp = len(predicted & actual)
    fp = len(predicted - actual)
    fn = len(actual - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": round(precision, 3), "recall": round(recall, 3)}


def evaluate(truth, rings, clusters, structuring, hubs):
    def actual(pattern):
        return {t["entity_id"] for t in truth if t["pattern"] == pattern}

    return {
        "ring (accounts)": _prf({a for r in rings for a in r["accounts"]}, actual("ring")),
        "mule network (customers)": _prf({c for cl in clusters for c in cl["customers"]}, actual("mule_network")),
        "mule hub (accounts)": _prf({h["account"] for h in hubs}, actual("mule_hub")),
        "structuring (accounts)": _prf({s["account"] for s in structuring}, actual("structuring")),
    }


def run_all(data_dir):
    ds = load_dataset(data_dir)
    txns = ds["transactions"]
    result = {
        "ds": ds,
        "rings": detect_rings(txns),
        "clusters": detect_identity_clusters(ds),
        "structuring": detect_structuring(txns),
        "hubs": detect_mule_hubs(txns),
    }
    graph = transfer_graph(txns)
    result["graph"] = graph
    result["communities"] = communities(graph)
    result["pagerank"] = pagerank(graph)
    result["scores"] = risk_scores(ds, result["rings"], result["clusters"], result["structuring"], result["hubs"])
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Run the AML / fraud graph detections offline.")
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="reports")
    args = p.parse_args()

    res = run_all(args.data)
    truth = load_truth(args.data)
    metrics = evaluate(truth, res["rings"], res["clusters"], res["structuring"], res["hubs"])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "risk_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["account_id", "customer_id", "score", "reasons"])
        w.writeheader()
        w.writerows(res["scores"])

    top_pr = sorted(res["pagerank"].items(), key=lambda kv: -kv[1])[:10]
    hub_ids = {h["account"] for h in res["hubs"]}
    summary = {
        "counts": {"rings": len(res["rings"]), "identity_clusters": len(res["clusters"]),
                   "structuring_accounts": len(res["structuring"]), "mule_hubs": len(res["hubs"]),
                   "louvain_communities": len(res["communities"]), "flagged_accounts": len(res["scores"])},
        "evaluation_vs_planted_patterns": metrics,
        "pagerank_top10": [{"account": a, "score": round(s, 5), "is_flagged_hub": a in hub_ids} for a, s in top_pr],
        "rings": [{"accounts": r["accounts"], "duration_hours": r["duration_hours"]} for r in res["rings"]],
        "identity_clusters": [{"customers": len(c["customers"]), "devices": len(c["devices"]),
                               "addresses": len(c["addresses"]), "phones": len(c["phones"])} for c in res["clusters"]],
    }
    with open(out / "results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("Detections")
    for k, v in summary["counts"].items():
        print(f"  {k:<22}{v:>5}")
    print("\nEvaluation against planted patterns (synthetic data)")
    print(f"  {'pattern':<26}{'TP':>4}{'FP':>5}{'FN':>5}{'precision':>11}{'recall':>8}")
    for name, m in metrics.items():
        print(f"  {name:<26}{m['true_positives']:>4}{m['false_positives']:>5}{m['false_negatives']:>5}"
              f"{m['precision']:>11.2f}{m['recall']:>8.2f}")
    print("\nTop 10 accounts by risk score")
    for row in res["scores"][:10]:
        print(f"  {row['account_id']}  {row['score']:>3}  {row['reasons']}")
    print(f"\nWrote {out / 'results.json'} and {out / 'risk_scores.csv'}")


if __name__ == "__main__":
    main()
