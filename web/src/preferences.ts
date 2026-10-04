import type { Baseline, Preferences } from "./types";
export const STORAGE_KEY = "fitness-dashboard-settings-v1";
const isoDate = (value: unknown): value is string =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
function baseline(value: unknown): value is Baseline {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<Baseline>;
  return (
    typeof entry.exercise_id === "string" &&
    typeof entry.condition_key === "string" &&
    typeof entry.source_set_id === "string" &&
    typeof entry.source_fingerprint === "string" &&
    /^[0-9a-f]{64}$/i.test(entry.source_fingerprint) &&
    typeof entry.version === "string" &&
    entry.version.length > 0 &&
    ["e1rm", "reps"].includes(entry.metric ?? "") &&
    typeof entry.value === "number" &&
    Number.isFinite(entry.value) &&
    entry.value > 0
  );
}
export function loadPreferences(): Preferences {
  let raw: Partial<Preferences> = {};
  try {
    const parsed: unknown = JSON.parse(
      localStorage.getItem(STORAGE_KEY) ?? "{}",
    );
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      raw = parsed as Partial<Preferences>;
  } catch {
    /* Invalid or restricted browser storage uses defaults. */
  }
  const customStart = isoDate(raw.customStart) ? raw.customStart : "";
  const customEnd = isoDate(raw.customEnd) ? raw.customEnd : "";
  const preset = ["4w", "12w", "all", "custom"].includes(raw.preset ?? "")
    ? raw.preset!
    : "12w";
  return {
    theme: ["system", "light", "dark"].includes(raw.theme ?? "")
      ? raw.theme!
      : "system",
    preset:
      preset === "custom" && (!customStart || !customEnd) ? "12w" : preset,
    customStart,
    customEnd,
    coreIds: Array.isArray(raw.coreIds)
      ? [
          ...new Set(
            raw.coreIds.filter(
              (id): id is string =>
                typeof id === "string" && id.length > 0 && id.length <= 128,
            ),
          ),
        ].slice(0, 64)
      : null,
    baselines: Array.isArray(raw.baselines)
      ? raw.baselines.filter(baseline).slice(0, 128)
      : [],
    selectedExerciseId:
      typeof raw.selectedExerciseId === "string"
        ? raw.selectedExerciseId
        : null,
  };
}
export function storePreferences(settings: Preferences) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    /* Storage restrictions do not block analysis. */
  }
}
