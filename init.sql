CREATE DATABASE langfuse;

\c thinktank

CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY,
  idea TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  result TEXT,
  agent_outputs JSONB,
  error TEXT,
  token_cost_usd NUMERIC(10, 4),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs(created_at DESC);
