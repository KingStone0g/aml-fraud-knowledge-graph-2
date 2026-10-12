"""Generate a synthetic banking dataset with planted AML / fraud patterns.

Everything here is synthetic and seeded, so results are reproducible. Besides the
graph CSVs, the generator writes ``ground_truth.csv`` so the detections can be
scored against the patterns that were planted.

Planted patterns
----------------
ring          Circular transfers (layering): money returns to where it started
              within 72 hours, with a small "fee" shaved off each hop.
mule_network  Customers who share devices / addresses / phones. One account in
              the network is a pass-through hub: many victims pay in, and nearly
              all of it is forwarded to a single cash-out account.
structuring   Repeated cash deposits just under the $10,000 reporting threshold
              within a week.
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2026, 1, 1)
DAYS = 90
CASH_SOURCE = "EXT-CASH"

FIRST_NAMES = [
    "Alex", "Maria", "James", "Priya", "Chen", "Fatima", "Liam", "Sofia", "Omar", "Hannah",
    "Diego", "Aisha", "Noah", "Yuki", "Ethan", "Amara", "Lucas", "Mei", "Carlos", "Zoe",
    "Ravi", "Elena", "Jamal", "Nina", "Tyler", "Grace", "Andre", "Leah", "Victor", "Ivy",
]
LAST_NAMES = [
    "Nguyen", "Garcia", "Smith", "Patel", "Kim", "Johnson", "Martinez", "Brown", "Lopez", "Davis",
    "Wilson", "Anderson", "Thomas", "Moore", "Jackson", "Lee", "Walker", "Hall", "Young", "King",
    "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Hill", "Campbell", "Mitchell", "Rivera",
]
STREETS = ["Oak St", "Maple Ave", "Cedar Ln", "Pine Rd", "Elm St", "Lake Dr", "Hill Ct", "River Rd", "Sunset Blvd", "Park Ave"]
CITIES = [("Dallas", "TX"), ("Austin", "TX"), ("Houston", "TX"), ("Plano", "TX"), ("Lubbock", "TX"),
          ("Denver", "CO"), ("Phoenix", "AZ"), ("Atlanta", "GA")]
DEVICE_TYPES = ["iPhone", "Android phone", "Windows PC", "MacBook", "iPad", "Android tablet"]
COUNTRIES = ["US"] * 17 + ["MX", "CA", "GB"]


def to_epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


class Dataset:
    """Accumulates rows for every CSV and hands out sequential ids."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.customers, self.accounts, self.devices = [], [], []
        self.addresses, self.phones = [], []
        self.cust_device, self.cust_address, self.cust_phone = [], [], []
        self.txns, self.truth = [], []

    # --- entities -----------------------------------------------------------
    def add_customer(self, risk_rating: str | None = None) -> str:
        rng = self.rng
        cid = f"C{len(self.customers) + 1:04d}"
        risk = risk_rating or rng.choices(["low", "medium", "high"], weights=[80, 17, 3])[0]
        onboarded = START - timedelta(days=rng.randint(30, 2000))
        self.customers.append({
            "customer_id": cid,
            "name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            "country": rng.choice(COUNTRIES),
            "risk_rating": risk,
            "onboarded": onboarded.date().isoformat(),
        })
        return cid

    def add_account(self, cid: str, account_type: str = "checking") -> str:
        aid = f"A{len(self.accounts) + 1:05d}"
        opened = START - timedelta(days=self.rng.randint(1, 1500))
        self.accounts.append({
            "account_id": aid, "customer_id": cid, "account_type": account_type,
            "opened": opened.date().isoformat(), "status": "active",
        })
        return aid

    def add_device(self) -> str:
        did = f"D{len(self.devices) + 1:04d}"
        self.devices.append({"device_id": did, "device_type": self.rng.choice(DEVICE_TYPES)})
        return did

    def add_address(self) -> str:
        aid = f"AD{len(self.addresses) + 1:04d}"
        city, state = self.rng.choice(CITIES)
        self.addresses.append({
            "address_id": aid,
            "street": f"{self.rng.randint(100, 9899)} {self.rng.choice(STREETS)}",
            "city": city, "state": state,
        })
        return aid

    def add_phone(self) -> str:
        pid = f"P{len(self.phones) + 1:04d}"
        self.phones.append({"phone_id": pid, "number": f"+1-555-{self.rng.randint(1000, 9999)}-{len(self.phones):04d}"})
        return pid

    def link_device(self, cid: str, did: str) -> None:
        self.cust_device.append({"customer_id": cid, "device_id": did})

    def link_address(self, cid: str, aid: str) -> None:
        self.cust_address.append({"customer_id": cid, "address_id": aid})

    def link_phone(self, cid: str, pid: str) -> None:
        self.cust_phone.append({"customer_id": cid, "phone_id": pid})

    def give_own_identity(self, cid: str) -> None:
        self.link_device(cid, self.add_device())
        self.link_address(cid, self.add_address())
        self.link_phone(cid, self.add_phone())

    # --- transactions -------------------------------------------------------
    def add_txn(self, src: str, dst: str, amount: float, ts: datetime, channel: str) -> None:
        ts = ts.replace(microsecond=0)
        self.txns.append({
            "src_account": src, "dst_account": dst, "amount": f"{amount:.2f}",
            "ts": ts.isoformat(), "ts_epoch": to_epoch(ts), "channel": channel,
        })

    def mark(self, pattern: str, group: str, entity_type: str, entity_id: str) -> None:
        self.truth.append({"pattern": pattern, "group_id": group, "entity_type": entity_type, "entity_id": entity_id})


