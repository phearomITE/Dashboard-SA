import {defineConfig,devices} from '@playwright/test'

export default defineConfig({
  testDir:'./tests',
  timeout:30000,
  retries:0,
  reporter:'list',
  use:{baseURL:process.env.DASHBOARD_TEST_URL||'http://127.0.0.1:8080',trace:'retain-on-failure'},
  projects:[
    {name:'desktop-chromium',use:{...devices['Desktop Chrome']}},
    {name:'tablet-chromium',use:{...devices['iPad Pro 11'],browserName:'chromium'}},
    {name:'phone-chromium',use:{...devices['Pixel 7']}},
  ],
})
