const { test, expect } = require('@playwright/test');

// The rephrase action is the middle path between approving a suggestion and
// rejecting it: keep the idea, redo the wording. This exercises the real
// /api/rephrase-anchors endpoint (so a settings.yaml typo fails here) and
// stubs only the model call.
test('rephrasing a planner suggestion swaps the text in place and can be undone', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel(/password|סיסמה/i).fill('playwright-secret');
  await page.getByRole('button').click();
  await expect(page).not.toHaveURL(/\/login$/);

  await page.goto('/planner');
  await expect(page.locator('#calendar')).toBeVisible();

  const captured = [];
  await page.route('**/api/generate', async (route) => {
    captured.push(JSON.parse(route.request().postData() || '{}'));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ content: 'ניסוח חדש', quality_failures: [] }),
    });
  });

  // Seed one suggestion and render the board — no generation job needed.
  await page.evaluate(() => {
    window._aiSuggestState.suggestions = [{
      key: 'k1',
      message_type: 'discussion',
      category: 'movies',
      topic_id: 54,
      date: '2099-01-01',
      time: '12:00',
      text: 'הטקסט המקורי',
      quality_failures: [],
    }];
    window.openAiSuggestModal();
    window._aiSuggestRenderCurrent();
  });

  const card = page.locator('[data-suggest-card="k1"]');
  await expect(card).toContainText('הטקסט המקורי');

  // The button label comes from config — if it renders empty, the copy keys
  // or the anchors endpoint are broken.
  const rephraseBtn = card.locator('button[data-rephrase-key="k1"]');
  await expect(rephraseBtn).not.toHaveText('');

  await rephraseBtn.click();
  const modal = page.locator('#rephrase-modal');
  await expect(modal).toBeVisible();
  await expect(modal.locator('#rephrase-original')).toContainText('הטקסט המקורי');

  const chips = modal.locator('#rephrase-anchors button');
  expect(await chips.count()).toBeGreaterThan(0);
  const firstAnchorKey = await chips.first().getAttribute('data-anchor-key');
  await chips.first().click();
  await modal.locator('#rephrase-notes').fill('בלי אמוג׳י');
  await modal.locator('#rephrase-ok').click();

  await expect(card).toContainText('ניסוח חדש');
  expect(captured).toHaveLength(1);
  expect(captured[0]).toMatchObject({
    field: 'discussion',
    mode: 'rewrite',
    existing: 'הטקסט המקורי',
    category: 'movies',
    anchors: [firstAnchorKey],
    instructions: 'בלי אמוג׳י',
  });

  // Undo restores the original wording — the operator can always back out.
  await card.locator('button[data-rephrase-undo-key="k1"]').click();
  await expect(page.locator('[data-suggest-card="k1"]')).toContainText('הטקסט המקורי');
});