def random_time(rng: random.Random, lo_day: float = 0, hi_day: float = DAYS) -> datetime:
    return START + timedelta(days=rng.uniform(lo_day, hi_day))


# ---------------------------------------------------------------------------
# Normal behaviour
# ---------------------------------------------------------------------------
def build_normal(ds: Dataset, n_customers: int, n_transfers: int, n_cash: int):
    rng = ds.rng
    cids, accounts_of = [], {}
    for _ in range(n_customers):
        cid = ds.add_customer()
        cids.append(cid)
        accounts_of[cid] = [ds.add_account(cid, "checking")]
        if rng.random() < 0.4:
            accounts_of[cid].append(ds.add_account(cid, "savings"))

    # Households share an address (and sometimes a phone or device). They are
    # legitimate, and exist so the shared-identity detector has to tell a family
    # of 2-3 apart from a 7-person mule network.
    order = cids[:]
    rng.shuffle(order)
    i = 0
    while i < len(order):
        size = rng.choices([1, 2, 3], weights=[85, 11, 4])[0]
        group = order[i:i + size]
        i += size
        shared_addr = ds.add_address()
        shared_phone = ds.add_phone() if len(group) > 1 and rng.random() < 0.5 else None
        shared_dev = ds.add_device() if len(group) > 1 and rng.random() < 0.3 else None
        for cid in group:
            ds.link_address(cid, shared_addr)
            ds.link_phone(cid, shared_phone or ds.add_phone())
            ds.link_device(cid, shared_dev or ds.add_device())
            if rng.random() < 0.3:
                ds.link_device(cid, ds.add_device())

    # Everyday transfers, mostly between a customer and a few regular counterparties.
    counterparties = {c: rng.sample([x for x in cids if x != c], 3) for c in cids}
    made = 0
    while made < n_transfers:
        src_c = rng.choice(cids)
        dst_c = rng.choice(counterparties[src_c]) if rng.random() < 0.85 else rng.choice(cids)
        if dst_c == src_c:
            continue
        src, dst = rng.choice(accounts_of[src_c]), rng.choice(accounts_of[dst_c])
        amount = min(max(rng.lognormvariate(5.0, 1.0), 5.0), 5000.0)
        channel = rng.choices(["ach", "p2p", "wire"], weights=[55, 40, 5])[0]
        ds.add_txn(src, dst, amount, random_time(rng), channel)
        made += 1

    # Everyday cash deposits, plus a few legitimate one-off deposits just under $10k
    # (e.g. a car sale) so structuring detection is tested for false positives.
    all_accts = [a for accts in accounts_of.values() for a in accts]
    for _ in range(n_cash):
        amount = min(max(rng.lognormvariate(6.5, 0.8), 100.0), 7000.0)
        ds.add_txn(CASH_SOURCE, rng.choice(all_accts), amount, random_time(rng), "cash")
    for acct in rng.sample(all_accts, 6):
        ds.add_txn(CASH_SOURCE, acct, rng.uniform(9000, 9900), random_time(rng), "cash")
    return cids, all_accts


