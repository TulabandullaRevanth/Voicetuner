// Drives the real UI to demonstrate STT: Create Voice -> Clone from audio ->
// upload sample -> Transcribe -> Reference Text fills from the local Whisper
// backend. Uses the home page so there is no background inspector form.
import { chromium } from 'playwright-core';

const URL = process.env.URL || 'http://localhost:5173/';
const SAMPLE = process.env.SAMPLE || '/tmp/demo_sample.wav';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('response', (r) => {
  if (r.url().includes('/transcribe')) console.log('  /transcribe ->', r.status());
});

await page.goto(URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);

await page.getByRole('button', { name: /create voice|new voice/i }).first().click();
const dlg = page.locator('[role="dialog"]').first();
await dlg.waitFor();
await page.waitForTimeout(600);

// clone source -> Upload tab -> set the audio file
await dlg.getByRole('button', { name: /clone from audio/i }).first().click();
await page.waitForTimeout(400);
await dlg.getByRole('tab', { name: /upload/i }).first().click().catch(() => {});
await page.waitForTimeout(500);
await dlg.locator('input[type="file"][accept="audio/*"]').first().setInputFiles(SAMPLE);
await page.waitForTimeout(1000);
await page.screenshot({ path: '/tmp/vb_stt_1_uploaded.png' });

// Transcribe — exact name avoids matching the dropzone container, whose
// accessible text also contains the word "Transcribe".
await dlg.getByRole('button', { name: 'Transcribe', exact: true }).first().click();

// wait for the Reference Text textarea (inside the dialog) to fill
const ta = dlg.getByPlaceholder(/exact text spoken/i).first();
await page.waitForFunction(
  (el) => el && el.value && el.value.trim().length > 3,
  await ta.elementHandle(),
  { timeout: 60000 },
);

console.log('REFERENCE TEXT (from STT):', JSON.stringify(await ta.inputValue()));
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/vb_stt_2_transcribed.png' });
await browser.close();
console.log('OK');
