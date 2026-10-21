-- Runs once, when the Postgres data volume is first created.
-- The main database (tibadirect) and the tiba role come from the POSTGRES_* variables.
CREATE DATABASE tibadirect_test OWNER tiba;
