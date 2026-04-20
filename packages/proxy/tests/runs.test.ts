import { Store } from "wickd-core";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RunTracker } from "../src/runs.js";

describe("RunTracker", () => {
  let store: Store;
  let now = 1_000_000;

  beforeEach(() => {
    store = new Store(":memory:");
    now = 1_000_000;
  });
  afterEach(() => {
    store.close();
  });

  const clock = (): number => now;

  it("creates a session run on first attach", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    const run = tracker.attach(null);
    expect(run.name).toBe("agent");
    expect(run.status).toBe("running");
  });

  it("reuses the same run within the idle window", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    const a = tracker.attach(null);
    now += 10_000;
    const b = tracker.attach(null);
    now += 20_000;
    const c = tracker.attach(null);

    expect(a.id).toBe(b.id);
    expect(b.id).toBe(c.id);
  });

  it("starts a new run once the idle window expires", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    const first = tracker.attach(null);
    now += 30_001;
    const second = tracker.attach(null);

    expect(first.id).not.toBe(second.id);
    const reloaded = store.getRun(first.id);
    expect(reloaded?.status).toBe("completed");
  });

  it("attributes explicitly to an existing run when header is provided", () => {
    const tracker = new RunTracker(store, {
      runName: "session",
      idleTimeoutMs: 30_000,
      clock,
    });
    const explicit = store.startRun({ name: "explicit" });

    const run = tracker.attach(explicit.id);
    expect(run.id).toBe(explicit.id);
  });

  it("does not create or modify session state when using explicit attribution", () => {
    const tracker = new RunTracker(store, {
      runName: "session",
      idleTimeoutMs: 30_000,
      clock,
    });
    const explicit = store.startRun({ name: "explicit" });

    tracker.attach(explicit.id);
    expect(tracker.currentRun).toBeNull();

    // A subsequent session call should still create a fresh session run.
    const session = tracker.attach(null);
    expect(session.id).not.toBe(explicit.id);
  });

  it("falls back to session attribution for unknown run ids", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    const run = tracker.attach("ghost-id");
    expect(run.name).toBe("agent");
    expect(run.status).toBe("running");
  });

  it("shutdown closes the current session run", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    const run = tracker.attach(null);
    tracker.shutdown();

    expect(store.getRun(run.id)?.status).toBe("completed");
    expect(tracker.currentRun).toBeNull();
  });

  it("shutdown is idempotent", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    tracker.attach(null);
    tracker.shutdown();
    expect(() => tracker.shutdown()).not.toThrow();
  });

  it("swallows endRun failures during shutdown", () => {
    const tracker = new RunTracker(store, {
      runName: "agent",
      idleTimeoutMs: 30_000,
      clock,
    });

    const run = tracker.attach(null);
    // End the run out-of-band so the internal endRun call will throw.
    store.endRun(run.id, { status: "completed" });

    expect(() => tracker.shutdown()).not.toThrow();
    expect(tracker.currentRun).toBeNull();
  });
});
