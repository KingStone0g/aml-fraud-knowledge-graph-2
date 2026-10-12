// Graph Data Science analytics. Requires the GDS plugin (docker-compose.yml installs it).
// Run each block separately in Neo4j Browser.

// ---------------------------------------------------------------------------
// A. Weakly connected components over shared identity attributes
// Customers who are connected through any shared device, address or phone end up in the
// same component. Households are small (2-3); mule networks are larger.
// ---------------------------------------------------------------------------
CALL gds.graph.project(
  'identity',
  ['Customer', 'Device', 'Address', 'Phone'],
  {
    USES:      {orientation: 'UNDIRECTED'},
    LIVES_AT:  {orientation: 'UNDIRECTED'},
    HAS_PHONE: {orientation: 'UNDIRECTED'}
  }
);

CALL gds.wcc.stream('identity') YIELD nodeId, componentId
WITH componentId, collect(gds.util.asNode(nodeId)) AS members
WITH componentId, [n IN members WHERE n:Customer] AS customers
WHERE size(customers) >= 4
RETURN componentId, size(customers) AS customers_in_network,
       [c IN customers | c.customer_id] AS customer_ids
ORDER BY customers_in_network DESC;

CALL gds.graph.drop('identity');

// ---------------------------------------------------------------------------
// B. Louvain community detection on the account transfer network
// Louvain needs an undirected graph. Communities group accounts that move money among
// themselves far more than with the rest of the network.
// ---------------------------------------------------------------------------
CALL gds.graph.project(
  'transfers_undirected',
  'Account',
  {TRANSFERRED_TO: {orientation: 'UNDIRECTED', properties: 'amount'}}
);

CALL gds.louvain.stream('transfers_undirected', {relationshipWeightProperty: 'amount'})
YIELD nodeId, communityId
WITH communityId, collect(gds.util.asNode(nodeId).account_id) AS accounts
RETURN communityId, size(accounts) AS size, accounts
ORDER BY size DESC
LIMIT 20;

CALL gds.graph.drop('transfers_undirected');

// ---------------------------------------------------------------------------
// C. PageRank (weighted by amount) on the directed transfer network
// Accounts that receive money from many well-connected senders rank high, which is
// a useful prioritisation signal for pass-through hubs.
// ---------------------------------------------------------------------------
CALL gds.graph.project(
  'transfers_directed',
  'Account',
  {TRANSFERRED_TO: {orientation: 'NATURAL', properties: 'amount'}}
);

CALL gds.pageRank.stream('transfers_directed', {relationshipWeightProperty: 'amount'})
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).account_id AS account, round(score, 5) AS pagerank
ORDER BY pagerank DESC
LIMIT 15;

CALL gds.graph.drop('transfers_directed');
