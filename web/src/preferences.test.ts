import { afterEach, describe, expect, it, vi } from "vitest";
import { loadPreferences, STORAGE_KEY, storePreferences } from "./preferences";
function storage(value: string | null, throws = false) {
  const setItem = vi.fn();
  vi.stubGlobal("localStorage", {
    getItem: () => {
      if (throws) throw new Error("blocked");
      return value;
    },
    setItem,
  });
  return setItem;
}
afterEach(() => vi.unstubAllGlobals());
describe("persistent browser preferences", () => {
  it.each(["null", "[]", "42", "{broken"])(
    "starts safely with corrupted storage %s",
    (value) => {
      storage(value);
      expect(loadPreferences()).toMatchObject({
        theme: "system",
        preset: "12w",
        coreIds: null,
        baselines: [],
      });
    },
  );
  it("survives blocked storage", () => {
    storage(null, true);
    expect(loadPreferences().theme).toBe("system");
    expect(() => storePreferences(loadPreferences())).not.toThrow();
  });
  it("preserves intentionally empty selection and readable theme", () => {
    storage(JSON.stringify({ coreIds: [], theme: "dark", preset: "all" }));
    expect(loadPreferences()).toMatchObject({
      coreIds: [],
      theme: "dark",
      preset: "all",
    });
  });
  it("deduplicates stable exercise IDs and rejects invalid entries", () => {
    storage(
      JSON.stringify({ coreIds: ["bench", "bench", null, 12, "squat", ""] }),
    );
    expect(loadPreferences().coreIds).toEqual(["bench", "squat"]);
  });
  it("does not restore incomplete custom range into a perpetual loading screen", () => {
    storage(JSON.stringify({ preset: "custom", customStart: "2026-10-01" }));
    expect(loadPreferences().preset).toBe("12w");
  });
  it("restores frozen fingerprinted baseline only", () => {
    const valid = {
      exercise_id: "bench",
      condition_key: "barbell",
      metric: "e1rm",
      source_set_id: "set",
      source_fingerprint: "a".repeat(64),
      version: "first",
      value: 100,
    };
    storage(
      JSON.stringify({
        baselines: [
          valid,
          { ...valid, value: 0 },
          { ...valid, source_fingerprint: null },
        ],
      }),
    );
    expect(loadPreferences().baselines).toEqual([valid]);
  });
  it("stores no runtime workout snapshots in preferences", () => {
    const setItem = storage(null);
    storePreferences(loadPreferences());
    expect(setItem).toHaveBeenCalledWith(STORAGE_KEY, expect.any(String));
    expect(JSON.parse(setItem.mock.calls[0][1])).not.toHaveProperty("analysis");
  });
});