# ---------------------------------------------------------------------------
# Planted fraud patterns
# ---------------------------------------------------------------------------
def plant_rings(ds: Dataset, n_rings: int) -> None:
    rng = ds.rng
    for g in range(1, n_rings + 1):
        k = rng.randint(3, 6)
        accts = []
        for _ in range(k):
            cid = ds.add_customer(risk_rating=rng.choice(["medium", "high"]))
            ds.give_own_identity(cid)
            accts.append(ds.add_account(cid, "checking"))
        t = random_time(rng, 10, DAYS - 10)
        amount = rng.uniform(8000, 48000)
        for i in range(k):
            ds.add_txn(accts[i], accts[(i + 1) % k], amount, t, "wire")
            amount *= rng.uniform(0.985, 0.995)
            t += timedelta(hours=rng.uniform(2, 12))
        for a in accts:
            ds.mark("ring", f"RING{g}", "account", a)


def plant_mule_networks(ds: Dataset, victim_pool: list[str], n_networks: int) -> None:
    rng = ds.rng
    for g in range(1, n_networks + 1):
        members = [ds.add_customer(risk_rating=rng.choice(["medium", "high"])) for _ in range(7)]
        devices = [ds.add_device(), ds.add_device()]
        shared_addr, shared_phone = ds.add_address(), ds.add_phone()
        for i, cid in enumerate(members):
            ds.link_device(cid, devices[i % 2])
            if i == 0:
                ds.link_device(cid, devices[1])  # bridges the two devices into one network
            if i < 3:
                ds.link_address(cid, shared_addr)
            else:
                ds.link_address(cid, ds.add_address())
            if 3 <= i < 5:
                ds.link_phone(cid, shared_phone)
            else:
                ds.link_phone(cid, ds.add_phone())
        accts = [ds.add_account(cid, "checking") for cid in members]
        hub, cashout = accts[0], accts[6]

        t0 = random_time(rng, 5, DAYS - 15)
        victims = rng.sample(victim_pool, rng.randint(12, 15))
        total = 0.0
        for v in victims:
            amount = rng.uniform(200, 2500)
            total += amount
            ds.add_txn(v, hub, amount, t0 + timedelta(hours=rng.uniform(0, 120)), "p2p")
        forwarded = total * rng.uniform(0.88, 0.95)
        t_out = t0 + timedelta(hours=rng.uniform(126, 160))
        ds.add_txn(hub, cashout, forwarded * 0.6, t_out, "wire")
        ds.add_txn(hub, cashout, forwarded * 0.4, t_out + timedelta(hours=rng.uniform(1, 8)), "wire")

        for cid in members:
            ds.mark("mule_network", f"MULE{g}", "customer", cid)
        ds.mark("mule_hub", f"MULE{g}", "account", hub)


def plant_structuring(ds: Dataset, n_actors: int) -> None:
    rng = ds.rng
    for g in range(1, n_actors + 1):
        cid = ds.add_customer(risk_rating=rng.choice(["low", "medium"]))
        ds.give_own_identity(cid)
        acct = ds.add_account(cid, "checking")
        t = random_time(rng, 5, DAYS - 12)
        for _ in range(rng.randint(6, 9)):
            ds.add_txn(CASH_SOURCE, acct, rng.uniform(9000, 9900), t, "cash")
            t += timedelta(hours=rng.uniform(6, 20))
        ds.mark("structuring", f"STRUCT{g}", "account", acct)



