// 用真实浏览器检查周概览面板；合成响应避免依赖项目库。 / Exercise the real weekly panels with deterministic API fixtures.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");

const week = "2026-W48";
const events = Array.from({length: 24}, (_, index) => ({
  issue_id: 100 + index, number: String(100 + index), from_state: "planned", to_state: "working",
  day: "2026-11-24", station: "Leak Test", actor: "记录人", note: "第 " + (index + 1) + " 条更新"
}));
const plans = Array.from({length: 19}, (_, index) => ({
  id: 700 + index, number: String(700 + index), station: "Oil Filling", state: "planned",
  state_label: "已列入计划", owner: "现场负责人", plan_owner: "计划负责人", zh: "复测间隙",
  plan: "2026-11-26", is_week_plan: true, working_since: ""
}));
Object.assign(plans[1], {state:"working", state_label:"整改中", plan:"", is_week_plan:false, working_since:"2026-11-24"});
Object.assign(plans[2], {state:"working", state_label:"整改中", working_since:"2026-11-23"});
Object.assign(plans[3], {state:"working", state_label:"整改中", working_since:"2026-11-24", plan:"2026-W49", is_week_plan:false});
Object.assign(plans[4], {plan:week});

async function waitPosition(page, selector, target) {
  await page.waitForFunction(({selector, target}) => {
    const node = document.querySelector(selector);
    if (target === "top") return node.scrollTop === 0;
    if (target === "bottom") return node.scrollTop + node.clientHeight >= node.scrollHeight - 1;
    return node.scrollTop > 30;
  }, {selector, target});
}

async function verifyControls(page, titleId) {
  const selector = '[role="region"][aria-labelledby="'+titleId+'"]';
  const region = page.locator(selector);
  await region.scrollIntoViewIfNeeded();
  await region.focus();
  await page.keyboard.press("Home");
  await waitPosition(page, selector, "top");
  const positions = () => page.locator(".overview-scroll").evaluateAll(nodes => nodes.map(n => n.scrollTop));
  const before = await positions();
  await region.hover();
  await page.mouse.wheel(0, 250);
  await waitPosition(page, selector, "moved");
  const after = await positions();
  const otherIndex = titleId === "weekly-events-title" ? 1 : 0;
  assert.equal(after[otherIndex], before[otherIndex], "滚动应只移动目标面板");
  await page.keyboard.press("End");
  await waitPosition(page, selector, "bottom");
  assert(await region.evaluate(el => el.lastElementChild.lastElementChild.getBoundingClientRect().bottom
      <= el.getBoundingClientRect().bottom + 1), "键盘应能看到最后一条");
  await page.keyboard.press("Home");
  await waitPosition(page, selector, "top");

  // 拖动原生滚动条滑块，不直接改 scrollTop。 / Drag the actual native scrollbar thumb.
  const bar = await region.evaluate(el => {
    const box = el.getBoundingClientRect();
    const gutter = box.width - el.clientWidth;
    const thumb = el.clientHeight * el.clientHeight / el.scrollHeight;
    return {width:gutter, x:box.right - gutter / 2, y:box.top + thumb / 2, end:box.bottom - 3};
  });
  assert(bar.width >= 5, "原生滚动条需要可拖动的宽度");
  await page.mouse.move(bar.x, bar.y);
  await page.mouse.down();
  assert.equal(await region.evaluate(el => el.scrollTop), 0, "按下滑块不应触发整页跳转");
  await page.mouse.move(bar.x, bar.end, {steps:18});
  await page.mouse.up();
  await waitPosition(page, selector, "bottom");
}

