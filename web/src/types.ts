export type Theme = "system" | "light" | "dark";
export type Preset = "4w" | "12w" | "all" | "custom";
export interface ChartPoint {
  date?: string;
  date_or_bucket?: string;
  label?: string;
  category_id?: string;
  value: number | null;
  sample_count?: number;
  source_set_ids: string[];
  source_session_ids: string[];
  detail?: Record<string, unknown>;
}
export interface ChartSeries {
  key: string;
  label: string;
  observation_only?: boolean;
  condition_key?: string | null;
  points: ChartPoint[];
}
export interface ChartResult {
  status: "ok" | "empty" | "unavailable";
  title: string;
  metric: string;
  unit: string;
  aggregation: string;
  series: ChartSeries[];
  exclusions: { count: number; reasons: Record<string, number> | string[] };
  denominator?: number;
  message?: string;
  reason?: string;
  range?: unknown;
  available_loads?: number[];
  selected_load?: number;
}
export interface ModeChart {
  modes: Record<string, ChartResult>;
}
export interface ActualSet {
  id: string;
  date?: string;
  exercise_id?: string;
  exercise_label?: string;
  exercise?: string;
  baseline_fingerprint?: string;
  load: number | null;
  reps: number | null;
  e1rm_observed?: number | null;
  condition?: string | null;
  condition_key?: string | null;
  condition_confirmed?: boolean;
  pullup_mode?: string | null;
  set_type?: string | null;
  url?: string;
  completed?: boolean;
  [key: string]: unknown;
}
export interface CoreExercise {
  id: string;
  label: string;
  muscle?: string;
  pullup: boolean;
  sequence_ambiguous?: boolean;
  latest_sets?: ActualSet[];
  current_set: ActualSet | null;
  previous_set: ActualSet | null;
  record_best?: ActualSet | null;
  verified_pr?: {
    value: number;
    metric: string;
    source_set_id: string;
    condition_key: string;
    is_current_best: boolean;
    scope: string;
  } | null;
  comparison?: {
    status?: string;
    reason?: string;
    delta?: number;
    percent?: number;
    sequence_ambiguous?: boolean;
  };
  trend: ChartResult;
}
export interface Session {
  id: string;
  date: string;
  split: string | null;
  url?: string;
  label?: string;
  sets: ActualSet[];
  set_count?: number;
  exercise_count?: number;
}
export interface Catalog {
  snapshot_id: string;
  exercises: {
    id: string;
    label: string;
    muscle?: string;
    pullup?: boolean;
    conditions?: string[];
    available_loads?: number[];
    selected_load?: number;
    pullup_modes?: string[];
  }[];
  default_core_exercise_ids: string[];
  filters?: { splits: string[]; set_types: string[] };
  splits?: string[];
  set_types?: string[];
  conditions?: Record<string, unknown> | unknown[];
}
export interface Status {
  snapshot_id: string | null;
  fetched_at: string | null;
  refresh_state: string;
  current_job_id?: string | null;
  error?: string | { message?: string } | null;
  using_previous_data: boolean;
  source_counts?: Record<string, number>;
  optional_sources?: Record<string, unknown>;
}
export interface Job {
  id: string;
  state: "queued" | "running" | "succeeded" | "failed";
  error?: string | { message?: string } | null;
  snapshot_id?: string;
  progress?:
    | string
    | { stage?: string; source?: string; page?: number; attempt?: number };
}
export interface Baseline {
  exercise_id: string;
  condition_key: string;
  metric: string;
  source_set_id: string;
  value: number;
  version: string;
  source_fingerprint: string;
}
export interface AnalysisRequest {
  snapshot_id: string;
  range: { preset: Preset; start?: string; end?: string };
  filters: {
    split_ids_or_values: string[];
    set_types: string[];
    exercise_ids: string[];
  };
  core_exercise_ids: string[];
  detail_scope: {
    exercise_id?: string;
    condition_key?: string;
    fixed_load?: number;
    pullup_mode?: string;
  };
  baselines: Baseline[];
  recent_limit?: number;
}
export interface Analysis {
  meta: {
    snapshot_id: string;
    schema_version: number | string;
    timezone: string;
    range_start: string;
    range_end: string;
    fetched_at: string;
    source_max_last_edited_at?: string | null;
    refresh_state: string;
    using_previous_data: boolean;
  };
  summary: {
    current_week_start: string;
    current_week_end: string;
    sessions: number;
    sets: number;
    latest_completed_session_date: string | null;
    range_sessions: number;
    range_sets: number;
  };
  core_exercises: CoreExercise[];
  charts: Record<string, ChartResult | ModeChart>;
  recent_sessions: Session[];
  recent_total?: number;
  diagnostics: Record<string, unknown>;
  optional_sources: Record<string, unknown>;
}
export interface Preferences {
  theme: Theme;
  preset: Preset;
  customStart: string;
  customEnd: string;
  coreIds: string[] | null;
  baselines: Baseline[];
  selectedExerciseId: string | null;
}
