import type { Page } from '@playwright/test';

/** Synthetic records only: these IDs and totals never come from the user's Notion. */
export const exercises = [
  { id: 'bench', label: '벤치 프레스', muscle: '가슴', pullup: false },
  { id: 'squat', label: '스쿼트', muscle: '하체', pullup: false },
  { id: 'deadlift', label: '데드 리프트', muscle: '등', pullup: false },
  { id: 'pullup', label: '풀업', muscle: '등', pullup: true },
  { id: 'long-machine', label: '한 팔씩 수행하는 케이블 시티드 로우 — 독립 손잡이 변형', muscle: '등', pullup: false },
];
export const fetchedAt = '2026-10-04T01:15:00+00:00';
export const previousFetchedAt = '2026-10-03T01:15:00+00:00';

function point(date: string, value: number | null, suffix: string, extra = {}) {
  return {
    date, label: date, value, sample_count: value === null ? 0 : 1,
    source_set_ids: value === null ? [] : [`set-${suffix}`],
    source_session_ids: value === null ? [] : [`session-${suffix}`], ...extra,
  };
}

function chart(title: string, metric: string, unit: string, points: object[], extra = {}) {
  const observed = ['e1rm', 'reps', 'load'].includes(metric);
  return {
    status: points.length ? 'ok' : 'empty', title, metric, unit,
    aggregation: '합성 테스트 스냅샷의 독립 집계',
    range: { start: '2026-07-13', end: '2026-10-04' },
    series: [{ key: observed ? 'observations' : 'completed', label: observed ? '조건 미확인 · 관측 추세' : '완료 기록', observation_only: observed, points }],
    exclusions: { count: 0, reasons: {} }, ...extra,
  };
}

function setFor(exerciseId: string, suffix: string, load: number | null, reps: number) {
  const exercise = exercises.find(item => item.id === exerciseId)!;
  const e1rm = load !== null && load > 0 && reps <= 12 && !exercise.pullup ? load * (1 + reps / 30) : null;
  return {
    id: `set-${suffix}`, exercise_id: exerciseId, exercise_ids: [exerciseId],
    exercise: exercise.label, date: '2026-10-02',
    load, reps, load_unit: 'kg', e1rm_observed: e1rm, set_type: 'Working', done: true,
    condition_key: null, condition_verified: false, condition_confirmed: false,
    pullup_mode: exercise.pullup ? 'unknown' : null,
    url: `https://www.notion.so/${'a'.repeat(32)}`,
    exclusions: [], exclusion_reasons: [],
  };
}

export const catalog = {
  snapshot_id: 'synthetic-new', exercises,
  default_core_exercise_ids: ['bench', 'squat', 'deadlift', 'pullup'],
  splits: ['Upper', 'Lower', 'Pull', '미분류'],
  set_types: ['Working', 'Top Set', 'Warm-up', '미분류'],
  conditions: [],
};