def plant_decoys(ds: Dataset, pool: list[str]) -> None:
    """Legitimate look-alikes that a naive rule would flag but a good one should not."""
    rng = ds.rng
    # Roommates settling up: a 3-cycle, but the amounts differ a lot.
    for _ in range(2):
        a, b, c = rng.sample(pool, 3)
        t = random_time(rng, 10, DAYS - 10)
        for src, dst, amount in ((a, b, 1800.0), (b, c, 1200.0), (c, a, 2400.0)):
            ds.add_txn(src, dst, amount, t, "p2p")
            t += timedelta(hours=rng.uniform(2, 10))
    # Similar amounts in a loop, but spread over ~10 days (outside the 72h window).
    a, b, c = rng.sample(pool, 3)
    t = random_time(rng, 10, DAYS - 20)
    for src, dst, amount in ((a, b, 1500.0), (b, c, 1490.0), (c, a, 1480.0)):
        ds.add_txn(src, dst, amount, t, "ach")
        t += timedelta(days=rng.uniform(3, 4))
    # Payroll / merchant style accounts: many senders in, but money fans out to many recipients.
    for _ in range(2):
        hub = rng.choice(pool)
        senders = rng.sample([x for x in pool if x != hub], 14)
        total = 0.0
        t = random_time(rng, 10, DAYS - 10)
        for s_acct in senders:
            amount = rng.uniform(200, 900)
            total += amount
            ds.add_txn(s_acct, hub, amount, t + timedelta(hours=rng.uniform(0, 72)), "ach")
        recipients = rng.sample([x for x in pool if x != hub and x not in senders], 10)
        for r_acct in recipients:
            ds.add_txn(hub, r_acct, total * 0.09, t + timedelta(hours=rng.uniform(80, 120)), "ach")

# ---------------------------------------------------------------------------
def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def generate(out_dir: str | Path, seed: int = 42, customers: int = 600, transfers: int = 3200,
             cash_deposits: int = 450, rings: int = 5, mule_networks: int = 4, structurers: int = 6) -> dict:
    rng = random.Random(seed)
    ds = Dataset(rng)
    _, victim_pool = build_normal(ds, customers, transfers, cash_deposits)
    plant_rings(ds, rings)
    plant_mule_networks(ds, victim_pool, mule_networks)
    plant_structuring(ds, structurers)
    plant_decoys(ds, victim_pool)

    ds.txns.sort(key=lambda t: t["ts_epoch"])
    for n, t in enumerate(ds.txns, start=1):
        t["txn_id"] = f"T{n:06d}"

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "customers.csv", ds.customers, ["customer_id", "name", "country", "risk_rating", "onboarded"])
    write_csv(out / "accounts.csv", ds.accounts, ["account_id", "customer_id", "account_type", "opened", "status"])
    write_csv(out / "devices.csv", ds.devices, ["device_id", "device_type"])
    write_csv(out / "addresses.csv", ds.addresses, ["address_id", "street", "city", "state"])
    write_csv(out / "phones.csv", ds.phones, ["phone_id", "number"])
    write_csv(out / "customer_device.csv", ds.cust_device, ["customer_id", "device_id"])
    write_csv(out / "customer_address.csv", ds.cust_address, ["customer_id", "address_id"])
    write_csv(out / "customer_phone.csv", ds.cust_phone, ["customer_id", "phone_id"])
    write_csv(out / "transactions.csv", ds.txns,
              ["txn_id", "src_account", "dst_account", "amount", "ts", "ts_epoch", "channel"])
    write_csv(out / "ground_truth.csv", ds.truth, ["pattern", "group_id", "entity_type", "entity_id"])
    return {"customers": len(ds.customers), "accounts": len(ds.accounts), "transactions": len(ds.txns),
            "devices": len(ds.devices), "addresses": len(ds.addresses), "phones": len(ds.phones)}


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the synthetic AML/fraud dataset.")
    p.add_argument("--out", default="data", help="output directory (default: data)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--customers", type=int, default=600)
    args = p.parse_args()
    stats = generate(args.out, seed=args.seed, customers=args.customers)
    print("Generated synthetic dataset in", args.out)
    for k, v in stats.items():
        print(f"  {k:<13}{v:>7,}")


if __name__ == "__main__":
    main()
