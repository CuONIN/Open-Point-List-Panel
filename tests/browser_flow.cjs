// 在临时工作区验证弹窗、编辑与附件。 / Verify navigation and field operations on synthetic issues.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

async function verifyFilterLayers(page, origin) {
  await page.goto(origin + "#issues");
  const table = page.locator(".desktop-issues"), picker = page.locator("[data-picker=area]");
  await table.locator("tbody tr").first().waitFor();
  await picker.locator("summary").click();
  await table.evaluate(el => {el.scrollLeft = 140; el.scrollTop = 55;});
  await page.evaluate(() => window.scrollBy(0, 40));
  const hits = await page.evaluate(() => {
    const menu = document.querySelector("[data-picker=area] .multi-menu").getBoundingClientRect();
    return [".pin1", ".pin2"].map(selector => {
      const cell = document.querySelector(".issue-table thead " + selector).getBoundingClientRect();
      const left = Math.max(menu.left, cell.left), right = Math.min(menu.right, cell.right);
      const top = Math.max(menu.top, cell.top), bottom = Math.min(menu.bottom, cell.bottom);
      if (left >= right || top >= bottom) return "no overlap";
      const hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
      return hit?.closest(".multi-menu") ? "menu" : "table";
    });
  });
  assert.deepEqual(hits, ["menu", "menu"], "Opened filters must receive pointer events over both sticky headers");
  await picker.locator("input[value=AGV]").check();
  assert(await picker.locator("input[value=AGV]").isChecked());
}

async function main() {
  const origin = process.argv[2];
  const browser = await chromium.launch({channel: "msedge", headless: true,
    ...(process.env.OPL_TEST_BROWSER ? {executablePath: process.env.OPL_TEST_BROWSER} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const base = origin + "#issues?area=S1&area=S2&sort=number_asc&page=2&limit=1";
    await verifyFilterLayers(page, origin);
    await page.goto(base);
    const link = page.locator(".desktop-issues [data-issue]").first();
    const id = await link.getAttribute("data-issue");
    const drawer = page.locator("#detail-dialog"), edit = page.locator("#edit-dialog");
    await page.route("**/api/issues/" + id, route => route.fulfill({
      status: 503, contentType: "application/json", body: JSON.stringify({error:"测试读取中断"})
    }), {times: 1});
    await link.click();
    await drawer.locator("[data-retry-detail]").click();
    await drawer.getByRole("heading", {name: "问题 #1405b", exact: true}).waitFor();
    await page.keyboard.press("Escape");
    await page.waitForURL(base);
    assert.equal(await page.locator("[data-remove-filter=area]").count(), 2);

    await link.click();
    await drawer.locator("[data-transition]").click();
    await edit.locator("[name=to]").selectOption("planned");
    await edit.locator("[name=actor]").fill("测试记录");
    await edit.locator("[name=owner]").fill("测试电工");
    await edit.locator("[name=plan]").fill("2026-12-01");
    await edit.locator("[name=note]").fill("检查 T2 线号，保存原始测量记录。");
    await edit.locator("[type=submit]").click();
    await edit.waitFor({state:"hidden"});
    await drawer.getByRole("button", {name:"已列入计划"}).waitFor();

    await drawer.locator("[data-detail-tab=feedback]").click();
    await drawer.locator("[data-detail=feedback]").first().click();
    await edit.locator("[name=note]").fill("T2 端子复测完成");
    await edit.locator("[name=files]").setInputFiles({
      name:"T2.txt", mimeType:"text/plain", buffer:Buffer.from("T2 measured")
    });
    await edit.locator("[type=submit]").click();
    await edit.waitFor({state:"hidden"});
    await drawer.getByText("T2 端子复测完成", {exact:true}).waitFor();
    const evidence = await drawer.getByRole("link", {name:"T2.txt"}).first().getAttribute("href");
    assert.equal(await (await page.request.get(new URL(evidence, origin).href)).text(), "T2 measured");

    await drawer.locator("[data-detail-tab=relations]").click();
    await drawer.locator("[data-detail=relation]").click();
    await edit.locator("[name=kind]").selectOption("similar");
    await edit.locator("[data-related-q]").fill("1405a");
    await edit.locator("[data-related-search]").click();
    const targetRows = await (await page.request.get(origin + "api/issues?q=1405a&scope=all")).json();
    const targetId = String(targetRows.items.find(row => row.number === "1405a").id);
    await edit.locator("[name=other]").selectOption(targetId);
    await edit.locator("[name=note]").fill("现场复测涉及相同检查项");
    await edit.locator("[type=submit]").click();
    await edit.waitFor({state:"hidden"});
    await drawer.locator("[data-close-dialog]").click();
    await page.waitForURL(base);

    const related = page.locator('.desktop-issues .work-cell a[data-issue="' + targetId + '"]');
    await related.click();
    await drawer.getByRole("heading", {name:"问题 #1405a", exact:true}).waitFor();
    await page.keyboard.press("Escape");
    await page.waitForURL(base);
    await page.goto(base + "&issue=" + id);
    await drawer.locator("[data-detail-tab=relations]").click();
    await drawer.locator('a[data-issue="' + targetId + '"]').click();
    await drawer.getByRole("heading", {name:"问题 #1405a", exact:true}).waitFor();
    await drawer.locator("[data-close-dialog]").click();
    await page.waitForURL(base);
    await page.setViewportSize({width:375, height:812});
    const mobile = page.locator(".issue-mobile-card").first();
    await mobile.locator('.related-links a[data-issue="' + targetId + '"]').waitFor();
    assert.equal(await mobile.locator("[data-transition]").count(), 1);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.setViewportSize({width:1440, height:900});

    await page.locator("[data-nav=tasks]").click();
    await page.locator("[data-new-task]").click();
    await edit.locator("[name=title]").fill("测试节拍复测");
    await edit.locator("[name=detail_target]").fill("28");
    await edit.locator("[name=detail_after]").fill("27.5");
    await edit.locator("[data-task-search]").fill("1405b");
    await edit.locator("[data-search-btn]").click();
    await edit.locator("[data-task-candidate]").selectOption(id);
    await edit.locator("[data-task-link]").click();
    await edit.locator("[type=submit]").click();
    await edit.waitFor({state:"hidden"});
    await page.getByRole("heading",{name:"测试节拍复测"}).waitFor();
    await page.locator("main [data-issue]").first().click();
    await drawer.getByRole("heading",{name:"问题 #1405b",exact:true}).waitFor();
    const detail = await (await page.request.get(origin + "api/issues/" + id)).json();
    assert.equal(detail.state, "planned");
    assert.equal(detail.owner, "测试电工");
    assert.equal(detail.events[0].to_state, "planned");
    assert.deepEqual(errors, []);
    console.log("PASS sticky header hit testing, detail retry and return, meeting update, feedback, related issues, mobile and linked task");
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