export function analysisFixture(request: any = {}, old = false) {
  const rangePreset = request.range?.preset ?? '12w';
  const filtered = (request.filters?.split_ids_or_values?.length ?? 0) > 0 || (request.filters?.set_types?.length ?? 0) > 0;
  const selected = request.detail_scope?.exercise_id ?? request.core_exercise_ids?.[0] ?? 'bench';
  const selectedExercise = exercises.find(item => item.id === selected) ?? exercises[0];
  const base = selected === 'squat' ? 136 : selected === 'deadlift' ? 177.6 : 105;
  const range = { start: rangePreset === '4w' ? '2026-09-07' : '2026-07-13', end: '2026-10-04' };
  const trend = [point('2026-07-22', base - 12, 'trend-a'), point('2026-08-31', base - 8, 'trend-b'), point('2026-09-30', base, 'trend-c')];
  const sessions = [
    { id: 'session-oct2', date: '2026-10-02', split: 'Pull', completed: true, url: `https://www.notion.so/${'b'.repeat(32)}`, sets: [setFor('deadlift', 'deadlift', 148, 6), setFor('pullup', 'pullup', null, 8), setFor('long-machine', 'long-machine', 37.5, 10)] },
    { id: 'session-sept30', date: '2026-09-30', split: 'Upper', completed: true, url: `https://www.notion.so/${'c'.repeat(32)}`, sets: [setFor('bench', 'bench', 90, 5)] },
    { id: 'session-sept28', date: '2026-09-28', split: 'Lower', completed: true, url: `https://www.notion.so/${'d'.repeat(32)}`, sets: [setFor('squat', 'squat', 120, 4)] },
    { id: 'session-sept25', date: '2026-09-25', split: 'Pull', completed: true, url: `https://www.notion.so/${'e'.repeat(32)}`, sets: [setFor('deadlift', 'previous', 142, 5)] },
  ];
  const categoryPoints = (entries: [string, string, number][]) => entries.map(([id, label, value]) => ({ category_id: id, label, value, sample_count: value, source_set_ids: [], source_session_ids: [] }));
  const weekly = [point('2026-09-14', 2, 'week-a'), point('2026-09-21', 3, 'week-b'), point('2026-09-28', 3, 'week-c')];
  const chart04 = chart('주간 훈련량', 'sessions', '회', weekly);
  const chart06 = chart('종목별 세트 분포', 'sets', '세트', categoryPoints([...exercises.map((item, index): [string, string, number] => [item.id, item.label, 16 - index * 2]), ['other', '기타', 11], ['unlinked', '미연결', 1]]));
  const chart07 = chart('종목별 빈도', 'sessions', '회', categoryPoints(exercises.map((item, index) => [item.id, item.label, 7 - index])));
  const chart09 = chart('분할별 구성', 'sessions', '회', categoryPoints([['Upper', 'Upper', 3], ['Lower', 'Lower', 2], ['Pull', 'Pull', 3]]), { denominator: 8 });
  const currentFor = (id: string) => sessions.flatMap(session => session.sets).find(set => set.exercise_id === id)!;
  return {
    meta: { snapshot_id: old ? 'synthetic-old' : 'synthetic-new', schema_version: 1, timezone: 'Asia/Seoul', range_start: range.start, range_end: range.end, fetched_at: old ? previousFetchedAt : fetchedAt, source_max_last_edited_at: '2026-10-02T10:00:00+00:00', refresh_state: 'succeeded', using_previous_data: old },
    summary: { current_week_start: '2026-09-28', current_week_end: '2026-10-04', sessions: 3, sets: 17, latest_completed_session_date: '2026-10-02', range_sessions: filtered ? 3 : rangePreset === '4w' ? 5 : 8, range_sets: filtered ? 12 : rangePreset === '4w' ? 36 : 72 },
    core_exercises: (request.core_exercise_ids ?? catalog.default_core_exercise_ids).map((id: string) => {
      const exercise = exercises.find(item => item.id === id)!;
      const current = currentFor(id);
      return { ...exercise, current_set: current, previous_set: id === 'deadlift' ? setFor('deadlift', 'previous', 142, 5) : null, record_best: current, verified_pr: null, comparison: { status: 'unavailable', reason: '조건 미확인', delta: null, percent: null }, trend: chart(`${exercise.label} 추세`, exercise.pullup ? 'reps' : 'e1rm', exercise.pullup ? '회' : 'kg', exercise.pullup ? [point('2026-09-25', 7, 'pullup-a'), point('2026-10-02', 8, 'pullup-b')] : trend) };
    }),
    charts: {
      C01: chart('핵심 종목 근력 추세', 'e1rm', 'kg', selectedExercise.pullup ? [] : trend),
      C02: chart('같은 중량의 반복', 'reps', '회', [point('2026-09-25', 4, 'fixed-a'), point('2026-10-02', 6, 'fixed-b')], { available_loads: [90, 100, 148], selected_load: 90 }),
      C03: { ...chart('반복 구간별 최고 중량', 'load', 'kg', []), status: 'ok', series: [{ key: '1-5', label: '1–5회', observation_only: true, points: [point('2026-09-01', 142, 'band-a')] }, { key: '6-8', label: '6–8회', observation_only: true, points: [point('2026-10-01', 148, 'band-b')] }, { key: '9-12', label: '9–12회', observation_only: true, points: [point('2026-09-01', null, 'band-c')] }] },
      C04: { ...chart04, modes: { sessions: chart04, sets: chart('주간 훈련량', 'sets', '세트', [point('2026-09-14', 20, 'sets-a'), point('2026-09-21', 35, 'sets-b'), point('2026-09-28', 17, 'sets-c')]), volume: chart('주간 훈련량', 'volume', 'kg·회', [point('2026-09-14', 1150, 'vol-a'), point('2026-09-21', 1580, 'vol-b'), point('2026-09-28', 2124, 'vol-c')]) } },
      C05: chart('근육별 세트 분포', 'sets', '세트', categoryPoints([['back', '등', 30], ['legs', '하체', 22], ['chest', '가슴', 15], ['unknown', '미상', 5]]), { denominator: 72 }),
      C06: { ...chart06, modes: { top10: chart06, all: chart06 } },
      C07: { ...chart07, modes: { top10: chart07, all: chart07 } },
      C08: chart('풀업 추세', 'reps', '회', [point('2026-09-25', 7, 'pullup-a'), point('2026-10-02', 8, 'pullup-b')], { available_modes: ['unknown'] }),
      C09: { ...chart09, modes: { sessions: chart09, sets: chart('분할별 구성', 'sets', '세트', categoryPoints([['Upper', 'Upper', 27], ['Lower', 'Lower', 22], ['Pull', 'Pull', 23]]), { denominator: 72 }) } },
      C10: chart('발전 지수', 'index', '지수', [], { status: 'unavailable', reason: '확인된 조건과 유효한 기준을 선택하세요', baseline_status: 'missing' }),
    },
    recent_sessions: sessions.slice(0, request.recent_limit ?? 3),
    recent_total: sessions.length,
    diagnostics: { unclassified_sets: 5, missing_exercise: 1, unconfirmed_conditions: 72, invalid_numbers: 0, unknown_dates: 0, counts: { unclassified: 5, missing_exercise: 1, unconfirmed_condition: 72 } },
    optional_sources: {},
  };
}

