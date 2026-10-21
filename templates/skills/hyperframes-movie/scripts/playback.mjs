// Gate: does Studio play the film at normal speed? Opens Studio, presses play, and compares the
// player clock with wall time every 2.5 s, saving a screenshot every 10 s.
// Setup once (scratch dir): npm i puppeteer-core@25
// usage: node playback.mjs <studio url> <out dir> <seconds>
import puppeteer from "puppeteer-core";
import { mkdirSync } from "node:fs";

const [url, out, seconds] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME || "/usr/bin/google-chrome",
  headless: "new",
  args: ["--autoplay-policy=no-user-gesture-required"],
  defaultViewport: { width: 1920, height: 1200 },
});
const page = await browser.newPage();
await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });
await new Promise((r) => setTimeout(r, 6000));
const clock = () =>
  page.evaluate(() => (document.body.innerText.match(/(\d{1,2}):(\d{2})\s*\/\s*\d{1,2}:\d{2}/) || [null])[0]);
await page.mouse.click(960, 400);
await page.keyboard.press("Space");
const t0 = Date.now();
const samples = [];
for (let i = 0; i <= Math.ceil(Number(seconds) / 2.5) + 1; i++) {
  const wall = (Date.now() - t0) / 1000;
  const c = await clock();
  const m = c && c.match(/(\d{1,2}):(\d{2})/);
  const player = m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
  if (i > 0 && player < Number(seconds) - 1) samples.push([wall, player]); // i = 0 is startup latency
  console.log(`wall ${wall.toFixed(2)}s  player ${c}`);
  if (i % 4 === 2) await page.screenshot({ path: `${out}/play-${String(i).padStart(2, "0")}.png` });
  await new Promise((r) => setTimeout(r, 2500 - ((Date.now() - t0) % 2500)));
}
await browser.close();
// The clock shows whole seconds, so judge the rate (least-squares slope), not single readings,
// and call any two consecutive equal readings before the end a stall.
const n = samples.length;
const mx = samples.reduce((a, [x]) => a + x, 0) / n;
const my = samples.reduce((a, [, y]) => a + y, 0) / n;
const rate = samples.reduce((a, [x, y]) => a + (x - mx) * (y - my), 0) / samples.reduce((a, [x]) => a + (x - mx) ** 2, 0);
const stalls = samples.filter((s, i) => i > 0 && s[1] === samples[i - 1][1] && s[1] > 0).length;
const ok = n >= 4 && rate > 0.93 && rate < 1.07 && stalls === 0;
console.log(`${ok ? "PLAYBACK: PASS" : "PLAYBACK: FAIL"} (rate ${rate.toFixed(3)}× over ${n} samples, ${stalls} stalls)`);
