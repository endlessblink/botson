const { test, expect } = require('@playwright/test');

test('planner generation follows the currently selected discussion topic', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel(/password|סיסמה/i).fill('playwright-secret');
  await page.getByRole('button').click();
  await expect(page).not.toHaveURL(/\/login$/);

  await page.goto('/planner');
  await expect(page.locator('#calendar')).toBeVisible();

  const captured = [];
  await page.route('**/api/generate-content', async (route) => {
    captured.push(JSON.parse(route.request().postData() || '{}'));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ text: 'שאלת בדיקה לערוץ החדש' }),
    });
  });

  const result = await page.evaluate(async () => {
    window.wizardState.type = 'discussion';
    window.wizardState.category = 'movies';
    window.wizardState.channelTopicId = '54';
    window.wizardState.date = '2099-01-01';
    window.wizardState.time = '20:00';

    const textarea = document.getElementById('create-text');
    textarea.value = 'תוכן ישן';
    const chip = document.createElement('button');
    chip.className = 'channel-chip';
    document.body.appendChild(chip);

    window.selectChannel(chip, 347);
    await window.aiGenerateContent();

    return {
      category: window.wizardState.category,
      topicId: window.wizardState.channelTopicId,
      text: textarea.value,
    };
  });

  expect(captured).toHaveLength(1);
  expect(captured[0]).toMatchObject({
    type: 'discussion',
    category: 'support',
    topic_id: '347',
    existing: 'תוכן ישן',
    scheduled_date: '2099-01-01',
    scheduled_time: '20:00',
  });
  expect(result).toMatchObject({
    category: 'support',
    topicId: '347',
    text: 'שאלת בדיקה לערוץ החדש',
  });
});
