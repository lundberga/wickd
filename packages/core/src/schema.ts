import type { Database } from "better-sqlite3";

interface Migration {
  readonly id: number;
  readonly name: string;
  readonly up: (db: Database) => void;
}

const MIGRATIONS: readonly Migration[] = [
  {
    id: 1,
    name: "init",
    up: (db) => {
      db.exec(`
        CREATE TABLE runs (
          id              TEXT PRIMARY KEY,
          name            TEXT NOT NULL,
          started_at      INTEGER NOT NULL,
          ended_at        INTEGER,
          status          TEXT NOT NULL
                          CHECK (status IN ('running','completed','failed','cancelled')),
          error           TEXT,
          total_cost_usd  REAL NOT NULL DEFAULT 0,
          metadata        TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX idx_runs_started_at ON runs(started_at DESC);
        CREATE INDEX idx_runs_status     ON runs(status);

        CREATE TABLE spans (
          id              TEXT PRIMARY KEY,
          run_id          TEXT NOT NULL
                          REFERENCES runs(id) ON DELETE CASCADE,
          parent_span_id  TEXT
                          REFERENCES spans(id) ON DELETE CASCADE,
          kind            TEXT NOT NULL
                          CHECK (kind IN ('llm','mcp_tool','http')),
          name            TEXT NOT NULL,
          started_at      INTEGER NOT NULL,
          ended_at        INTEGER,
          status          TEXT NOT NULL
                          CHECK (status IN ('pending','ok','error')),
          error           TEXT,
          input           TEXT NOT NULL DEFAULT '{}',
          output          TEXT NOT NULL DEFAULT '{}',
          cost_usd        REAL NOT NULL DEFAULT 0,
          tokens_input    INTEGER,
          tokens_output   INTEGER,
          provider        TEXT,
          model           TEXT,
          metadata        TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX idx_spans_run_started ON spans(run_id, started_at);
        CREATE INDEX idx_spans_kind         ON spans(kind);
        CREATE INDEX idx_spans_model        ON spans(model);
      `);
    },
  },
];

interface MigrationRow {
  readonly id: number;
}

export function migrate(db: Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      id         INTEGER PRIMARY KEY,
      name       TEXT NOT NULL,
      applied_at INTEGER NOT NULL
    )
  `);

  const rows = db
    .prepare("SELECT id FROM schema_migrations")
    .all() as readonly MigrationRow[];
  const applied = new Set(rows.map((r) => r.id));

  const insert = db.prepare(
    "INSERT INTO schema_migrations (id, name, applied_at) VALUES (?, ?, ?)",
  );

  const run = db.transaction((pending: readonly Migration[]) => {
    for (const m of pending) {
      m.up(db);
      insert.run(m.id, m.name, Date.now());
    }
  });

  const pending = MIGRATIONS.filter((m) => !applied.has(m.id));
  if (pending.length > 0) run(pending);
}

export function appliedMigrationIds(db: Database): readonly number[] {
  const rows = db
    .prepare("SELECT id FROM schema_migrations ORDER BY id ASC")
    .all() as readonly MigrationRow[];
  return rows.map((r) => r.id);
}