type MockOptions = {
  analysisConflictOnce?: boolean;
  hasCache?: boolean;
  refreshOutcome?: 'succeeded' | 'failed' | 'running';
  refreshPolls?: number;
  transformAnalysis?: (result: ReturnType<typeof analysisFixture>, request: any) => unknown;
};
export async function installDashboardAPI(page: Page, options: MockOptions = {}) {
  const calls = { refresh: 0, analysis: [] as any[], exports: [] as any[], status: 0, polls: 0 };
  const hasCache = options.hasCache ?? true;
  let completed = false;
  let conflictSent = false;
  const outcome = options.refreshOutcome ?? 'succeeded';
  const error = { code: 'notion_permission', message: 'Notion 접근 권한을 확인하세요' };
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(value) });
    if (path === '/api/status') {
      calls.status++;
      return json({ snapshot_id: completed ? 'synthetic-new' : hasCache ? 'synthetic-old' : null, fetched_at: completed ? fetchedAt : hasCache ? previousFetchedAt : null, refresh_state: completed ? 'succeeded' : 'idle', current_job_id: null, error: null, using_previous_data: hasCache && !completed, source_counts: hasCache || completed ? { sessions: 8, sets: 72, exercises: 5 } : {}, optional_sources: {} });
    }
    if (path === '/api/refresh' && request.method() === 'POST') {
      calls.refresh++;
      calls.polls = 0;
      return json({ id: 'synthetic-job', state: 'running', started_at: '2026-10-04T01:14:59+00:00' }, 202);
    }
    if (path === '/api/refresh/synthetic-job') {
      calls.polls++;
      const running = outcome === 'running' || calls.polls <= (options.refreshPolls ?? 0);
      if (!running && outcome === 'succeeded') completed = true;
      return json({ id: 'synthetic-job', state: running ? 'running' : outcome, snapshot_id: completed ? 'synthetic-new' : hasCache ? 'synthetic-old' : null, fetched_at: completed ? fetchedAt : hasCache ? previousFetchedAt : null, started_at: '2026-10-04T01:14:59+00:00', error: !running && outcome === 'failed' ? error : null, progress: { stage: 'query', source: 'sets', page: running ? 1 : 3, attempt: 1 } });
    }
    if (path === '/api/catalog') return json({ ...catalog, snapshot_id: completed ? 'synthetic-new' : 'synthetic-old' });
    if (path === '/api/analysis') {
      const body = request.postDataJSON();
      calls.analysis.push(body);
      if (options.analysisConflictOnce && !conflictSent) {
        conflictSent = true;
        completed = true;
        return json({ detail: '새 스냅샷', code: 'SNAPSHOT_CHANGED', snapshot_id: 'synthetic-new' }, 409);
      }
      if (!hasCache && !completed) return json({ detail: '사용 가능한 스냅샷 없음' }, 503);
      const result = analysisFixture(body, !completed);
      return json(options.transformAnalysis ? options.transformAnalysis(result, body) : result);
    }
    if (path === '/api/export/csv') {
      calls.exports.push(request.postDataJSON());
      return route.fulfill({ status: 200, contentType: 'text/csv; charset=utf-8', headers: { 'Content-Disposition': 'attachment; filename="fitness-sets.csv"' }, body: '\uFEFFdate,exercise,load,reps,observed_e1rm\r\n2026-10-02,데드 리프트,148,6,177.6\r\n' });
    }
    if (path.startsWith('/api/sessions/')) {
      const session = analysisFixture().recent_sessions.find(item => item.id === path.split('/').pop());
      return json(session ?? { detail: '세션 없음' }, session ? 200 : 404);
    }
    return json({ detail: `Unmocked endpoint: ${request.method()} ${path}` }, 404);
  });
  return calls;
}
