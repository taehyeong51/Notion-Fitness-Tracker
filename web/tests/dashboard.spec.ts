import { expect, test, type Page } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { exercises, installDashboardAPI, previousFetchedAt } from './fixtures';

const uncaughtErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date('2026-10-04T01:16:00Z') });
  const errors: string[] = [];
  uncaughtErrors.set(page, errors);
  page.on('pageerror', error => errors.push(error.message));
});
test.afterEach(async ({ page }) => {
  expect(uncaughtErrors.get(page), 'The UI must not throw uncaught JavaScript errors').toEqual([]);
});

async function openDashboard(page: Page, options: Parameters<typeof installDashboardAPI>[1] = {}) {
  const calls = await installDashboardAPI(page, options);
  await page.goto('/');
  await expect(page.getByText('이번 주 운동', { exact: true })).toBeVisible();
  return calls;
}

async function hasNoPageOverflow(page: Page) {
  const width = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(width.content).toBeLessThanOrEqual(width.viewport + 1);
}

async function ready(page: Page) {
  await expect(page.getByTestId('core-exercise-bench')).toBeVisible();
  await expect(page.getByTestId('chart-C04')).toBeVisible();
}

test.describe('layout and actual browser screenshots', () => {
  for (const viewport of [
    { name: 'desktop-1440', width: 1440, height: 900 },
    { name: 'desktop-1920', width: 1920, height: 1080 },
    { name: 'tablet-768', width: 768, height: 1024 },
    { name: 'mobile-390', width: 390, height: 844 },
    { name: 'mobile-430', width: 430, height: 932 },
    { name: 'small-320', width: 320, height: 844 },
  ]) {
    test(`${viewport.name}: all analytics fit the page width`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: viewport.width < 768 ? 'dark' : 'light', reducedMotion: 'reduce' });
      await openDashboard(page);
      await ready(page);
      await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
      const geometry = await page.evaluate(() => Object.fromEntries(
        ['.app-header', '.summary-grid', '.core-section', '.analysis-tools', '.primary-charts', '[data-testid="chart-C04"]', '[data-testid="chart-C05"]'].map(selector => {
          const rect = document.querySelector(selector)!.getBoundingClientRect();
          return [selector, { x: rect.x, y: rect.y, width: rect.width, height: rect.height, bottom: rect.bottom }];
        })
      ));
      const geometryPath = testInfo.outputPath('layout-geometry.json');
      await writeFile(geometryPath, JSON.stringify(geometry, null, 2));
      await testInfo.attach('layout-geometry.json', { path: geometryPath, contentType: 'application/json' });
      const screenshotPath = testInfo.outputPath(`${viewport.name}.png`);
      await page.screenshot({ path: screenshotPath, fullPage: true, animations: 'disabled' });
      await testInfo.attach(`${viewport.name}-${testInfo.project.name}.png`, { path: screenshotPath, contentType: 'image/png' });
      await hasNoPageOverflow(page);
      for (const label of ['이번 주 운동', '기록 세트', '최근 운동']) {
        const box = await page.getByRole('heading', { name: label, exact: true }).boundingBox();
        expect(box).not.toBeNull();
        expect(box!.y + box!.height).toBeLessThan(viewport.height);
      }
      if (viewport.width >= 1200) {
        for (const id of ['bench', 'squat', 'deadlift', 'pullup']) {
          const box = await page.getByTestId(`core-exercise-${id}`).boundingBox();
          expect(box!.y + box!.height).toBeLessThan(viewport.height);
        }
        const weekly = await page.getByTestId('chart-C04').boundingBox();
        const muscle = await page.getByTestId('chart-C05').boundingBox();
        expect(Math.abs(weekly!.y - muscle!.y)).toBeLessThan(5);
        expect(weekly!.y + weekly!.height).toBeLessThanOrEqual(viewport.height);
        expect(muscle!.y + muscle!.height).toBeLessThanOrEqual(viewport.height);
      } else if (viewport.width < 768) {
        const selected = await page.getByTestId('core-exercise-bench').boundingBox();
        expect(selected!.y + selected!.height).toBeLessThan(viewport.height);
        await page.getByRole('tab', { name: '풀업', exact: true }).click();
        await expect(page.getByTestId('core-exercise-pullup')).toBeVisible();
        await hasNoPageOverflow(page);
      }
    });
  }
});

