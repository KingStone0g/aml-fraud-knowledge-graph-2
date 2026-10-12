"""Load the synthetic dataset into Neo4j.

Usage:
    docker compose up -d
    python -m aml_graph.load_neo4j --reset

Connection settings come from environment variables (see .env.example):
NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from neo4j import GraphDatabase

BATCH = 1000
CASH_SOURCE = "EXT-CASH"

CONSTRAINTS = [
    "CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (n:Customer) REQUIRE n.customer_id IS UNIQUE",
    "CREATE CONSTRAINT account_id IF NOT EXISTS FOR (n:Account) REQUIRE n.account_id IS UNIQUE",
    "CREATE CONSTRAINT device_id IF NOT EXISTS FOR (n:Device) REQUIRE n.device_id IS UNIQUE",
    "CREATE CONSTRAINT address_id IF NOT EXISTS FOR (n:Address) REQUIRE n.address_id IS UNIQUE",
    "CREATE CONSTRAINT phone_id IF NOT EXISTS FOR (n:Phone) REQUIRE n.phone_id IS UNIQUE",
    "CREATE CONSTRAINT cash_source_id IF NOT EXISTS FOR (n:CashSource) REQUIRE n.source_id IS UNIQUE",
    "CREATE INDEX transfer_ts IF NOT EXISTS FOR ()-[t:TRANSFERRED_TO]-() ON (t.ts_epoch)",
    "CREATE INDEX deposit_ts IF NOT EXISTS FOR ()-[t:DEPOSITED]-() ON (t.ts_epoch)",
]

Q_CUSTOMERS = """
UNWIND $rows AS row
MERGE (c:Customer {customer_id: row.customer_id})
SET c.name = row.name, c.country = row.country, c.risk_rating = row.risk_rating,
    c.onboarded = date(row.onboarded)
"""

Q_ACCOUNTS = """
UNWIND $rows AS row
MERGE (a:Account {account_id: row.account_id})
SET a.account_type = row.account_type, a.opened = date(row.opened), a.status = row.status
WITH a, row
MATCH (c:Customer {customer_id: row.customer_id})
MERGE (c)-[:OWNS]->(a)
"""

Q_DEVICES = """
UNWIND $rows AS row
MERGE (d:Device {device_id: row.device_id})
SET d.device_type = row.device_type
"""

Q_ADDRESSES = """
UNWIND $rows AS row
MERGE (a:Address {address_id: row.address_id})
SET a.street = row.street, a.city = row.city, a.state = row.state
"""

Q_PHONES = """
UNWIND $rows AS row
MERGE (p:Phone {phone_id: row.phone_id})
SET p.number = row.number
"""

Q_CUSTOMER_DEVICE = """
UNWIND $rows AS row
MATCH (c:Customer {customer_id: row.customer_id})
MATCH (d:Device {device_id: row.device_id})
MERGE (c)-[:USES]->(d)
"""

Q_CUSTOMER_ADDRESS = """
UNWIND $rows AS row
MATCH (c:Customer {customer_id: row.customer_id})
MATCH (a:Address {address_id: row.address_id})
MERGE (c)-[:LIVES_AT]->(a)
"""

Q_CUSTOMER_PHONE = """
UNWIND $rows AS row
MATCH (c:Customer {customer_id: row.customer_id})
MATCH (p:Phone {phone_id: row.phone_id})
MERGE (c)-[:HAS_PHONE]->(p)
"""

Q_TRANSFERS = """
UNWIND $rows AS row
MATCH (s:Account {account_id: row.src_account})
MATCH (d:Account {account_id: row.dst_account})
MERGE (s)-[t:TRANSFERRED_TO {txn_id: row.txn_id}]->(d)
SET t.amount = toFloat(row.amount), t.ts = datetime(row.ts),
    t.ts_epoch = toInteger(row.ts_epoch), t.channel = row.channel
"""

Q_DEPOSITS = """
UNWIND $rows AS row
MERGE (s:CashSource {source_id: row.src_account})
WITH s, row
MATCH (d:Account {account_id: row.dst_account})
MERGE (s)-[t:DEPOSITED {txn_id: row.txn_id}]->(d)
SET t.amount = toFloat(row.amount), t.ts = datetime(row.ts),
    t.ts_epoch = toInteger(row.ts_epoch), t.channel = row.channel
"""


def read_csv(data_dir: Path, name: str) -> list[dict]:
    with open(data_dir / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def run_batches(session, query: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), BATCH):
        session.run(query, rows=rows[i:i + BATCH]).consume()


def main() -> None:
    p = argparse.ArgumentParser(description="Load the AML dataset into Neo4j.")
    p.add_argument("--data", default="data", help="directory with the CSV files")
    p.add_argument("--reset", action="store_true", help="delete everything in the database first")
    args = p.parse_args()

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "changeme123")
    data_dir = Path(args.data)

    transactions = read_csv(data_dir, "transactions")
    transfers = [t for t in transactions if t["src_account"] != CASH_SOURCE]
    deposits = [t for t in transactions if t["src_account"] == CASH_SOURCE]

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            if args.reset:
                print("Clearing database ...")
                session.run("MATCH (n) DETACH DELETE n").consume()
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()

            steps = [
                ("customers", Q_CUSTOMERS, read_csv(data_dir, "customers")),
                ("accounts", Q_ACCOUNTS, read_csv(data_dir, "accounts")),
                ("devices", Q_DEVICES, read_csv(data_dir, "devices")),
                ("addresses", Q_ADDRESSES, read_csv(data_dir, "addresses")),
                ("phones", Q_PHONES, read_csv(data_dir, "phones")),
                ("customer-device links", Q_CUSTOMER_DEVICE, read_csv(data_dir, "customer_device")),
                ("customer-address links", Q_CUSTOMER_ADDRESS, read_csv(data_dir, "customer_address")),
                ("customer-phone links", Q_CUSTOMER_PHONE, read_csv(data_dir, "customer_phone")),
                ("account transfers", Q_TRANSFERS, transfers),
                ("cash deposits", Q_DEPOSITS, deposits),
            ]
            for label, query, rows in steps:
                run_batches(session, query, rows)
                print(f"  loaded {len(rows):>6,} {label}")

            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            print(f"Done: {nodes:,} nodes, {rels:,} relationships.")


if __name__ == "__main__":
    main()
