import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart3,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  Dumbbell,
  ExternalLink,
  Heart,
  Info,
  LoaderCircle,
  Moon,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import { DataChart } from "./components/Chart";
import { displayLabel, issueLabel } from "./labels";
import { api, ApiError, csv, errorMessage } from "./api";
import { loadPreferences, storePreferences } from "./preferences";
import type {
  ActualSet,
  Analysis,
  AnalysisRequest,
  Baseline,
  Catalog,
  ChartResult,
  CoreExercise,
  Job,
  ModeChart,
  Preferences,
  Session,
  Status,
  Theme,
} from "./types";

const emptyChart = (title: string): ChartResult => ({
  status: "empty",
  title,
  metric: "",
  unit: "",
  aggregation: "",
  series: [],
  exclusions: { count: 0, reasons: {} },
});
const TITLES: Record<string, string> = {
  C01: "근력 추세",
  C02: "같은 중량의 반복",
  C03: "반복 구간별 최고 중량",
  C04: "주간 훈련량",
  C05: "근육별 세트 분포",
  C06: "종목별 세트 분포",
  C07: "종목별 빈도",
  C08: "풀업 추세",
  C09: "분할별 구성",
  C10: "발전 지수",
};
function chartOf(
  data: Analysis | null,
  id: string,
  mode?: string,
): ChartResult {
  const chart = data?.charts[id];
  if (!chart) return emptyChart(TITLES[id]);
  const result =
    "modes" in chart
      ? (chart as ModeChart).modes[mode ?? Object.keys(chart.modes)[0]]
      : (chart as ChartResult);
  return {
    ...(result ?? emptyChart(TITLES[id])),
    title: result?.title ?? TITLES[id],
  };
}
function numeric(value: number | null | undefined, digits = 1) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(
        value,
      );
}
function shortDate(value: string | null | undefined) {
  if (!value) return "—";
  return value.slice(5, 10).replace("-", "/");
}
function fetched(value: string | null | undefined) {
  if (!value) return "아직 조회하지 않음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const part = (type: string) =>
    parts.find((p) => p.type === type)?.value ?? "";
  return `${part("month")}/${part("day")} ${part("hour")}:${part("minute")}`;
}
function progressLabel(progress: Job["progress"]) {
  if (typeof progress === "string") return progress;
  if (!progress) return "";
  const stages: Record<string, string> = {
    schema: "원본 속성 확인",
    reading: "원본 조회",
    read: "원본 조회",
    scan: "원본 조회",
    fingerprint: "변경 확인",
    verify: "변경 확인",
    normalize: "기록 분석",
    optional_source: "건강 원본 조회",
    succeeded: "조회 완료",
  };
  return [
    stages[progress.stage ?? ""] ?? "원본 조회",
    progress.page ? `${progress.page}페이지` : null,
    progress.attempt && progress.attempt > 1
      ? `${progress.attempt}번째 시도`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function seoulDay(date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}
function displayField(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map(displayField).join(" · ");
  if (typeof value === "object") {
    const date = value as { start?: string; end?: string };
    if (date.start)
      return date.end ? `${date.start} – ${date.end}` : date.start;
    return "입력 형식 확인 필요";
  }
  return String(value);
}
function actual(set: ActualSet | null | undefined) {
  if (!set) return "기록 없음";
  if (set.load === null || set.load === undefined)
    return `${numeric(set.reps, 0)}회 · 중량 미기록`;
  return `${numeric(set.load)} kg × ${numeric(set.reps, 0)}회`;
}
function safeNotionUrl(url: string | undefined) {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" &&
      (parsed.hostname === "app.notion.com" ||
        parsed.hostname === "notion.so" ||
        parsed.hostname.endsWith(".notion.so") ||
        parsed.hostname === "notion.site" ||
        parsed.hostname.endsWith(".notion.site"))
      ? url
      : undefined;
  } catch {
    return undefined;
  }
}
function IconLogo() {
  return (
    <span className="logo-bars" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
function ModeButtons({
  items,
  value,
  onChange,
  label,
}: {
  items: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.key}
          className={value === item.key ? "active" : ""}
          onClick={() => onChange(item.key)}
          aria-pressed={value === item.key}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
function Loading() {
  return (
    <div className="loading-layout" aria-label="Notion 조회 중">
      <div className="loading-note">
        <LoaderCircle className="spin" size={18} /> Notion 조회 중
      </div>
      <div className="summary-grid">
        {[0, 1, 2, 3].map((n) => (
          <div className="skeleton skeleton-summary" key={n} />
        ))}
      </div>
      <div className="skeleton skeleton-core" />
      <div className="charts-grid">
        {[0, 1].map((n) => (
          <div className="skeleton skeleton-chart" key={n} />
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [preferences, setPreferences] = useState<Preferences>(loadPreferences);
  const [status, setStatus] = useState<Status | null>(null),
    [catalog, setCatalog] = useState<Catalog | null>(null),
    [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [job, setJob] = useState<Job | null>(null),
    [error, setError] = useState<string | null>(null),
    [analysisError, setAnalysisError] = useState<string | null>(null),
    [isAnalyzing, setAnalyzing] = useState(false);
  const [filters, setFilters] = useState({
      splits: [] as string[],
      types: [] as string[],
    }),
    [filtersOpen, setFiltersOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false),
    [draftIds, setDraftIds] = useState<string[]>([]),
    [search, setSearch] = useState("");
  const [weeklyMode, setWeeklyMode] = useState("sessions"),
    [splitMode, setSplitMode] = useState("sets"),
    [frequencyMode, setFrequencyMode] = useState("top10"),
    [setsMode, setSetsMode] = useState("top10");
  const [detailTab, setDetailTab] = useState("C01"),
    [detailOpen, setDetailOpen] = useState(false),
    [fixedLoad, setFixedLoad] = useState<string>(""),
    [condition, setCondition] = useState(""),
    [pullupMode, setPullupMode] = useState("");
  const [expandedSession, setExpandedSession] = useState<string | null>(null),
    [recentLimit, setRecentLimit] = useState(3),
    [exporting, setExporting] = useState(false),
    [exportError, setExportError] = useState<string | null>(null),
    [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [currentDay, setCurrentDay] = useState(() => seoulDay());
  useEffect(() => {
    setRecentLimit(3);
    setExpandedSession(null);
  }, [
    preferences.preset,
    preferences.customStart,
    preferences.customEnd,
    filters,
  ]);
  useEffect(() => {
    const update = () => setCurrentDay(seoulDay());
    const timer = setInterval(update, 60000);
    document.addEventListener("visibilitychange", update);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  const [resolvedTheme, setResolvedTheme] = useState<"light" | "dark">("light");
  const dialogRef = useRef<HTMLDivElement>(null),
    settingsTrigger = useRef<HTMLElement | null>(null);
  const refreshRef = useRef<Promise<void> | null>(null),
    mounted = useRef(true),
    requestCounter = useRef(0);
  const busy = job?.state === "queued" || job?.state === "running";
  const coreIds =
    preferences.coreIds ?? catalog?.default_core_exercise_ids ?? [];
  const selectedId =
    coreIds.includes(preferences.selectedExerciseId ?? "") &&
    catalog?.exercises.some(
      (exercise) => exercise.id === preferences.selectedExerciseId,
    )
      ? preferences.selectedExerciseId!
      : (coreIds.find((id) =>
          catalog?.exercises.some((exercise) => exercise.id === id),
        ) ??
        catalog?.exercises[0]?.id ??
        "");
  const selectedCore = analysis?.core_exercises.find(
    (exercise) => exercise.id === selectedId,
  );
  const selectedExercise = catalog?.exercises.find(
    (exercise) => exercise.id === selectedId,
  );
  const setPreference = useCallback(
    (patch: Partial<Preferences>) =>
      setPreferences((current) => ({ ...current, ...patch })),
    [],
  );
  useEffect(() => storePreferences(preferences), [preferences]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const theme =
        preferences.theme === "system"
          ? media.matches
            ? "dark"
            : "light"
          : preferences.theme;
      document.documentElement.dataset.theme = theme;
      document.documentElement.style.colorScheme = theme;
      setResolvedTheme(theme);
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preferences.theme]);
  const loadCatalog = useCallback(async () => {
    const next = await api<Catalog>("/api/catalog");
    if (mounted.current) {
      setCatalog(next);
      setPreferences((current) => {
        const ids = current.coreIds ?? next.default_core_exercise_ids;
        return {
          ...current,
          coreIds: ids,
          selectedExerciseId: ids.includes(current.selectedExerciseId ?? "")
            ? current.selectedExerciseId
            : (ids[0] ?? null),
        };
      });
    }
  }, []);
  const refresh = useCallback(async () => {
    if (refreshRef.current) return refreshRef.current;
    const run = async () => {
      setError(null);
      try {
        let next = await api<Job>("/api/refresh", {});
        if (!mounted.current) return;
        setJob(next);
        const started = Date.now();
        while (next.state === "queued" || next.state === "running") {
          await new Promise((resolve) => setTimeout(resolve, 600));
          if (!mounted.current) return;
          if (Date.now() - started > 135000)
            throw new Error(
              "조회 시간이 초과되었습니다. 상태를 확인하고 다시 시도하세요",
            );
          next = await api<Job>(`/api/refresh/${encodeURIComponent(next.id)}`);
          setJob(next);
        }
        const nextStatus = await api<Status>("/api/status");
        if (!mounted.current) return;
        setStatus(nextStatus);
        if (next.state === "failed") {
          setError(
            errorMessage(
              next.error ?? nextStatus.error ?? "Notion 조회에 실패했습니다",
            ),
          );
          return;
        }
        await loadCatalog();
      } catch (caught) {
        if (mounted.current) setError(errorMessage(caught));
      } finally {
        if (mounted.current)
          setJob((current) =>
            current?.state === "running" || current?.state === "queued"
              ? { ...current, state: "failed" }
              : current,
          );
        refreshRef.current = null;
      }
    };
    refreshRef.current = run();
    return refreshRef.current;
  }, [loadCatalog]);
  useEffect(() => {
    mounted.current = true;
    (async () => {
      try {
        const current = await api<Status>("/api/status");
        if (!mounted.current) return;
        setStatus(current);
        if (current.snapshot_id) await loadCatalog();
      } catch (caught) {
        if (mounted.current) setError(errorMessage(caught));
      }
      if (mounted.current) void refresh();
    })();
    return () => {
      mounted.current = false;
    };
  }, [loadCatalog, refresh]);
  useEffect(() => {
    setFixedLoad("");
    setCondition("");
    setPullupMode("");
    setDetailTab(selectedExercise?.pullup ? "C08" : "C01");
  }, [selectedId, selectedExercise?.pullup]);
  const analysisRequest = useMemo<AnalysisRequest | null>(
    () =>
      catalog
        ? {
            snapshot_id: catalog.snapshot_id,
            range: {
              preset: preferences.preset,
              ...(preferences.preset === "custom"
                ? { start: preferences.customStart, end: preferences.customEnd }
                : {}),
            },
            filters: {
              split_ids_or_values: filters.splits,
              set_types: filters.types,
              exercise_ids: [],
            },
            core_exercise_ids: coreIds.filter((id) =>
              catalog.exercises.some((ex) => ex.id === id),
            ),
            detail_scope: {
              exercise_id: selectedId || undefined,
              condition_key: condition || undefined,
              fixed_load: fixedLoad !== "" ? Number(fixedLoad) : undefined,
              pullup_mode: pullupMode || undefined,
            },
            baselines: preferences.baselines,
            recent_limit: recentLimit,
          }
        : null,
    [
      catalog,
      preferences.preset,
      preferences.customStart,
      preferences.customEnd,
      preferences.baselines,
      coreIds,
      filters,
      selectedId,
      condition,
      fixedLoad,
      pullupMode,
      recentLimit,
    ],
  );
  useEffect(() => {
    if (!analysisRequest) return;
    if (
      analysisRequest.range.preset === "custom" &&
      (!analysisRequest.range.start || !analysisRequest.range.end)
    )
      return;
    const controller = new AbortController();
    const count = ++requestCounter.current;
    setAnalyzing(true);
    setAnalysisError(null);
    api<Analysis>("/api/analysis", analysisRequest, controller.signal)
      .then((next) => {
        if (count === requestCounter.current) setAnalysis(next);
      })
      .catch(async (caught) => {
        if (controller.signal.aborted) return;
        if (caught instanceof ApiError && caught.status === 409) {
          try {
            await loadCatalog();
          } catch (err) {
            setAnalysisError(errorMessage(err));
          }
        } else setAnalysisError(errorMessage(caught));
      })
      .finally(() => {
        if (count === requestCounter.current) setAnalyzing(false);
      });
    return () => controller.abort();
  }, [analysisRequest, loadCatalog]);
  useEffect(() => {
    if (!settingsOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const timer = setTimeout(
      () => dialogRef.current?.querySelector<HTMLElement>("button")?.focus(),
      0,
    );
    return () => {
      clearTimeout(timer);
      document.body.style.overflow = previousOverflow;
      settingsTrigger.current?.focus();
    };
  }, [settingsOpen]);
  const rangeLabel =
    preferences.preset === "4w"
      ? "최근 4주"
      : preferences.preset === "12w"
        ? "최근 12주"
        : preferences.preset === "all"
          ? "전체 기록"
          : "선택 기간";
  const period = analysis
    ? `${analysis.meta.range_start} – ${analysis.meta.range_end}`
    : "";
  const visibleFetched = analysis?.meta.fetched_at ?? status?.fetched_at;
  const needsFreshQuery = Boolean(
    visibleFetched && seoulDay(new Date(visibleFetched)) < currentDay,
  );
  const previousData =
    busy ||
    Boolean(error) ||
    status?.using_previous_data ||
    analysis?.meta.using_previous_data;
  const selectedConditions = useMemo(() => {
    const keys = new Set<string>(selectedExercise?.conditions ?? []);
    for (const session of analysis?.recent_sessions ?? [])
      for (const set of session.sets ?? [])
        if (
          set.exercise_id === selectedId &&
          set.condition_confirmed &&
          (set.condition_key || set.condition)
        )
          keys.add(String(set.condition_key ?? set.condition));
    for (const series of chartOf(
      analysis,
      selectedExercise?.pullup ? "C08" : "C01",
    ).series)
      if (series.condition_key) keys.add(series.condition_key);
    return [...keys];
  }, [
    analysis,
    selectedId,
    selectedExercise?.pullup,
    selectedExercise?.conditions,
  ]);
  const availableLoads = selectedExercise?.pullup
    ? (selectedExercise.available_loads ?? [])
    : (chartOf(analysis, "C02").available_loads ??
      selectedExercise?.available_loads ??
      []);
  const diagnostics = diagnosticEntries(analysis?.diagnostics ?? {}).filter(
    ([, value]) => Number(value) > 0,
  );
  const currentBaseline = preferences.baselines.find(
    (b) =>
      b.exercise_id === selectedId &&
      b.condition_key ===
        (condition ||
          selectedCore?.current_set?.condition_key ||
          selectedCore?.current_set?.condition),
  );
  const baselineSet = selectedCore?.current_set;
  const canSetBaseline = Boolean(
    baselineSet?.baseline_fingerprint &&
    baselineSet?.condition_confirmed &&
    (baselineSet.condition_key || baselineSet.condition) &&
    (selectedExercise?.pullup
      ? baselineSet.pullup_mode === "bodyweight" && Number(baselineSet.reps) > 0
      : Number(baselineSet.e1rm_observed) > 0),
  );
  function setBaseline() {
    if (!baselineSet || !canSetBaseline) return;
    const key = String(baselineSet.condition_key ?? baselineSet.condition);
    const baseline: Baseline = {
      exercise_id: selectedId,
      condition_key: key,
      metric: selectedExercise?.pullup ? "reps" : "e1rm",
      source_set_id: baselineSet.id,
      value: Number(
        selectedExercise?.pullup ? baselineSet.reps : baselineSet.e1rm_observed,
      ),
      version: String(Date.now()),
      source_fingerprint: baselineSet.baseline_fingerprint!,
    };
    setPreference({
      baselines: [
        ...preferences.baselines.filter(
          (b) => !(b.exercise_id === selectedId && b.condition_key === key),
        ),
        baseline,
      ],
    });
  }
  function openSettings() {
    settingsTrigger.current = document.activeElement as HTMLElement;
    setDraftIds([...coreIds]);
    setSearch("");
    setSettingsOpen(true);
  }
  function changeCore(id: string) {
    setPreference({ selectedExerciseId: id });
  }
  function reorder(index: number, direction: number) {
    const next = [...draftIds];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setDraftIds(next);
  }
  async function exportCsv(kind: "sets" | "sessions") {
    if (!analysisRequest) return;
    setExporting(true);
    setExportError(null);
    try {
      await csv(analysisRequest, kind);
    } catch (caught) {
      setExportError(errorMessage(caught));
    } finally {
      setExporting(false);
    }
  }
  const graphProps = { period, fetchedAt: visibleFetched ?? "" };
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <IconLogo />
          <div>
            <h1>Fitness Tracker</h1>
            <p>운동 분석</p>
          </div>
        </div>
        <div className="header-controls">
          <span className="local-badge">로컬 실행</span>
          <span
            className={`fetch-time ${previousData ? "previous" : ""}`}
            title={visibleFetched ?? undefined}
          >
            {busy
              ? "이전 조회 · 갱신 중"
              : error
                ? "갱신 실패 · 마지막 성공"
                : "Notion"}{" "}
            · {fetched(visibleFetched)} 조회
          </span>
          <button
            className="primary refresh-button"
            aria-label="새로고침"
            onClick={() => void refresh()}
            disabled={busy}
          >
            <RefreshCw size={16} className={busy ? "spin" : ""} />
            <span>{busy ? "조회 중" : "새로고침"}</span>
          </button>
          <div className="range-controls">
            <ModeButtons
              label="분석 기간"
              value={preferences.preset}
              items={[
                { key: "4w", label: "4주" },
                { key: "12w", label: "12주" },
                { key: "all", label: "전체" },
              ]}
              onChange={(value) =>
                setPreference({ preset: value as Preferences["preset"] })
              }
            />
            <button
              className={`icon-button ${preferences.preset === "custom" ? "selected" : ""}`}
              aria-label="사용자 기간"
              onClick={() => setPreference({ preset: "custom" })}
            >
              <CalendarDays size={18} />
            </button>
          </div>
          <label className="theme-control" title="테마">
            <span aria-hidden="true">
              {resolvedTheme === "dark" ? (
                <Moon size={18} />
              ) : (
                <Sun size={18} />
              )}
            </span>
            <select
              aria-label="테마"
              value={preferences.theme}
              onChange={(event) =>
                setPreference({ theme: event.target.value as Theme })
              }
            >
              <option value="system">시스템</option>
              <option value="light">밝게</option>
              <option value="dark">어둡게</option>
            </select>
          </label>
        </div>
      </header>
      {preferences.preset === "custom" && (
        <div className="custom-range">
          <label>
            시작일
            <input
              aria-label="시작일"
              type="date"
              max={currentDay}
              value={preferences.customStart}
              onChange={(event) =>
                setPreference({ customStart: event.target.value })
              }
            />
          </label>
          <span>—</span>
          <label>
            종료일
            <input
              aria-label="종료일"
              type="date"
              max={currentDay}
              value={preferences.customEnd}
              onChange={(event) =>
                setPreference({ customEnd: event.target.value })
              }
            />
          </label>
          <small>서울 날짜 · 시작일과 종료일 포함</small>
        </div>
      )}
      {(error || analysisError) && (
        <div role="alert" className="error-banner">
          <Info size={18} />
          <div>
            <strong>{error ? "Notion 갱신 실패" : "분석을 확인하세요"}</strong>
            <span>{error ?? analysisError}</span>
            {analysis && (
              <small>
                이전 조회 데이터를 표시합니다. 성공 시각은 유지됩니다.
              </small>
            )}
          </div>
          {error && <button onClick={() => void refresh()}>다시 시도</button>}
        </div>
      )}
      {needsFreshQuery && !busy && analysis && (
        <div className="freshness-note">
          <Clock3 size={14} />
          <span>
            마지막 조회는 이전 날짜입니다. 새로고침하면 현재 주간 범위와 원본을
            다시 확인합니다.
          </span>
        </div>
      )}
      {busy && analysis && (
        <div className="refresh-note" role="status">
          <LoaderCircle className="spin" size={14} /> 이전 데이터를 표시하며
          원본을 조회합니다
          {job?.progress ? ` · ${progressLabel(job.progress)}` : ""}
        </div>
      )}
      {!analysis ? (
        error ? (
          <section className="empty-screen">
            <Activity size={32} />
            <h2>연결 후 운동 기록을 보여줍니다</h2>
            <p>서버의 Notion 토큰과 원본 읽기 권한을 확인하세요.</p>
            <button className="primary" onClick={() => void refresh()}>
              다시 시도
            </button>
          </section>
        ) : (
          <Loading />
        )
      ) : (
        <>
          <section className="summary-grid" aria-label="운동 요약">
            <Summary
              icon={<CalendarDays />}
              label="이번 주 운동"
              value={`${numeric(analysis.summary.sessions, 0)}회`}
              note={`이번 주 ${shortDate(analysis.summary.current_week_start)} – ${shortDate(analysis.summary.current_week_end)}`}
            />
            <Summary
              icon={<Dumbbell />}
              label="기록 세트"
              value={`${numeric(analysis.summary.sets, 0)}세트`}
              note={`이번 주 ${shortDate(analysis.summary.current_week_start)} – ${shortDate(analysis.summary.current_week_end)}`}
            />
            <Summary
              icon={<Clock3 />}
              label="최근 운동"
              value={shortDate(analysis.summary.latest_completed_session_date)}
              note={
                analysis.summary.latest_completed_session_date ??
                "완료 기록 없음"
              }
              violet
            />
            <Summary
              icon={<BarChart3 />}
              label={rangeLabel}
              value={`${numeric(analysis.summary.range_sessions, 0)}회 / ${numeric(analysis.summary.range_sets, 0)}세트`}
              note={`${shortDate(analysis.meta.range_start)} – ${shortDate(analysis.meta.range_end)}`}
            />
          </section>
          <section className="core-section">
            <div className="section-heading">
              <div>
                <h2>핵심 종목</h2>
                <p>실제 수행과 추세를 확인하세요.</p>
              </div>
              <button className="secondary" onClick={openSettings}>
                <Settings2 size={16} /> 종목 설정
              </button>
            </div>
            <div
              className="mobile-core-tabs"
              role="tablist"
              aria-label="핵심 종목 선택"
            >
              {coreIds.map((id) => {
                const exercise = catalog?.exercises.find((ex) => ex.id === id);
                return (
                  <button
                    role="tab"
                    key={id}
                    aria-selected={selectedId === id}
                    className={selectedId === id ? "active" : ""}
                    onClick={() => changeCore(id)}
                  >
                    {displayLabel(exercise?.label ?? "접근할 수 없는 종목")}
                  </button>
                );
              })}
            </div>
            {coreIds.length === 0 ? (
              <div className="empty-core">
                <p>자주 보는 종목을 선택하세요.</p>
                <button onClick={openSettings}>종목 설정</button>
              </div>
            ) : (
              <div className="core-grid">
                {coreIds.map((id) => {
                  const core = analysis.core_exercises.find(
                    (ex) => ex.id === id,
                  );
                  if (!core) {
                    const library = catalog?.exercises.find(
                      (ex) => ex.id === id,
                    );
                    return (
                      <div
                        key={id}
                        className={`core-card ${selectedId === id ? "selected" : ""}`}
                        data-testid={`core-exercise-${id}`}
                      >
                        <button
                          className="core-title"
                          onClick={() => changeCore(id)}
                        >
                          <Dumbbell size={20} />
                          {displayLabel(
                            library?.label ?? "접근할 수 없는 종목",
                          )}
                        </button>
                        <p className="muted">선택 기간에 기록 없음</p>
                      </div>
                    );
                  }
                  return (
                    <CoreCard
                      key={id}
                      core={core}
                      selected={id === selectedId}
                      onSelect={() => changeCore(id)}
                      graphProps={graphProps}
                    />
                  );
                })}
              </div>
            )}
          </section>
          <div className="analysis-tools">
            <div className="analysis-period">
              <span>{rangeLabel}</span>
              <small>{period}</small>
              {isAnalyzing && (
                <LoaderCircle
                  size={14}
                  className="spin"
                  aria-label="분석 갱신 중"
                />
              )}
            </div>
            <button
              className={`filter-button ${filters.splits.length || filters.types.length ? "selected" : ""}`}
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen((current) => !current)}
            >
              <SlidersHorizontal size={15} /> 필터
              {filters.splits.length + filters.types.length > 0 && (
                <span>{filters.splits.length + filters.types.length}</span>
              )}
            </button>
          </div>
          {filtersOpen && (
            <section className="filter-panel" aria-label="분석 필터">
              <FilterGroup
                title="분할"
                values={catalog?.splits ?? catalog?.filters?.splits ?? []}
                selected={filters.splits}
                onChange={(values) =>
                  setFilters((current) => ({ ...current, splits: values }))
                }
              />
              <FilterGroup
                title="세트 유형"
                values={catalog?.set_types ?? catalog?.filters?.set_types ?? []}
                selected={filters.types}
                onChange={(values) =>
                  setFilters((current) => ({ ...current, types: values }))
                }
              />
              <p>이번 주 요약은 분석 필터와 독립적으로 유지됩니다.</p>
            </section>
          )}
          {(filters.splits.length > 0 || filters.types.length > 0) && (
            <div className="filter-chips">
              {filters.splits.map((value) => (
                <button
                  key={`split-${value}`}
                  onClick={() =>
                    setFilters((current) => ({
                      ...current,
                      splits: current.splits.filter((v) => v !== value),
                    }))
                  }
                >
                  {value || "미상"}
                  <X size={12} />
                </button>
              ))}
              {filters.types.map((value) => (
                <button
                  key={`type-${value}`}
                  onClick={() =>
                    setFilters((current) => ({
                      ...current,
                      types: current.types.filter((v) => v !== value),
                    }))
                  }
                >
                  {value || "미분류"}
                  <X size={12} />
                </button>
              ))}
              <button
                className="clear-filter"
                onClick={() => setFilters({ splits: [], types: [] })}
              >
                전체 해제
              </button>
            </div>
          )}
          <div className="charts-grid primary-charts">
            <section className="panel" data-testid="chart-C04">
              <div className="panel-actions">
                <ModeButtons
                  label="주간 훈련량 지표"
                  items={[
                    { key: "sessions", label: "운동 횟수" },
                    { key: "sets", label: "세트 수" },
                    { key: "volume", label: "볼륨" },
                  ]}
                  value={weeklyMode}
                  onChange={setWeeklyMode}
                />
              </div>
              <DataChart
                chart={chartOf(analysis, "C04", weeklyMode)}
                color={weeklyMode === "sets" ? undefined : "#0ca8a1"}
                kind="bar"
                {...graphProps}
              />
            </section>
            <section className="panel" data-testid="chart-C05">
              <DataChart
                chart={chartOf(analysis, "C05")}
                kind="horizontal"
                {...graphProps}
              />
            </section>
          </div>
          <div className="charts-grid secondary-charts">
            <section className="panel" data-testid="chart-C09">
              <div className="panel-actions">
                <ModeButtons
                  label="분할별 구성 지표"
                  items={[
                    { key: "sessions", label: "운동 횟수" },
                    { key: "sets", label: "세트 수" },
                  ]}
                  value={splitMode}
                  onChange={setSplitMode}
                />
              </div>
              <DataChart
                chart={chartOf(analysis, "C09", splitMode)}
                kind="donut"
                {...graphProps}
              />
            </section>
            <section className="panel" data-testid="chart-C07">
              <div className="panel-actions">
                <ModeButtons
                  label="종목별 빈도 범위"
                  items={[
                    { key: "top10", label: "상위 10" },
                    { key: "all", label: "전체" },
                  ]}
                  value={frequencyMode}
                  onChange={setFrequencyMode}
                />
              </div>
              <DataChart
                chart={chartOf(analysis, "C07", frequencyMode)}
                kind="bar"
                {...graphProps}
              />
            </section>
          </div>
          <details className="panel expandable" data-testid="chart-C06">
            <summary>
              <BarChart3 size={18} /> 종목별 세트 분포
              <ChevronDown size={18} />
            </summary>
            <div className="panel-actions">
              <ModeButtons
                label="종목별 세트 분포 범위"
                items={[
                  { key: "top10", label: "상위 10" },
                  { key: "all", label: "전체" },
                ]}
                value={setsMode}
                onChange={setSetsMode}
              />
            </div>
            <DataChart
              chart={chartOf(analysis, "C06", setsMode)}
              kind="horizontal"
              {...graphProps}
            />
          </details>
          <section className="panel detail-panel">
            <button
              className="detail-toggle"
              onClick={() => setDetailOpen((current) => !current)}
              aria-expanded={detailOpen}
            >
              <Dumbbell size={19} />
              <span>
                {displayLabel(selectedExercise?.label ?? "종목")} 상세 분석
              </span>
              <span className="detail-subtitle">
                추세 · 같은 중량 · 반복 구간 · 발전 지수
              </span>
              <ChevronDown size={18} />
            </button>
            {detailOpen && (
              <div className="detail-body">
                <div className="detail-filters">
                  <label>
                    종목
                    <select
                      aria-label="상세 종목"
                      value={selectedId}
                      onChange={(event) =>
                        setPreference({
                          selectedExerciseId: event.target.value,
                          coreIds: coreIds.includes(event.target.value)
                            ? coreIds
                            : [...coreIds, event.target.value],
                        })
                      }
                    >
                      {catalog?.exercises.map((exercise) => (
                        <option key={exercise.id} value={exercise.id}>
                          {displayLabel(exercise.label)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    측정 조건
                    <select
                      aria-label="측정 조건"
                      value={condition}
                      onChange={(event) => setCondition(event.target.value)}
                    >
                      <option value="">전체 조건 · 시리즈 분리</option>
                      <option value="__observed__">조건 미확인</option>
                      {selectedConditions.map((key) => (
                        <option key={key} value={key}>
                          {key}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selectedExercise?.pullup && (
                    <label>
                      풀업 방식
                      <select
                        aria-label="풀업 방식"
                        value={pullupMode}
                        onChange={(event) => setPullupMode(event.target.value)}
                      >
                        <option value="">전체 방식 · 분리</option>
                        <option value="bodyweight">맨몸</option>
                        <option value="added">추가 중량</option>
                        <option value="assisted">보조 중량</option>
                        <option value="unknown">방식 미확인</option>
                      </select>
                    </label>
                  )}
                </div>
                <div
                  className="detail-tabs"
                  role="tablist"
                  aria-label="종목 분석"
                >
                  {(selectedExercise?.pullup
                    ? ["C08", "C02", "C10"]
                    : ["C01", "C02", "C03", "C10"]
                  ).map((id) => (
                    <button
                      role="tab"
                      key={id}
                      aria-selected={detailTab === id}
                      onClick={() => setDetailTab(id)}
                    >
                      {TITLES[id]}
                    </button>
                  ))}
                </div>
                {(detailTab === "C02" ||
                  (selectedExercise?.pullup &&
                    ["added", "assisted"].includes(pullupMode))) && (
                  <label className="fixed-load">
                    중량
                    <select
                      aria-label="같은 중량"
                      value={
                        fixedLoad ||
                        String(chartOf(analysis, "C02").selected_load ?? "")
                      }
                      onChange={(event) => setFixedLoad(event.target.value)}
                    >
                      <option value="">중량 선택</option>
                      {availableLoads.map((load) => (
                        <option value={load} key={load}>
                          {numeric(load)} kg
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {detailTab === "C10" && (
                  <div className="baseline-control">
                    <div>
                      <strong>
                        {currentBaseline
                          ? "고정 기준 설정됨"
                          : "확인된 조건의 기준이 필요합니다"}
                      </strong>
                      <small>
                        {currentBaseline
                          ? `기준 ${numeric(currentBaseline.value)} · 세트 ${currentBaseline.source_set_id.slice(0, 8)} · 버전 ${currentBaseline.version}`
                          : "조건 미확인 기록으로 발전율을 만들지 않습니다."}
                      </small>
                    </div>
                    <button disabled={!canSetBaseline} onClick={setBaseline}>
                      현재 확인 기록을 기준으로
                    </button>
                    {currentBaseline && (
                      <button
                        className="text-button"
                        onClick={() =>
                          setPreference({
                            baselines: preferences.baselines.filter(
                              (b) => b !== currentBaseline,
                            ),
                          })
                        }
                      >
                        기준 해제
                      </button>
                    )}
                  </div>
                )}
                <div data-testid={`chart-${detailTab}`}>
                  <DataChart
                    chart={chartOf(analysis, detailTab)}
                    kind={detailTab === "C03" ? "bar" : "line"}
                    {...graphProps}
                  />
                </div>
                {selectedCore && (
                  <div className="comparison-grid">
                    <div>
                      <span>최근 실제 수행</span>
                      <strong>{actual(selectedCore.current_set)}</strong>
                      <small>
                        {shortDate(selectedCore.current_set?.date)} ·{" "}
                        {selectedCore.current_set?.condition_confirmed
                          ? "조건 확인"
                          : "조건 미확인"}
                      </small>
                    </div>
                    <div>
                      <span>직전 수행</span>
                      <strong>
                        {selectedCore.previous_set
                          ? actual(selectedCore.previous_set)
                          : "직전 기록 없음"}
                      </strong>
                      <small>
                        {selectedCore.sequence_ambiguous
                          ? "같은 날짜 · 순서 미확인"
                          : (selectedCore.comparison?.reason ??
                            shortDate(selectedCore.previous_set?.date))}
                      </small>
                    </div>
                    <div>
                      <span>기록상 최고 · 선택 기간</span>
                      <strong>
                        {selectedCore.record_best
                          ? actual(selectedCore.record_best)
                          : "—"}
                      </strong>
                      <small>
                        {selectedCore.record_best?.condition_confirmed
                          ? "조건 확인"
                          : "조건 미확인 · 관측값"}
                      </small>
                    </div>
                    <div>
                      <span>조건 확인 PR · 전체 과거</span>
                      <strong>
                        {selectedCore.verified_pr
                          ? `${numeric(selectedCore.verified_pr.value)} ${selectedCore.verified_pr.metric === "reps" ? "회" : "kg e1RM"}`
                          : "비교 가능한 기록 없음"}
                      </strong>
                      {selectedCore.comparison?.status === "ok" && (
                        <small>
                          {numeric(selectedCore.comparison.percent)}% 변화
                        </small>
                      )}
                    </div>
                  </div>
                )}
                {Boolean(selectedCore?.latest_sets?.length) && (
                  <details className="latest-set-list">
                    <summary>선택 종목의 최근 실제 세트</summary>
                    <SessionSets
                      session={{
                        id: "core-latest",
                        date:
                          selectedCore?.current_set?.date ??
                          selectedCore?.latest_sets?.[0]?.date ??
                          "",
                        split: null,
                        sets: selectedCore?.latest_sets ?? [],
                      }}
                    />
                  </details>
                )}
              </div>
            )}
          </section>
          <section className="panel recent-panel">
            <div className="section-heading">
              <div>
                <h2>
                  <Activity size={18} /> 최근 수행
                </h2>
                <p>선택 기간의 실제 세션과 세트</p>
              </div>
              <div className="export-actions">
                <button
                  onClick={() => void exportCsv("sessions")}
                  disabled={exporting}
                >
                  <Download size={15} /> 세션 CSV
                </button>
                <button
                  onClick={() => void exportCsv("sets")}
                  disabled={exporting}
                >
                  <Download size={15} /> 세트 CSV
                </button>
              </div>
            </div>
            {exportError && (
              <p role="alert" className="inline-error">
                {exportError}
              </p>
            )}
            {analysis.recent_sessions.length === 0 ? (
              <p className="muted empty-records">이 기간에 기록 없음</p>
            ) : (
              <>
                <div className="desktop-sessions">
                  <table>
                    <thead>
                      <tr>
                        <th>날짜</th>
                        <th>분할</th>
                        <th>운동</th>
                        <th>중량·반복 (기록 일부)</th>
                        <th>기록 세트</th>
                        <th>상세</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analysis.recent_sessions.map((session) => (
                        <tr key={session.id}>
                          <td>{session.date}</td>
                          <td>
                            <span className="split-dot" />
                            {session.split ?? "미상"}
                          </td>
                          <td>
                            {session.label ??
                              (session.sets
                                ?.map(
                                  (set) => set.exercise_label ?? set.exercise,
                                )
                                .filter(Boolean)
                                .filter(
                                  (label, index, all) =>
                                    all.indexOf(label) === index,
                                )
                                .join(" · ") ||
                                "세션 기록")}
                          </td>
                          <td className="session-preview">
                            {session.sets?.slice(0, 2).map((set) => (
                              <div
                                key={set.id}
                                title={displayLabel(
                                  set.exercise_label ??
                                    set.exercise ??
                                    "종목 미연결",
                                )}
                              >
                                {actual(set)}
                              </div>
                            ))}
                          </td>
                          <td>
                            {session.set_count ?? session.sets?.length ?? 0}세트
                          </td>
                          <td>
                            <button
                              aria-expanded={expandedSession === session.id}
                              onClick={() =>
                                setExpandedSession(
                                  expandedSession === session.id
                                    ? null
                                    : session.id,
                                )
                              }
                            >
                              실제 기록 <ChevronDown size={14} />
                            </button>
                            {safeNotionUrl(session.url) && (
                              <a
                                href={safeNotionUrl(session.url)}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`${session.date} 원본 열기`}
                              >
                                <ExternalLink size={14} />
                              </a>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mobile-sessions">
                  {analysis.recent_sessions.map((session) => (
                    <article className="session-card" key={session.id}>
                      <button
                        className="session-heading"
                        aria-expanded={expandedSession === session.id}
                        onClick={() =>
                          setExpandedSession(
                            expandedSession === session.id ? null : session.id,
                          )
                        }
                      >
                        <span>
                          <strong>{session.date}</strong>
                          <small>{session.split ?? "미상"}</small>
                        </span>
                        <span>
                          {session.set_count ?? session.sets?.length ?? 0}세트
                          <ChevronDown size={17} />
                        </span>
                      </button>
                      {expandedSession === session.id && (
                        <SessionSets session={session} />
                      )}
                    </article>
                  ))}
                </div>
                {expandedSession && (
                  <div className="desktop-set-details">
                    <SessionSets
                      session={analysis.recent_sessions.find(
                        (session) => session.id === expandedSession,
                      )!}
                    />
                  </div>
                )}
              </>
            )}
            {(analysis.recent_total ?? analysis.recent_sessions.length) > 3 && (
              <div className="recent-pagination">
                <small className="muted">
                  {analysis.recent_sessions.length} /{" "}
                  {analysis.recent_total ?? analysis.recent_sessions.length}회
                </small>
                {analysis.recent_sessions.length <
                  (analysis.recent_total ?? 0) &&
                  recentLimit < 1000 && (
                    <button
                      disabled={isAnalyzing}
                      onClick={() =>
                        setRecentLimit((limit) => Math.min(1000, limit + 10))
                      }
                    >
                      더 보기
                    </button>
                  )}
                {recentLimit > 3 && (
                  <button
                    disabled={isAnalyzing}
                    onClick={() => {
                      setRecentLimit(3);
                      setExpandedSession(null);
                    }}
                  >
                    접기
                  </button>
                )}
              </div>
            )}
          </section>
          <section className="quality-bar">
            <button
              onClick={() => setDiagnosticsOpen((current) => !current)}
              aria-expanded={diagnosticsOpen}
            >
              <Info size={16} />
              <strong>입력 점검</strong>
              <span>
                {diagnostics.length
                  ? diagnostics
                      .filter(([key]) => key !== "날짜 제외 · 전체 원본")
                      .slice(0, 2)
                      .map(([key, value]) => `${diagnosticLabel(key)} ${value}`)
                      .join(" · ")
                  : "진단 및 조회 정보"}
              </span>
              {Number(analysis.diagnostics.excluded_undated_sets ?? 0) > 0 && (
                <span className="undated-count">
                  전체 원본 · 날짜 제외{" "}
                  {Number(analysis.diagnostics.excluded_undated_sets)}세트
                </span>
              )}
              <ChevronDown size={16} />
            </button>
            {diagnosticsOpen && (
              <div className="diagnostics-details">
                <dl>
                  <div>
                    <dt>조회 완료</dt>
                    <dd>{visibleFetched}</dd>
                  </div>
                  <div>
                    <dt>원본 최종 수정</dt>
                    <dd>
                      {analysis.meta.source_max_last_edited_at ?? "확인 불가"}
                    </dd>
                  </div>
                  <div>
                    <dt>스냅샷</dt>
                    <dd>{analysis.meta.snapshot_id}</dd>
                  </div>
                  <div>
                    <dt>시간대</dt>
                    <dd>{analysis.meta.timezone}</dd>
                  </div>
                  {diagnosticEntries(analysis.diagnostics).map(
                    ([key, value]) => (
                      <div key={key}>
                        <dt>{diagnosticLabel(key)}</dt>
                        <dd>
                          {typeof value === "number"
                            ? value
                            : typeof value === "string"
                              ? value
                              : JSON.stringify(value)}
                        </dd>
                      </div>
                    ),
                  )}
                </dl>
              </div>
            )}
          </section>
          <HealthSection sources={analysis.optional_sources} />
        </>
      )}
      <footer className="app-footer">
        <span>Notion 원본 · 읽기 전용</span>
        <span>PC 실행 중에 사용 · 새로고침할 때 조회</span>
        <button
          className="text-button"
          onClick={() => setDiagnosticsOpen(true)}
        >
          조회 정보
        </button>
      </footer>
      {settingsOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSettingsOpen(false);
          }}
        >
          <div
            className="settings-dialog"
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="settings-title"
            onKeyDown={(event) => {
              if (event.key === "Escape") setSettingsOpen(false);
              if (event.key === "Tab") {
                const nodes = Array.from(
                  dialogRef.current?.querySelectorAll<HTMLElement>(
                    'button:not(:disabled),input,select,[tabindex="0"]',
                  ) ?? [],
                );
                const first = nodes[0],
                  last = nodes[nodes.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                  event.preventDefault();
                  last?.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first?.focus();
                }
              }
            }}
          >
            <div className="dialog-heading">
              <h2 id="settings-title">핵심 종목 설정</h2>
              <button
                className="icon-button"
                aria-label="설정 닫기"
                onClick={() => setSettingsOpen(false)}
              >
                <X size={20} />
              </button>
            </div>
            <p>종목과 순서는 이 브라우저에 저장됩니다.</p>
            <div className="selected-exercises">
              {draftIds.map((id, index) => {
                const exercise = catalog?.exercises.find((ex) => ex.id === id);
                const label = displayLabel(
                  exercise?.label ?? "접근할 수 없는 종목",
                );
                return (
                  <div className="selected-exercise" key={id}>
                    <span>
                      <strong>{label}</strong>
                      <small>
                        {exercise?.label !== label
                          ? `${exercise?.label} · `
                          : ""}
                        {displayLabel(exercise?.muscle ?? "주 근육 미상")}
                      </small>
                    </span>
                    <button
                      aria-label={`${label} 위로`}
                      disabled={index === 0}
                      onClick={() => reorder(index, -1)}
                    >
                      <ArrowUp size={16} />
                    </button>
                    <button
                      aria-label={`${label} 아래로`}
                      disabled={index === draftIds.length - 1}
                      onClick={() => reorder(index, 1)}
                    >
                      <ArrowDown size={16} />
                    </button>
                    <button
                      aria-label={`${label} 제거`}
                      onClick={() =>
                        setDraftIds((ids) =>
                          ids.filter((value) => value !== id),
                        )
                      }
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                );
              })}
            </div>
            <input
              className="exercise-search"
              aria-label="종목 검색"
              placeholder="종목 검색"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <div className="exercise-picker">
              {catalog?.exercises
                .filter((exercise) =>
                  `${exercise.label} ${displayLabel(exercise.label)}`
                    .toLowerCase()
                    .replace(/\s/g, "")
                    .includes(search.toLowerCase().replace(/\s/g, "")),
                )
                .map((exercise) => (
                  <label key={exercise.id}>
                    <input
                      type="checkbox"
                      checked={draftIds.includes(exercise.id)}
                      disabled={
                        !draftIds.includes(exercise.id) && draftIds.length >= 64
                      }
                      onChange={(event) =>
                        setDraftIds((ids) =>
                          event.target.checked
                            ? [...ids, exercise.id]
                            : ids.filter((id) => id !== exercise.id),
                        )
                      }
                    />
                    <span>
                      {displayLabel(exercise.label)}
                      <small>
                        {displayLabel(exercise.muscle ?? "미상")} ·{" "}
                        {exercise.id.slice(0, 8)}
                      </small>
                    </span>
                    {draftIds.includes(exercise.id) && <Check size={15} />}
                  </label>
                ))}
            </div>
            <div className="dialog-footer">
              <button
                className="secondary"
                onClick={() =>
                  setDraftIds(catalog?.default_core_exercise_ids ?? [])
                }
              >
                기본 종목
              </button>
              <button
                className="primary"
                onClick={() => {
                  setPreference({
                    coreIds: draftIds,
                    selectedExerciseId: draftIds.includes(selectedId)
                      ? selectedId
                      : (draftIds[0] ?? null),
                  });
                  setSettingsOpen(false);
                }}
              >
                설정 저장
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
function Summary({
  icon,
  label,
  value,
  note,
  violet = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  note: string;
  violet?: boolean;
}) {
  return (
    <article className="summary-card">
      <span className={`summary-icon ${violet ? "violet" : ""}`}>{icon}</span>
      <div>
        <h2>{label}</h2>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
    </article>
  );
}
function CoreCard({
  core,
  selected,
  onSelect,
  graphProps,
}: {
  core: CoreExercise;
  selected: boolean;
  onSelect: () => void;
  graphProps: { period: string; fetchedAt: string };
}) {
  return (
    <article
      className={`core-card ${selected ? "selected" : ""}`}
      data-testid={`core-exercise-${core.id}`}
    >
      <button className="core-title" onClick={onSelect} aria-pressed={selected}>
        <Dumbbell size={20} />
        <strong title={core.label}>{displayLabel(core.label)}</strong>
        <ChevronRight size={17} />
      </button>
      <div className="core-card-body">
        <div className="core-metrics">
          <strong className="actual-performance">
            {core.current_set
              ? core.pullup
                ? `${numeric(core.current_set.reps, 0)}회`
                : actual(core.current_set)
              : core.latest_sets?.length
                ? `${core.latest_sets.length}세트 기록`
                : "기록 없음"}
          </strong>
          {core.pullup ? (
            <span className="e1rm-label">
              원본 중량{" "}
              {core.current_set?.load === null ||
              core.current_set?.load === undefined
                ? "미기록"
                : `${numeric(core.current_set.load)} kg`}{" "}
              ·{" "}
              {core.current_set?.pullup_mode === "bodyweight"
                ? "맨몸"
                : core.current_set?.pullup_mode === "added"
                  ? "추가 중량"
                  : core.current_set?.pullup_mode === "assisted"
                    ? "보조 중량"
                    : "방식 미확인"}
            </span>
          ) : (
            <span className="e1rm-label">
              e1RM <b>{numeric(core.current_set?.e1rm_observed)} kg</b>
            </span>
          )}
          <span
            className={`condition-badge ${core.current_set?.condition_confirmed ? "confirmed" : ""}`}
          >
            {core.current_set?.condition_confirmed
              ? "조건 확인"
              : "조건 미확인 · 관측 추세"}
          </span>
          {!core.current_set && Boolean(core.latest_sets?.length) && (
            <small>대표값 계산 불가 · 상세에서 실제 수행 확인</small>
          )}
          <small className="core-date">
            {core.sequence_ambiguous
              ? "동일 날짜 · 순서 미확인"
              : (core.current_set?.date ?? "선택 기간에 기록 없음")}
          </small>
        </div>
        <div className="core-sparkline">
          <DataChart
            chart={{
              ...core.trend,
              title: core.label,
              unit: core.pullup ? "회" : "kg",
            }}
            kind="line"
            {...graphProps}
            compact
            color={selected ? "var(--accent)" : undefined}
            showAxes={selected}
            exportable={false}
          />
        </div>
      </div>
    </article>
  );
}
function FilterGroup({
  title,
  values,
  selected,
  onChange,
}: {
  title: string;
  values: string[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  return (
    <fieldset>
      <legend>{title}</legend>
      {values.map((value) => (
        <label key={value}>
          <input
            type="checkbox"
            checked={selected.includes(value)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...selected, value]
                  : selected.filter((v) => v !== value),
              )
            }
          />
          {value || "미분류"}
        </label>
      ))}
    </fieldset>
  );
}
function SessionSets({ session }: { session: Session }) {
  if (!session) return null;
  return (
    <div className="session-sets">
      <div className="sets-heading">
        <strong>
          {session.date} · {session.split ?? "미상"}
        </strong>
        {safeNotionUrl(session.url) && (
          <a href={safeNotionUrl(session.url)} target="_blank" rel="noreferrer">
            원본 열기 <ExternalLink size={13} />
          </a>
        )}
      </div>
      {session.sets?.length ? (
        session.sets.map((set, index) => (
          <div className="actual-set-row" key={set.id}>
            <span className="set-index">{index + 1}</span>
            <span className="set-exercise">
              {displayLabel(
                set.exercise_label ?? set.exercise ?? "종목 미연결",
              )}
              <small>
                {set.set_type ?? "미분류"} ·{" "}
                {set.condition_confirmed ? "조건 확인" : "조건 미확인"}
              </small>
            </span>
            <strong>{actual(set)}</strong>
            {safeNotionUrl(set.url) && (
              <a
                href={safeNotionUrl(set.url)}
                target="_blank"
                rel="noreferrer"
                aria-label={`${set.exercise_label ?? set.exercise ?? "세트"} 원본 열기`}
              >
                <ExternalLink size={13} />
              </a>
            )}
          </div>
        ))
      ) : (
        <p className="muted">기록 세트 없음</p>
      )}
    </div>
  );
}
function diagnosticLabel(key: string) {
  const labels: Record<string, string> = {
    unconfirmed_condition: "조건 미확인",
    volume_ineligible: "볼륨 계산 제외",
    unconfirmed_condition_sets: "조건 미확인",
    excluded_undated_sets: "날짜 제외 · 전체 원본",
    invalid_sessions: "유효하지 않은 세션",
    selected_sets: "선택 세트",
    unclassified_set_type: "미분류",
    unlinked_exercise: "종목 미연결",
    unknown_condition: "조건 미확인",
    invalid_numeric: "수치 이상",
    unclassified_sets: "미분류",
    unlinked_sets: "종목 미연결",
    unknown_conditions: "조건 미확인",
    missing_exercise: "종목 미연결",
    unconfirmed_conditions: "조건 미확인",
    orphan_session: "세션 미연결",
    ambiguous_session: "세션 관계 중복",
    invalid_reps: "반복 수 이상",
    invalid_load: "중량 이상",
    invalid_date: "날짜 이상",
    excluded_date_count: "날짜 제외",
    future_sessions: "미래 수행",
  };
  return labels[key] ?? (/[a-z]/i.test(key) ? issueLabel(key) : key);
}
function diagnosticEntries(
  diagnostics: Record<string, unknown>,
): [string, number][] {
  const period = diagnostics.period;
  if (period && typeof period === "object")
    return Object.entries(period)
      .map(
        ([key, value]) =>
          [
            key,
            typeof value === "number"
              ? value
              : Number((value as { count?: number })?.count ?? 0),
          ] as [string, number],
      )
      .concat([
        [
          "날짜 제외 · 전체 원본",
          Number(diagnostics.excluded_undated_sets ?? 0),
        ],
      ]);
  return Object.entries(diagnostics).filter(
    (entry): entry is [string, number] => typeof entry[1] === "number",
  );
}
function HealthSection({ sources }: { sources: Record<string, unknown> }) {
  const entries = Object.entries(sources ?? {}).filter(
    ([, source]) =>
      source &&
      typeof source === "object" &&
      (source as { state?: string }).state !== "not_configured",
  );
  if (!entries.length) return null;
  const configured = entries.filter(([, source]) => {
    const data = source as { state?: string; records?: unknown[] };
    return data.state === "failed" || Boolean(data.records?.length);
  });
  if (!configured.length) return null;
  const fieldLabels: Record<string, string> = {
    date: "날짜",
    weight_kg: "체중 (kg)",
    weight: "체중",
    body_fat_pct: "체지방 (%)",
    body_fat_percent: "체지방 (%)",
    calories_kcal: "열량 (kcal)",
    sleep_hr: "수면 (시간)",
    sleep_hours: "수면 (시간)",
    protein: "단백질",
    fat_g: "지방 (g)",
    carbs_g: "탄수화물 (g)",
    skeletal_muscle_kg: "골격근량 (kg)",
    height_cm: "키 (cm)",
    bmi: "BMI",
    steps: "걸음 수",
    status: "상태",
    active: "상태",
    target_weight_kg: "목표 체중 (kg)",
    start_date: "시작일",
    end_date: "종료일",
    body_fat: "체지방",
    muscle_mass_kg: "근육량 (kg)",
    title: "기록",
    name: "기록",
    calories: "열량 (kcal)",
    protein_g: "단백질 (g)",
    notes: "메모",
  };
  return (
    <details className="panel health-panel">
      <summary>
        <Heart size={18} /> 건강 기록<span>입력된 값만 표시</span>
        <ChevronDown size={18} />
      </summary>
      {configured.map(([name, source]) => {
        const data = source as {
          state?: string;
          fetched_at?: string;
          records?: Record<string, unknown>[];
          error?: string;
          using_previous_data?: boolean;
        };
        return (
          <div key={name}>
            <small>
              {(
                {
                  health: "건강 원본",
                  body: "신체 기록",
                  body_metrics: "신체 기록",
                  nutrition: "식단 기록",
                  diet: "식단 기록",
                  weight: "체중 기록",
                  sleep: "수면 기록",
                  goals: "목표 기록",
                  training_plan: "훈련 계획",
                  workout_plan: "훈련 계획",
                  weekly_reviews: "주간 회고",
                } as Record<string, string>
              )[name] ?? "선택 원본"}{" "}
              · {fetched(data.fetched_at)}
              {data.using_previous_data ? " · 이전 조회" : ""}
            </small>
            {data.state === "failed" && (
              <p className="inline-error">
                이 원본 조회 실패 ·{" "}
                {data.error ?? "원본 접근 권한을 확인하세요"}
              </p>
            )}
            <div className="health-records">
              {data.records?.map((row, index) => (
                <dl key={index}>
                  {Object.entries(
                    (row.values as Record<string, unknown>) ?? row,
                  )
                    .filter(
                      ([key, value]) =>
                        value !== null &&
                        !["id", "url", "last_edited_time"].includes(key),
                    )
                    .map(([key, value]) => (
                      <div key={key}>
                        <dt>
                          {fieldLabels[key] ??
                            (/^[a-z_]+$/i.test(key) ? "기록 값" : key)}
                        </dt>
                        <dd>{displayField(value)}</dd>
                      </div>
                    ))}
                </dl>
              ))}
            </div>
          </div>
        );
      })}
    </details>
  );
}
