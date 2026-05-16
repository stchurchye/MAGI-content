import { chromium } from 'playwright';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto('http://localhost:8080/', { waitUntil: 'domcontentloaded', timeout: 15000 });

const m = await page.evaluate(() => {
  const layout = document.querySelector('.home-layout');
  const sidebar = document.querySelector('.home-sidebar');
  const scroll = document.querySelector('.home-sidebar-scroll');
  const main = document.querySelector('.home-main');
  const r = (el) => el ? el.getBoundingClientRect() : null;
  const layoutR = r(layout);
  const sidebarR = r(sidebar);
  const mainR = r(main);
  const ratio = sidebarR && mainR ? (sidebarR.width / mainR.width).toFixed(3) : null;
  return {
    layoutH: layoutR?.height,
    viewportH: window.innerHeight,
    sidebarW: sidebarR?.width,
    mainW: mainR?.width,
    widthRatioLeftToRight: ratio,
    scrollOverflow: scroll ? scroll.scrollHeight > scroll.clientHeight : false,
    mainH: mainR?.height,
    layoutHvsMainH: layoutR && mainR ? Math.abs(layoutR.height - mainR.height) < 2 : null,
  };
});

console.log(JSON.stringify(m, null, 2));
const phi = 1.618;
const okRatio = m.widthRatioLeftToRight && Math.abs(parseFloat(m.widthRatioLeftToRight) - phi) < 0.08;
const okHeight = m.layoutHvsMainH && m.layoutH < window.innerHeight;
console.log({ goldenRatioOk: okRatio, heightLockedOk: m.layoutH < 800 && m.mainH <= m.layoutH });
await browser.close();
