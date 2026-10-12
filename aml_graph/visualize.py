"""Render example figures for the README (no Neo4j needed).

    python -m aml_graph.visualize --data data --out docs/images
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402

from aml_graph.detect import run_all  # noqa: E402

COLORS = {"Customer": "#4C78A8", "Device": "#F58518", "Address": "#54A24B", "Phone": "#B279A2",
          "Account": "#E45756", "Hub": "#D62728"}


def draw_ring(ring: dict, path: Path) -> None:
    accounts = ring["accounts"]
    g = nx.DiGraph()
    labels = {}
    for hop in ring["hops"]:
        g.add_edge(hop["src"], hop["dst"])
        labels[(hop["src"], hop["dst"])] = f"${hop['amount']:,.0f}"
    pos = nx.circular_layout(g)
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    nx.draw_networkx_nodes(g, pos, node_color=COLORS["Account"], node_size=1300, ax=ax)
    nx.draw_networkx_labels(g, pos, font_size=8, font_color="white", ax=ax)
    nx.draw_networkx_edges(g, pos, arrows=True, arrowsize=22, node_size=1300,
                           connectionstyle="arc3,rad=0.12", ax=ax, width=1.8)
    nx.draw_networkx_edge_labels(g, pos, edge_labels=labels, font_size=8, label_pos=0.5,
                                 connectionstyle="arc3,rad=0.12", ax=ax)
    ax.set_title(f"Circular transfers: {len(accounts)} accounts, {ring['duration_hours']} hours end to end")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def draw_identity_cluster(cluster: dict, hubs: set, owner_of: dict, path: Path) -> None:
    g = nx.Graph()
    kinds = {}
    for c in cluster["customers"]:
        g.add_node(c)
        kinds[c] = "Hub" if any(owner_of.get(h) == c for h in hubs) else "Customer"
    # connect customers to the shared attributes in this cluster (only shared ones are interesting)
    return_links = cluster["_links"]
    for attr, kind, custs in return_links:
        if len(custs) > 1:
            g.add_node(attr)
            kinds[attr] = kind
            for c in custs:
                g.add_edge(c, attr)
    pos = nx.spring_layout(g, seed=7, k=0.9)
    fig, ax = plt.subplots(figsize=(7.5, 6))
    for kind in ("Customer", "Hub", "Device", "Address", "Phone"):
        nodes = [n for n in g if kinds[n] == kind]
        shape = "o" if kind in ("Customer", "Hub") else "s"
        nx.draw_networkx_nodes(g, pos, nodelist=nodes, node_color=COLORS[kind], node_shape=shape,
                               node_size=900 if shape == "o" else 650, label=kind if kind != "Hub" else "Customer (owns hub account)", ax=ax)
    nx.draw_networkx_edges(g, pos, alpha=0.5, ax=ax)
    nx.draw_networkx_labels(g, pos, font_size=7, ax=ax)
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False, markerscale=0.4, labelspacing=1.2)
    ax.set_title(f"Shared-identity network: {len(cluster['customers'])} customers linked through devices, addresses and phones")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def attach_links(ds: dict, cluster: dict) -> None:
    """Group the cluster's customers by each shared attribute."""
    members = set(cluster["customers"])
    links = []
    for table, key in (("customer_device", "device_id"), ("customer_address", "address_id"),
                       ("customer_phone", "phone_id")):
        kind = {"device_id": "Device", "address_id": "Address", "phone_id": "Phone"}[key]
        by_attr = {}
        for row in ds[table]:
            if row["customer_id"] in members:
                by_attr.setdefault(row[key], []).append(row["customer_id"])
        links += [(attr, kind, custs) for attr, custs in by_attr.items()]
    cluster["_links"] = links


def main() -> None:
    p = argparse.ArgumentParser(description="Render README figures.")
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="docs/images")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res = run_all(args.data)
    ds = res["ds"]

    ring = max(res["rings"], key=lambda r: len(r["accounts"]))
    draw_ring(ring, out / "circular_transfers.png")

    cluster = res["clusters"][0]
    attach_links(ds, cluster)
    owner_of = {a["account_id"]: a["customer_id"] for a in ds["accounts"]}
    draw_identity_cluster(cluster, {h["account"] for h in res["hubs"]}, owner_of, out / "mule_network.png")
    print("Wrote", out / "circular_transfers.png", "and", out / "mule_network.png")


if __name__ == "__main__":
    main()
