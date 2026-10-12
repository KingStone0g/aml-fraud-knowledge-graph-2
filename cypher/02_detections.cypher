// Fraud / AML detection queries. Run them one at a time in Neo4j Browser.
// Thresholds mirror aml_graph/detect.py so the two implementations can be compared.

// ---------------------------------------------------------------------------
// Q1. Circular transfers (layering)
// 3-6 hop cycles where every hop is >= $1,000, all hops happen within 72 hours,
// and amounts stay within 15% of each other. Each cycle is reported once
// (canonical start = smallest account_id). Variable-length cycle search is fine at
// this scale; for millions of edges use time-bounded traversals or GDS instead.
// ---------------------------------------------------------------------------
MATCH p = (a:Account)-[:TRANSFERRED_TO*3..6]->(a)
WITH p, a, nodes(p)[1..-1] AS middle, relationships(p) AS rels
WHERE all(n IN middle WHERE n.account_id > a.account_id)
  AND size(middle) + 1 = size(reduce(seen = [a.account_id], n IN middle |
        CASE WHEN n.account_id IN seen THEN seen ELSE seen + n.account_id END))
  AND all(r IN rels WHERE r.amount >= 1000)
WITH p, rels,
     [r IN rels | r.ts_epoch] AS times,
     [r IN rels | r.amount]   AS amounts
WITH p, times, amounts,
     reduce(lo = times[0],   x IN times   | CASE WHEN x < lo THEN x ELSE lo END) AS first_ts,
     reduce(hi = times[0],   x IN times   | CASE WHEN x > hi THEN x ELSE hi END) AS last_ts,
     reduce(lo = amounts[0], x IN amounts | CASE WHEN x < lo THEN x ELSE lo END) AS min_amt,
     reduce(hi = amounts[0], x IN amounts | CASE WHEN x > hi THEN x ELSE hi END) AS max_amt
WHERE last_ts - first_ts <= 72 * 3600
  AND max_amt <= 1.15 * min_amt
RETURN [n IN nodes(p)[0..-1] | n.account_id] AS ring_accounts,
       length(p)                              AS hops,
       round((last_ts - first_ts) / 3600.0, 1) AS hours_elapsed,
       round(max_amt, 2)                      AS largest_hop
ORDER BY hops DESC, largest_hop DESC;

// ---------------------------------------------------------------------------
// Q2. Devices shared by three or more customers
// ---------------------------------------------------------------------------
MATCH (d:Device)<-[:USES]-(c:Customer)
WITH d, collect(c.customer_id) AS customers
WHERE size(customers) >= 3
RETURN d.device_id AS device, size(customers) AS customers_on_device, customers
ORDER BY customers_on_device DESC;

// ---------------------------------------------------------------------------
// Q3. Structuring: 5+ cash deposits of $9,000-$9,999 inside any 7-day window
// ---------------------------------------------------------------------------
MATCH (:CashSource)-[t1:DEPOSITED]->(a:Account)
WHERE t1.amount >= 9000 AND t1.amount < 10000
MATCH (:CashSource)-[t2:DEPOSITED]->(a)
WHERE t2.amount >= 9000 AND t2.amount < 10000
  AND t2.ts_epoch >= t1.ts_epoch AND t2.ts_epoch <= t1.ts_epoch + 7 * 86400
WITH a, t1, count(t2) AS deposits_in_window, sum(t2.amount) AS total_in_window
WHERE deposits_in_window >= 5
RETURN a.account_id AS account,
       max(deposits_in_window) AS max_deposits_in_7_days,
       round(max(total_in_window), 2) AS total_deposited
ORDER BY max_deposits_in_7_days DESC;

// ---------------------------------------------------------------------------
// Q4. Pass-through mule hubs
// 10+ distinct senders, and 80%+ of what comes in goes out to at most 3 recipients.
// ---------------------------------------------------------------------------
MATCH (s:Account)-[t:TRANSFERRED_TO]->(h:Account)
WITH h, count(DISTINCT s) AS senders, sum(t.amount) AS total_in
WHERE senders >= 10
MATCH (h)-[o:TRANSFERRED_TO]->(r:Account)
WITH h, senders, total_in, sum(o.amount) AS total_out, count(DISTINCT r) AS recipients
WHERE total_out >= 0.8 * total_in AND recipients <= 3
RETURN h.account_id AS hub, senders, recipients,
       round(total_in, 2) AS total_in, round(total_out, 2) AS total_out,
       round(total_out / total_in, 2) AS pass_through
ORDER BY senders DESC;

// ---------------------------------------------------------------------------
// Q5. Investigation view: everything around one flagged hub
// A00870 is a planted hub in the committed sample data (seed 42); swap in any id from Q4.
// Shows who owns it, which devices/addresses/phones those owners share, and the
// accounts that sent it money.
// ---------------------------------------------------------------------------
MATCH (hub:Account)
WHERE hub.account_id = 'A00870'
MATCH (owner:Customer)-[:OWNS]->(hub)
OPTIONAL MATCH (owner)-[:USES|LIVES_AT|HAS_PHONE]->(shared)<-[:USES|LIVES_AT|HAS_PHONE]-(associate:Customer)
OPTIONAL MATCH (sender:Account)-[t:TRANSFERRED_TO]->(hub)
RETURN owner, hub, shared, associate, sender, t
LIMIT 200;
