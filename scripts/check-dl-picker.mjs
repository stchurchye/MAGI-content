import { chromium } from 'playwright';

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto('http://localhost:8080/', { waitUntil: 'domcontentloaded', timeout: 15000 });
await page.waitForSelector('.dl-picker-option', { state: 'attached' });

const summary = page.locator('#downloader-summary');
await summary.click();

const options = page.locator('.dl-picker-option');
const count = await options.count();
const labels = [];
for (let i = 0; i < count; i++) {
  labels.push((await options.nth(i).innerText()).trim());
}

console.log(JSON.stringify({ optionCount: count, labels, visible: await options.first().isVisible() }));

await options.nth(1).click();
const selected = await page.locator('#downloader-input').inputValue();
const summaryText = await summary.innerText();
console.log(JSON.stringify({ afterClick: { selected, summaryText } }));

await browser.close();