async function main() {
  const origin = process.argv[2];
  assert(origin, "用法：node tests/browser_overview.cjs http://127.0.0.1:PORT/");
  // 保留系统滚动条使鼠标能真实命中滑块。 / Expose native scrollbars in headless mode for pointer testing.
  const browser = await chromium.launch({channel:"msedge", headless:true, ignoreDefaultArgs:["--hide-scrollbars"],
    ...(process.env.OPL_TEST_BROWSER ? {executablePath:process.env.OPL_TEST_BROWSER} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440, height:900}, reducedMotion:"reduce"});
    page.setDefaultTimeout(6000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/overview?*", route => {
      const selected = new URL(route.request().url()).searchParams.get("week");
      return route.fulfill({json:{week:selected,start:"2026-11-23",end:"2026-11-29",as_of:"2026-11-24",
        counts:{clarify_zf:2,approve_zf:3,confirm_internal:1},events:selected===week ? events : [],plans:selected===week ? plans : []}});
    });
    await page.goto(origin+"#overview?week="+week);
    const changes = page.getByRole("region", {name:"状态变更记录"});
    const agenda = page.getByRole("region", {name:"本周实施计划与整改"});
    await agenda.waitFor();
    const metrics = page.locator("a.metric");
    assert.equal(await metrics.count(),7);
    assert.equal(await metrics.nth(0).locator(".metric-caption").innerText(),"待澄清／确认");
    assert.equal(await metrics.nth(0).locator(".big-number").innerText(),"6");
    assert.equal(await metrics.nth(1).locator(".metric-caption").innerText(),"进入整改");
    await metrics.first().click();
    await page.waitForURL(/#issues\?/);
    assert.deepEqual(new URLSearchParams(new URL(page.url()).hash.split("?")[1]).getAll("state"),
      ["clarify_zf","approve_zf","confirm_internal"]);
    await page.goBack();
    await agenda.waitFor();
    for (const link of await page.locator("a.metric").all()) {
      const query = new URLSearchParams((await link.getAttribute("href")).split("?")[1]);
      assert.equal(query.get("mode"), "latest");
      assert.equal(query.get("week"), week);
      assert.equal(query.get("scope"), "all");
    }
    assert.equal(await changes.getByRole("listitem").count(), events.length);
    assert.equal(await agenda.getByRole("listitem").count(), plans.length);
    const items = agenda.getByRole("listitem");
    const dates = row => row.locator(".overview-dates").innerText().then(s => s.replace(/\s+/g," "));
    assert(!(await dates(items.nth(0))).includes("本周开工"));
    assert((await dates(items.nth(0))).includes("本周正式计划 2026-11-26"));
    assert((await dates(items.nth(1))).includes("本周开工 2026-11-24"));
    assert((await dates(items.nth(1))).includes("正式计划 待安排"));
    assert((await dates(items.nth(2))).includes("本周开工 2026-11-23"));
    assert((await dates(items.nth(2))).includes("本周正式计划 2026-11-26"));
    assert((await dates(items.nth(3))).includes("正式计划 2026 · CW49"));
    assert((await dates(items.nth(4))).includes("本周正式计划 2026 · CW48"));
    await verifyControls(page, "weekly-events-title");
    await verifyControls(page, "weekly-plans-title");

    await page.setViewportSize({width:375, height:812});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    const last = items.last();
    await last.scrollIntoViewIfNeeded();
    assert(await last.getByRole("link", {name:"#718"}).isVisible());
    await page.getByRole("link", {name:"查看全部", exact:true}).click();
    await page.waitForURL(/#issues\?/);
    const search = new URLSearchParams(new URL(page.url()).hash.split("?")[1]);
    assert.deepEqual(Object.fromEntries(search), {mode:"planned", week, scope:"all"});
    await page.goto(origin+"#overview?week=2026-W49");
    await page.getByText("本周暂无状态变更。", {exact:true}).waitFor();
    await page.getByText("本周暂无正式计划或新开工的整改项。", {exact:true}).waitFor();
    assert.equal(await page.locator(".overview-scroll").count(), 0);
    await page.goto(origin+"#report?week=2025-W02");
    const cutoff = page.locator("[data-report-cutoff]");
    await cutoff.fill("2025-01-08");
    await page.waitForURL(/as_of=2025-01-08/);
    await page.getByText(/统计截至 2025-01-08/).waitFor();
    assert((await page.locator(".report-output").innerText()).includes("二期（S2）"));
    await page.getByRole("button",{name:"上一周"}).click();
    await page.waitForURL(/week=2025-W01$/);
    assert.deepEqual(errors, []);
    console.log("PASS full weekly lists, native scrollbar drag, wheel/keyboard, start and plan dates, mobile and week drilldown");
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
