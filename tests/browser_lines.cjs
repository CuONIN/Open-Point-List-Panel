// 验证带线体路径的浏览器导航和筛选。 / Exercise line switching, scoped links and station filtering.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

async function main() {
  const origin = process.argv[2];
  const browser = await chromium.launch({ channel: "msedge", headless: true,
    ...(process.env.OPL_TEST_BROWSER ? { executablePath: process.env.OPL_TEST_BROWSER } : {}) });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(origin + "#issues?q=1405a");
    await page.locator(".desktop-issues tbody tr").first().waitFor();
    await page.getByLabel("切换线体").selectOption("/lines/pal_stator/");
    await page.getByText("PAL fixture", {exact:true}).first().waitFor();
    assert.match(page.url(), /\/lines\/pal_stator\/#issues$/);
    assert.equal(await page.locator("[data-picker=area], [data-picker=avo], [data-picker=machine]").count(), 0);
    await page.locator("[data-picker=station] summary").click();
    await page.locator("[data-picker=station] [data-pick=none]").click();
    await page.locator('[data-picker=station] input[value="201-051"]').check();
    await page.locator("[data-picker=station] [type=submit]").click();
    await page.waitForURL(/station=201-051/);
    const issue = page.locator(".desktop-issues [data-issue]").first();
    await issue.click();
    await page.locator("#detail-dialog[open]").getByText("PAL fixture",{exact:true}).waitFor();
    await page.keyboard.press("Escape");
    await page.locator('[data-nav=report]').click();
    await page.getByText(/工位汇总/, {exact:false}).last().waitFor();
    assert(!((await page.locator(".report-output").innerText()).includes("分期统计")));
    await page.getByLabel("切换线体").selectOption("/");
    await page.locator(".report-output").getByText(/分期统计/).or(page.locator(".report-output").filter({hasText:"分期统计"})).first().waitFor();
    await page.locator('[data-nav=issues]').click();
    await page.waitForURL(/q=1405a/);
    await page.getByLabel("切换线体").selectOption("/lines/pal_stator/");
    await page.waitForURL(/station=201-051/);
    await page.getByText("PAL fixture", {exact:true}).first().waitFor();
    assert.deepEqual(errors, []);
    console.log("PASS line switching, isolated filters and detail links, multi-station search and line reports");
  } finally { await browser.close(); }
}

main().catch(e=>{ console.error(e); process.exitCode=1; });
