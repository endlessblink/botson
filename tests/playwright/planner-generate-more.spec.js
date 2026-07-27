// Per-day "+ עוד" in the AI-suggest board: the job takes 1-3 minutes, so the
// lane must show that it is running, allow cancelling, and make the newly
// arrived cards visible. Several days may run in parallel.
const { test, expect } = require('@playwright/test');

const WINDOW = { start: '2099-01-04', end: '2099-01-10' };

function suggestion(key, date, time, text) {
  return {
    key,
    date,
    time,
    message_type: 'discussion',
    topic_id: 54,
    topic_name: 'סרטים',
    category: 'movies',
    text,
    quality_failures: [],
    validation_failures: [],
  };
}

async function login(page) {
  await page.goto('/login');
  await page.getByLabel(/password|סיסמה/i).fill('playwright-secret');
  await page.getByRole('button').click();
  await expect(page).not.toHaveURL(/\/login$/);
  await page.goto('/planner');
  await expect(page.locator('#calendar')).toBeVisible();
}

// Seed the board directly instead of running a real week generation, which
// would call the LLM.
async function seedBoard(page, seeds) {
  await page.evaluate(({ win, seeds }) => {
    window._aiSuggestState.suggestions = seeds;
    window._aiSuggestState.checked = {};
    seeds.forEach((s) => { window._aiSuggestState.checked[s.key] = true; });
    window._aiSuggestState.skipReasons = [];
    window._aiSuggestState.boardMode = true;
    window._aiSuggestState.boardWindow = win;
    window.openAiSuggestModal();
    window._aiSuggestRenderCurrent();
  }, { win: WINDOW, seeds });
}

// Job control: the POST hands out an id, the status GET stays `running` until
// the test releases it.
async function stubAiSuggest(page, { resolvers }) {
  let nextId = 0;
  await page.route('**/api/weekplan/ai-suggest', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = JSON.parse(route.request().postData() || '{}');
    nextId += 1;
    const jobId = 'job-' + nextId;
    resolvers[jobId] = { status: 'running', targetDate: body.target_date, result: null };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: jobId, status: 'pending' }),
    });
  });
  await page.route('**/api/weekplan/ai-suggest/*', async (route) => {
    const url = route.request().url();
    const jobId = decodeURIComponent(url.split('/').pop().split('?')[0]);
    if (route.request().method() === 'POST') {
      if (resolvers[jobId]) resolvers[jobId].status = 'cancelled';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: jobId, status: 'cancelled' }),
      });
    }
    const job = resolvers[jobId] || { status: 'running' };
    const payload = { job_id: jobId, status: job.status };
    if (job.status === 'completed') payload.result = job.result || { suggestions: [], window: WINDOW };
    if (job.status === 'failed') payload.error = job.error || 'AI suggest failed';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

function lane(page, iso) {
  return page.locator('[data-ai-suggest-date="' + iso + '"]');
}

test('a day shows a running state while it generates, then the new cards land highlighted', async ({ page }) => {
  await login(page);
  const resolvers = {};
  await stubAiSuggest(page, { resolvers });
  await seedBoard(page, [suggestion('a1', '2099-01-04', '13:00', 'שאלה קיימת')]);

  const target = lane(page, '2099-01-04');
  await target.locator('button[data-generate-more-date]').click();

  // Running: lane marked, skeletons visible, the button is replaced by a
  // timer + cancel, and the existing card is untouched.
  await expect(target).toHaveClass(/is-loading/);
  await expect(target.locator('[data-ai-suggest-skeleton]')).toHaveCount(3);
  await expect(target.locator('button[data-generate-more-date]')).toHaveCount(0);
  await expect(target.locator('button[data-cancel-more-date]')).toHaveCount(1);
  await expect(target.locator('[data-day-timer]')).toBeVisible();
  await expect(target.locator('[data-suggest-card]')).toHaveCount(1);

  // Another day is still free to start — days are independent.
  await expect(lane(page, '2099-01-05').locator('button[data-generate-more-date]')).toBeEnabled();

  // The elapsed timer advances rather than sitting at 0:00.
  await expect(target.locator('[data-day-timer]')).not.toHaveText('0:00', { timeout: 4000 });

  const jobId = Object.keys(resolvers)[0];
  resolvers[jobId].status = 'completed';
  resolvers[jobId].result = {
    window: WINDOW,
    suggestions: [
      suggestion('a2', '2099-01-04', '17:00', 'שאלה חדשה אחת'),
      suggestion('a3', '2099-01-04', '19:00', 'שאלה חדשה שתיים'),
    ],
    skip_reasons: [],
  };

  await expect(target.locator('[data-suggest-card]')).toHaveCount(3);
  await expect(target).not.toHaveClass(/is-loading/);
  await expect(target.locator('[data-ai-suggest-skeleton]')).toHaveCount(0);
  await expect(target.locator('.ai-suggest-card.is-new')).toHaveCount(2);
  // The pre-existing card is not marked as new.
  await expect(target.locator('[data-suggest-card="a1"]')).not.toHaveClass(/is-new/);
});