test.describe('refresh and error states', () => {
  test('a snapshot conflict recovers by loading the latest catalog and analysis', async ({ page }) => {
    const calls = await openDashboard(page, { analysisConflictOnce: true });
    await ready(page);
    await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
    await expect.poll(() => calls.analysis.at(-1)?.snapshot_id).toBe('synthetic-new');
    expect(calls.analysis.some(request => request.snapshot_id === 'synthetic-old')).toBe(true);
    await page.locator('.quality-bar > button').click();
    await expect(page.locator('.diagnostics-details')).toContainText('synthetic-new');
    await expect(page.getByText('새 스냅샷', { exact: true })).toHaveCount(0);
  });

  test('first open without a cache starts a real refresh and fills the page only after success', async ({ page }) => {
    const calls = await installDashboardAPI(page, { hasCache: false, refreshPolls: 1 });
    await page.goto('/');
    await expect(page.getByText(/Notion 조회 중/)).toBeVisible();
    await ready(page);
    expect(calls.refresh).toBe(1);
    expect(calls.analysis.some(request => request.snapshot_id === 'synthetic-new')).toBeTruthy();
  });

  test('cached data stays visible during refresh and repeated clicks do not fan out jobs', async ({ page }) => {
    const calls = await openDashboard(page, { refreshOutcome: 'running' });
    await ready(page);
    await expect(page.getByText(/이전 조회.*갱신 중|갱신 중.*이전 조회/)).toBeVisible();
    const button = page.getByRole('button', { name: '새로고침', exact: true });
    await expect(button).toBeDisabled();
    expect(calls.refresh).toBe(1);
    await expect(page.getByTestId('core-exercise-bench')).toContainText('90');
  });

  test('failed refresh keeps the previous figures and success timestamp', async ({ page }) => {
    const calls = await openDashboard(page, { refreshOutcome: 'failed' });
    await ready(page);
    await expect(page.getByText(/갱신 실패/).first()).toBeVisible();
    await expect(page.getByText(/10\/03/).first()).toBeVisible();
    await expect(page.getByText(/Notion 접근 권한을 확인하세요/)).toBeVisible();
    const retry = page.getByRole('button', { name: /다시 시도|재시도/ }).first();
    await retry.click();
    await expect.poll(() => calls.refresh).toBe(2);
    await expect(page.getByTestId('core-exercise-bench')).toContainText('90');
  });

  test('first refresh failure has an error and no generated analytics', async ({ page }) => {
    await installDashboardAPI(page, { hasCache: false, refreshOutcome: 'failed' });
    await page.goto('/');
    await expect(page.getByText(/Notion 접근 권한을 확인하세요/)).toBeVisible();
    await expect(page.getByTestId('core-exercise-bench')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /다시 시도|재시도/ }).first()).toBeVisible();
  });

  test('manual refresh invokes the read endpoint again', async ({ page }) => {
    const calls = await openDashboard(page);
    await ready(page);
    await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '새로고침', exact: true }).click();
    await expect.poll(() => calls.refresh).toBe(2);
  });

  test('returning after Seoul midnight marks the old snapshot without background Notion polling', async ({ page }) => {
    const calls = await openDashboard(page);
    await ready(page);
    await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
    await page.clock.setSystemTime(new Date('2026-10-04T15:01:00Z'));
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await expect(page.getByText(/마지막 조회는 이전 날짜입니다/)).toBeVisible();
    expect(calls.refresh).toBe(1);
    await expect(page.getByTestId('core-exercise-bench')).toContainText('90');
  });
});

