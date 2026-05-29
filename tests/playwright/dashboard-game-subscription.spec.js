const { test, expect } = require('@playwright/test');

test('dashboard exposes game warmup in relevant topic without public reminder row', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel(/password|סיסמה/i).fill('playwright-secret');
  await page.getByRole('button').click();
  await expect(page).not.toHaveURL(/\/login$/);

  await page.goto('/planner');
  await expect(page.locator('#calendar')).toBeVisible();

  const events = await page.evaluate(async () => {
    const response = await fetch('/api/calendar?start=2099-01-01&end=2099-01-02');
    if (!response.ok) throw new Error(`calendar api failed: ${response.status}`);
    return response.json();
  });

  const warmups = events.filter((event) => event.extendedProps?.messageType === 'trivia_warmup_rsvp');
  expect(warmups).toHaveLength(1);
  expect(warmups[0].extendedProps.channelTopicId).toBe(1517);
  expect(warmups[0].extendedProps.gamePayload.warmup_marker).toBe('warmup-rsvp:playwright-dashboard');

  const games = events.filter((event) => event.extendedProps?.messageType === 'trivia_round');
  expect(games).toHaveLength(1);
  expect(games[0].extendedProps.channelTopicId).toBe(4037);
  expect(games[0].extendedProps.gamePayload.warmup_marker).toBe('warmup-rsvp:playwright-dashboard');

  const publicReminders = events.filter((event) => event.extendedProps?.messageType === 'warmup_reminder');
  expect(publicReminders).toHaveLength(0);
});
