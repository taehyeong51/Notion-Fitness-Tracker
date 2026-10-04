import { expect, test } from '@playwright/test';
import { installDashboardAPI } from './fixtures';

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test('a mobile chart opens and closes its value tooltip by touch', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.setFixedTime(new Date('2026-10-04T01:16:00Z'));
  await installDashboardAPI(page);
  await page.goto('/');
  const chart = page.getByTestId('chart-C04');
  await expect(chart).toBeVisible();
  await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
  await chart.locator('.recharts-bar-rectangle').last().tap();
  const tooltip = chart.locator('.chart-tooltip');
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('3 회');
  await tooltip.getByRole('button', { name: '툴팁 닫기', exact: true }).tap();
  await expect(tooltip).toHaveCount(0);
  expect(errors).toEqual([]);
});
