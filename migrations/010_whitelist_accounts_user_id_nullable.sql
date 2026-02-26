-- Allow x_whitelist_accounts.user_id to be NULL so we can sync handle -> user_id via API.
-- Primary key becomes handle; user_id remains UNIQUE when set.
-- Safe to run once (IF EXISTS / do nothing if already applied).

ALTER TABLE x_whitelist_accounts DROP CONSTRAINT IF EXISTS x_whitelist_accounts_pkey;
ALTER TABLE x_whitelist_accounts ADD CONSTRAINT x_whitelist_accounts_pkey PRIMARY KEY (handle);
ALTER TABLE x_whitelist_accounts ALTER COLUMN user_id DROP NOT NULL;
