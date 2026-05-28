const { test, expect, chromium } = require('@playwright/test');

const enabled = process.env.TELEGRAM_WEB_SMOKE === '1';

test.describe('Telegram Web live smoke', () => {
  test.skip(!enabled, 'Set TELEGRAM_WEB_SMOKE=1 plus TEST_GROUP_NAME or TELEGRAM_WEB_CHAT_URL to run this live smoke test.');

  test('latest Botson warmup exposes private subscription path', async () => {
    const groupName = process.env.TEST_GROUP_NAME || '';
    const chatUrl = process.env.TELEGRAM_WEB_CHAT_URL || '';
    const profileDir = process.env.TELEGRAM_WEB_PROFILE_DIR || '.playwright/telegram-profile';
    const allowClick = process.env.TELEGRAM_WEB_ALLOW_CLICK === '1';

    if (!groupName && !chatUrl) {
      throw new Error('Set TEST_GROUP_NAME or TELEGRAM_WEB_CHAT_URL. Refusing to search production Telegram implicitly.');
    }

    const context = await chromium.launchPersistentContext(profileDir, { headless: false });
    const page = context.pages()[0] || await context.newPage();
    try {
      await page.goto(chatUrl || 'https://web.telegram.org/k/');
      await expect(page.locator('body')).toContainText(/Telegram|טלגרם|Search|חיפוש/i, { timeout: 30_000 });

      if (!chatUrl) {
        const search = page.locator('input[placeholder*="Search"], input[placeholder*="חיפוש"], [contenteditable="true"]').first();
        await search.click();
        await search.fill(groupName);
        await page.getByText(groupName, { exact: false }).first().click();
      }

      const body = page.locator('body');
      await expect(body).toContainText(/Botson|בוטסון|תזכורת|אני בפנים/i, { timeout: 30_000 });

      const subscriptionButton = page.getByText(/תזכורת בפרטי|פתחו תפריט אישי|אני בפנים|בטלו תזכורת/i).last();
      await expect(subscriptionButton).toBeVisible();

      if (allowClick) {
        await subscriptionButton.click();
        await expect(body).toContainText(/רשמנו|ביטלתי|תזכורת|Botson|בוטסון/i, { timeout: 30_000 });
      }
    } finally {
      await context.close();
    }
  });
});
