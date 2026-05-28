const fs = require('fs');

const systemChromium = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);

module.exports = {
  testDir: './tests/playwright',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:3978',
    trace: 'retain-on-failure',
    launchOptions: systemChromium ? { executablePath: systemChromium } : undefined
  },
  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER === '1' ? undefined : {
    command: 'uv run python scripts/playwright_dashboard_server.py',
    url: 'http://127.0.0.1:3978/login',
    reuseExistingServer: false,
    timeout: 20_000,
    env: {
      DASHBOARD_PORT: '3978',
      DASHBOARD_PASSWORD: 'playwright-secret',
      DASHBOARD_SECRET: 'playwright-session-secret',
      DB_PATH: './data/playwright-dashboard.db',
      BOT_USERNAME: 'BotsonPlaywrightBot',
      GROUP_ID: '-1001111111111',
      TEST_GROUP_ID: '-1002222222222',
      PYTHONPATH: '.'
    }
  }
};
