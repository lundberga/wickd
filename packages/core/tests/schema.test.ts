import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";

import { appliedMigrationIds, migrate } from "../src/schema.js";

const inMemory = (): Database.Database => new Database(":memory:");

interface CountRow {
  readonly n: number;
}

describe("migrate", () => {
  it("creates the schema_migrations table and applies the init migration", () => {
    const db = inMemory();
    migrate(db);
    expect(appliedMigrationIds(db)).toEqual([1]);

    const count = db
      .prepare(
        "SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name IN ('runs','spans','schema_migrations')",
      )
      .get() as CountRow;
    expect(count.n).toBe(3);
  });

  it("is idempotent", () => {
    const db = inMemory();
    migrate(db);
    migrate(db);
    migrate(db);
    expect(appliedMigrationIds(db)).toEqual([1]);
  });

  it("enforces the runs.status CHECK constraint", () => {
    const db = inMemory();
    migrate(db);
    expect(() =>
      db
        .prepare(
          "INSERT INTO runs (id, name, started_at, status) VALUES ('x','n',0,'bogus')",
        )
        .run(),
    ).toThrow();
  });

  it("enforces the spans.kind CHECK constraint", () => {
    const db = inMemory();
    migrate(db);
    db.prepare(
      "INSERT INTO runs (id, name, started_at, status) VALUES ('r','n',0,'running')",
    ).run();
    expect(() =>
      db
        .prepare(
          "INSERT INTO spans (id, run_id, kind, name, started_at, status) VALUES ('s','r','bogus','n',0,'pending')",
        )
        .run(),
    ).toThrow();
  });

  it("cascades span deletes when a run is deleted", () => {
    const db = inMemory();
    migrate(db);
    db.pragma("foreign_keys = ON");

    db.prepare(
      "INSERT INTO runs (id, name, started_at, status) VALUES ('r','n',0,'running')",
    ).run();
    db.prepare(
      "INSERT INTO spans (id, run_id, kind, name, started_at, status) VALUES ('s','r','llm','n',0,'pending')",
    ).run();

    db.prepare("DELETE FROM runs WHERE id = ?").run("r");

    const { n } = db
      .prepare("SELECT count(*) AS n FROM spans WHERE run_id = 'r'")
      .get() as CountRow;
    expect(n).toBe(0);
  });
});