test('two days generate in parallel and finish independently', async ({ page }) => {
  await login(page);
  const resolvers = {};
  await stubAiSuggest(page, { resolvers });
  await seedBoard(page, [suggestion('a1', '2099-01-04', '13:00', 'שאלה קיימת')]);

  const sun = lane(page, '2099-01-04');
  const mon = lane(page, '2099-01-05');
  await sun.locator('button[data-generate-more-date]').click();
  await expect(sun).toHaveClass(/is-loading/);
  await mon.locator('button[data-generate-more-date]').click();
  await expect(mon).toHaveClass(/is-loading/);
  await expect(sun).toHaveClass(/is-loading/);

  const ids = Object.keys(resolvers);
  expect(ids).toHaveLength(2);
  const sunJob = ids.find((id) => resolvers[id].targetDate === '2099-01-04');
  const monJob = ids.find((id) => resolvers[id].targetDate === '2099-01-05');
  expect(sunJob && monJob).toBeTruthy();

  resolvers[monJob].status = 'completed';
  resolvers[monJob].result = {
    window: WINDOW,
    suggestions: [suggestion('b1', '2099-01-05', '20:00', 'שאלה של שני')],
  };

  // Monday lands while Sunday keeps running.
  await expect(mon).not.toHaveClass(/is-loading/);
  await expect(mon.locator('[data-suggest-card]')).toHaveCount(1);
  await expect(sun).toHaveClass(/is-loading/);

  resolvers[sunJob].status = 'completed';
  resolvers[sunJob].result = {
    window: WINDOW,
    suggestions: [suggestion('a2', '2099-01-04', '17:00', 'שאלה של ראשון')],
  };
  await expect(sun).not.toHaveClass(/is-loading/);
  await expect(sun.locator('[data-suggest-card]')).toHaveCount(2);
});

test('a failed day keeps the board and offers a retry in that lane', async ({ page }) => {
  await login(page);
  const resolvers = {};
  await stubAiSuggest(page, { resolvers });
  await seedBoard(page, [
    suggestion('a1', '2099-01-04', '13:00', 'שאלה קיימת'),
    suggestion('b1', '2099-01-05', '13:00', 'שאלה של שני'),
  ]);

  const target = lane(page, '2099-01-04');
  await target.locator('button[data-generate-more-date]').click();
  await expect(target).toHaveClass(/is-loading/);

  const jobId = Object.keys(resolvers)[0];
  resolvers[jobId].status = 'failed';
  resolvers[jobId].error = 'boom';

  await expect(target.locator('[data-lane-note]')).toContainText('boom');
  // Board survives: both lanes keep their cards.
  await expect(target.locator('[data-suggest-card]')).toHaveCount(1);
  await expect(lane(page, '2099-01-05').locator('[data-suggest-card]')).toHaveCount(1);
  await expect(target.getByRole('button', { name: 'נסה שוב' })).toBeVisible();
});

test('cancelling a running day returns the lane to idle', async ({ page }) => {
  await login(page);
  const resolvers = {};
  await stubAiSuggest(page, { resolvers });
  await seedBoard(page, [suggestion('a1', '2099-01-04', '13:00', 'שאלה קיימת')]);

  const target = lane(page, '2099-01-04');
  await target.locator('button[data-generate-more-date]').click();
  await expect(target).toHaveClass(/is-loading/);

  await target.locator('button[data-cancel-more-date]').click();
  await expect(target).not.toHaveClass(/is-loading/);
  await expect(target.locator('[data-ai-suggest-skeleton]')).toHaveCount(0);
  await expect(target.locator('button[data-generate-more-date]')).toBeEnabled();
  await expect(target.locator('[data-suggest-card]')).toHaveCount(1);
});

test('a day with nothing new says so in the lane', async ({ page }) => {
  await login(page);
  const resolvers = {};
  await stubAiSuggest(page, { resolvers });
  await seedBoard(page, [suggestion('a1', '2099-01-04', '13:00', 'שאלה קיימת')]);

  const target = lane(page, '2099-01-04');
  await target.locator('button[data-generate-more-date]').click();
  await expect(target).toHaveClass(/is-loading/);

  const jobId = Object.keys(resolvers)[0];
  resolvers[jobId].status = 'completed';
  resolvers[jobId].result = { window: WINDOW, suggestions: [] };

  await expect(target.locator('[data-lane-note]')).toContainText('לא נמצאו הצעות חדשות');
  await expect(target.locator('[data-suggest-card]')).toHaveCount(1);
});
