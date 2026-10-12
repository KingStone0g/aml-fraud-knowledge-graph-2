// Constraints and indexes (the loader runs these too; kept here for reference).
CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (n:Customer) REQUIRE n.customer_id IS UNIQUE;
CREATE CONSTRAINT account_id IF NOT EXISTS FOR (n:Account) REQUIRE n.account_id IS UNIQUE;
CREATE CONSTRAINT device_id IF NOT EXISTS FOR (n:Device) REQUIRE n.device_id IS UNIQUE;
CREATE CONSTRAINT address_id IF NOT EXISTS FOR (n:Address) REQUIRE n.address_id IS UNIQUE;
CREATE CONSTRAINT phone_id IF NOT EXISTS FOR (n:Phone) REQUIRE n.phone_id IS UNIQUE;
CREATE CONSTRAINT cash_source_id IF NOT EXISTS FOR (n:CashSource) REQUIRE n.source_id IS UNIQUE;
CREATE INDEX transfer_ts IF NOT EXISTS FOR ()-[t:TRANSFERRED_TO]-() ON (t.ts_epoch);
CREATE INDEX deposit_ts IF NOT EXISTS FOR ()-[t:DEPOSITED]-() ON (t.ts_epoch);