test.describe('filters, themes, settings and details', () => {
  test('recent records start at three and expand within the same snapshot without a Notion refresh', async ({ page }) => {
    const calls = await openDashboard(page);
    await ready(page);
    await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
    const panel = page.locator('.recent-panel');
    await expect(panel.locator('.desktop-sessions tbody tr')).toHaveCount(3);
    await expect(page.locator('.fetch-time')).toContainText('10/04');
    const refreshes = calls.refresh;
    const snapshot = calls.analysis.at(-1).snapshot_id;
    await panel.getByRole('button', { name: '더 보기', exact: true }).click();
    await expect(panel.locator('.desktop-sessions tbody tr')).toHaveCount(4);
    expect(calls.analysis.at(-1).snapshot_id).toBe(snapshot);
    expect(calls.refresh).toBe(refreshes);
    await panel.getByRole('button', { name: '접기', exact: true }).click();
    await expect(panel.locator('.desktop-sessions tbody tr')).toHaveCount(3);
  });

  test('a successful empty snapshot displays empty values without fabricated strength or PRs', async ({ page }) => {
    await openDashboard(page, { transformAnalysis: result => ({
      ...result,
      summary: { ...result.summary, sessions: 0, sets: 0, latest_completed_session_date: null, range_sessions: 0, range_sets: 0 },
      core_exercises: result.core_exercises.map(core => ({ ...core, current_set: null, previous_set: null, record_best: null, verified_pr: null, trend: { ...core.trend, status: 'empty', series: [] } })),
      charts: Object.fromEntries(Object.entries(result.charts).map(([id, result]) => [id, 'modes' in result ? { modes: Object.fromEntries(Object.entries(result.modes).map(([key, mode]) => [key, { ...mode, status: 'empty', series: [] }])) } : { ...result, status: 'empty', series: [] }])),
      recent_sessions: [],
    }) });
    await ready(page);
    await expect(page.getByTestId('core-exercise-bench')).toContainText('기록 없음');
    await expect(page.getByTestId('chart-C04')).toContainText('이 기간에 기록 없음');
    await expect(page.locator('.summary-grid')).toContainText('0회');
    await expect(page.locator('.summary-grid')).toContainText('완료 기록 없음');
    await expect(page.locator('main, .app-shell').first()).not.toContainText('NaN');
  });

  test('a confirmed baseline stores its source fingerprint and is invalidated when the source changes', async ({ page }) => {
    let sourceChanged = false;
    const calls = await openDashboard(page, { transformAnalysis: (result, request) => {
      const bench = result.core_exercises.find(core => core.id === 'bench')!;
      const confirmed = { ...bench.current_set, condition_confirmed: true, condition: 'bench-standard', condition_key: 'bench-standard', baseline_fingerprint: sourceChanged ? 'synthetic-revised-source' : 'synthetic-source-v1' };
      const hasBaseline = request.baselines?.length > 0;
      return {
        ...result,
        core_exercises: result.core_exercises.map(core => core.id === 'bench' ? { ...core, current_set: confirmed, record_best: confirmed } : core),
        charts: { ...result.charts, C10: {
          ...result.charts.C10,
          status: hasBaseline && !sourceChanged ? 'ok' : 'unavailable',
          reason: sourceChanged ? '기준 원본이 변경되었습니다. 기준을 다시 선택하세요.' : '확인된 기준을 선택하세요',
          series: hasBaseline && !sourceChanged ? [{ key: 'bench-standard', label: '벤치 프레스 · 조건 확인', condition_key: 'bench-standard', observation_only: false, points: [{ date: '2026-10-02', value: 100, sample_count: 1, source_set_ids: ['set-bench'], source_session_ids: ['session-sept30'] }] }] : [],
          invalid_baselines: sourceChanged ? [{ exercise_id: 'bench', reason: 'source_changed' }] : [],
        } },
      };
    } });
    await ready(page);
    await page.getByRole('button', { name: /벤치 프레스 상세 분석/ }).click();
    await page.getByRole('tab', { name: '발전 지수', exact: true }).click();
    const choose = page.getByRole('button', { name: '현재 확인 기록을 기준으로', exact: true });
    await expect(choose).toBeEnabled();
    await choose.click();
    await expect.poll(() => calls.analysis.at(-1)?.baselines[0]?.source_fingerprint).toBe('synthetic-source-v1');
    const baseline = calls.analysis.at(-1).baselines[0];
    expect(baseline).toMatchObject({ exercise_id: 'bench', condition_key: 'bench-standard', source_set_id: 'set-bench', metric: 'e1rm', value: 105 });
    expect(typeof baseline.version).toBe('string');
    await expect(page.getByTestId('chart-C10').getByRole('img', { name: /^발전 지수\./ })).toBeVisible();
    sourceChanged = true;
    await page.getByRole('button', { name: '4주', exact: true }).click();
    await expect(page.getByTestId('chart-C10')).toContainText('기준 원본이 변경되었습니다');
    await expect(page.getByTestId('chart-C10').getByRole('img', { name: /^발전 지수\./ })).toHaveCount(0);
    expect(calls.analysis.at(-1).baselines[0].source_fingerprint).toBe('synthetic-source-v1');
  });

  test('a configured optional source failure exposes its own stale timestamp and real values', async ({ page }) => {
    await openDashboard(page, { transformAnalysis: result => ({ ...result, optional_sources: {
      daily_health: { state: 'failed', fetched_at: previousFetchedAt, using_previous_data: true, error: '선택 건강 원본 읽기 권한 없음', records: [{ id: 'health-synthetic', values: { date: '2026-10-01', weight_kg: 72.1, body_fat_pct: null } }] },
    } }) });
    await ready(page);
    const health = page.locator('.health-panel');
    await expect(health).toBeVisible();
    await health.locator('summary').click();
    await expect(health).toContainText('이 원본 조회 실패');
    await expect(health).toContainText('선택 건강 원본 읽기 권한 없음');
    await expect(health).toContainText('10/03');
    await expect(health).toContainText('72.1');
    await expect(health).not.toContainText('체지방');
    await expect(page.getByTestId('chart-C04')).toBeVisible();
  });

  test('period and set filters recalculate locally without requesting Notion', async ({ page }) => {
    const calls = await openDashboard(page);
    await ready(page);
    await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
    const refreshes = calls.refresh;
    await page.getByRole('button', { name: '4주', exact: true }).click();
    await expect.poll(() => calls.analysis.at(-1)?.range.preset).toBe('4w');
    await page.getByRole('button', { name: '필터', exact: true }).click();
    await page.getByRole('group', { name: '세트 유형', exact: true }).getByRole('checkbox', { name: 'Working', exact: true }).check();
    await expect.poll(() => calls.analysis.at(-1)?.filters.set_types).toEqual(['Working']);
    expect(calls.refresh).toBe(refreshes);
    await expect(page.getByText('이번 주 운동', { exact: true })).toBeVisible();
  });

  test('system theme reacts to the OS and manual choices persist', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await openDashboard(page);
    await ready(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByLabel('테마', { exact: true }).selectOption('light');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.reload();
    await ready(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.getByLabel('테마', { exact: true }).selectOption('system');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('core exercise selection supports remove, reorder and long labels with persistence', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openDashboard(page);
    await ready(page);
    await page.getByRole('button', { name: '종목 설정', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: '스쿼트 제거', exact: true }).click();
    await dialog.getByRole('button', { name: '데드 리프트 위로', exact: true }).click();
    await dialog.getByLabel('종목 검색', { exact: true }).fill('케이블');
    await dialog.getByRole('checkbox', { name: new RegExp(exercises[4].label) }).check();
    await dialog.getByRole('button', { name: '설정 저장', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const tabNames = await page.getByRole('tablist', { name: '핵심 종목 선택', exact: true }).getByRole('tab').allTextContents();
    expect(tabNames[0]).toContain('데드 리프트');
    expect(tabNames.join(' ')).not.toContain('스쿼트');
    expect(tabNames.join(' ')).toContain('케이블');
    await hasNoPageOverflow(page);
    await page.reload();
    await expect(page.getByRole('tablist', { name: '핵심 종목 선택', exact: true }).getByRole('tab').first()).toContainText('데드 리프트');
    await expect(page.getByRole('tablist', { name: '핵심 종목 선택', exact: true }).getByRole('tab', { name: '스쿼트', exact: true })).toHaveCount(0);
  });

  test('all five detail charts are reachable, fixed load is sent and missing baseline is honest', async ({ page }) => {
    const calls = await openDashboard(page);
    await ready(page);
    await page.getByRole('button', { name: /벤치 프레스 상세 분석/ }).click();
    await page.getByLabel('측정 조건', { exact: true }).selectOption('__observed__');
    await expect.poll(() => calls.analysis.at(-1)?.detail_scope.condition_key).toBe('__observed__');
    for (const [name, id] of [['근력 추세', 'C01'], ['같은 중량의 반복', 'C02'], ['반복 구간별 최고 중량', 'C03'], ['발전 지수', 'C10']]) {
      await page.getByRole('tab', { name, exact: true }).click();
      await expect(page.getByTestId(`chart-${id}`)).toBeVisible();
      if (id === 'C02') {
        await expect(page.getByLabel('같은 중량', { exact: true })).toHaveValue('90');
        await page.getByLabel('같은 중량', { exact: true }).selectOption('148');
        await expect.poll(() => calls.analysis.at(-1)?.detail_scope.fixed_load).toBe(148);
      }
      if (id === 'C10') await expect(page.getByTestId('chart-C10')).toContainText(/기준|조건/);
    }
    await page.getByLabel('상세 종목', { exact: true }).selectOption('pullup');
    await expect(page.getByTestId('chart-C08')).toBeVisible();
    await expect(page.getByLabel('풀업 방식', { exact: true })).toBeVisible();
  });

  test('chart unit switches, readable data tables and keyboard controls work', async ({ page }) => {
    await openDashboard(page);
    await ready(page);
    const weekly = page.getByTestId('chart-C04');
    await weekly.getByRole('button', { name: '세트 수', exact: true }).click();
    await expect(weekly).toContainText('세트');
    await weekly.getByRole('button', { name: '볼륨', exact: true }).click();
    await expect(weekly).toContainText('kg·회');
    await weekly.getByText('수치 목록', { exact: true }).click();
    await expect(weekly.getByRole('listitem').first()).toBeVisible();
    const settings = page.getByRole('button', { name: '종목 설정', exact: true });
    await settings.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('button', { name: '설정 닫기', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(settings).toBeFocused();
  });

  test('mobile recent workout unfolds actual records and opens validated Notion URLs', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openDashboard(page);
    await ready(page);
    const session = page.locator('.mobile-sessions').getByRole('article').filter({ hasText: '2026-10-02' });
    await session.getByRole('button', { name: /세트|상세|펼치기/ }).click();
    await expect(session).toContainText('148');
    await expect(session).toContainText('6');
    await expect(session).toContainText('데드 리프트');
    await expect(session.getByRole('link', { name: '원본 열기', exact: true }).first()).toHaveAttribute('href', /^https:\/\/www\.notion\.so\//);
    await hasNoPageOverflow(page);
  });
});

test.describe('exports', () => {
  test('CSV uses the current period and produces a real download', async ({ page }) => {
    const calls = await openDashboard(page);
    await ready(page);
    await page.getByRole('button', { name: '4주', exact: true }).click();
    await expect.poll(() => calls.analysis.at(-1)?.range.preset).toBe('4w');
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: '세트 CSV', exact: true }).click();
    const download = await pending;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    await expect.poll(() => calls.exports.at(-1)?.range.preset).toBe('4w');
    expect(await download.failure()).toBeNull();
    const bytes = await readFile((await download.path())!);
    expect(bytes.subarray(0, 3).toString('hex')).toBe('efbbbf');
    expect(bytes.toString('utf8')).toContain('데드 리프트,148,6,177.6');
  });

  test('individual chart PNG renders in dark theme and downloads', async ({ page }) => {
    await openDashboard(page);
    await ready(page);
    await page.getByLabel('테마', { exact: true }).selectOption('dark');
    const pending = page.waitForEvent('download');
    await page.getByTestId('chart-C04').getByRole('button', { name: /주간 훈련량 PNG (저장|내보내기)/ }).click();
    const download = await pending;
    expect(download.suggestedFilename()).toMatch(/\.png$/);
    expect(await download.failure()).toBeNull();
    const bytes = await readFile((await download.path())!);
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
    expect(bytes.readUInt32BE(16)).toBeGreaterThanOrEqual(600);
    expect(bytes.readUInt32BE(20)).toBeGreaterThanOrEqual(470);
    expect(bytes.length).toBeGreaterThan(1000);
    const background = await page.evaluate(async (base64) => {
      const image = new Image();
      image.src = 'data:image/png;base64,' + base64;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d')!;
      context.drawImage(image, 0, 0);
      return Array.from(context.getImageData(0, 0, 1, 1).data);
    }, bytes.toString('base64'));
    expect(background[0]).toBeLessThan(80);
    expect(background[1]).toBeLessThan(80);
    expect(background[2]).toBeLessThan(80);
    expect(background[3]).toBe(255);
  });
});
