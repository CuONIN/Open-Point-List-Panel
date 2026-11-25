// 在临时工作区验证弹窗、编辑与附件。 / Verify navigation and field operations on synthetic issues.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

async function verifyFilterLayers(page, origin) {
  await page.goto(origin + "#issues");
  const table = page.locator(".desktop-issues"), picker = page.locator("[data-picker=area]");
  await table.locator("tbody tr").first().waitFor();
  const search = page.getByRole("searchbox", {name:"搜索问题", exact:true});
  for (const width of [375, 1440]) {
    await page.setViewportSize({width, height:900});
    await search.fill("1405a,1405b");
    assert(await search.evaluate(input => {
      const icon = input.closest("label").querySelector("svg").getBoundingClientRect();
      const style = getComputedStyle(input);
      const textLeft = input.getBoundingClientRect().left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
      return icon.right + 4 <= textLeft;
    }), "The magnifier must leave room before the first typed character");
  }
  await search.fill("");
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
  await picker.locator("input[value=AGV]").uncheck();
  await picker.locator("input[value=AGV]").check();
  assert(await picker.locator("input[value=AGV]").isChecked());
}

async function verifyExcelFilters(page, origin) {
  const form=page.locator("#issue-filters"),edit=page.locator("#edit-dialog");
  const params=()=>new URLSearchParams(new URL(page.url()).hash.split("?")[1]);
  const picker=k=>form.locator('[data-picker="'+k+'"]');
  const box=(k,v)=>picker(k).locator('input[type=checkbox][value="'+v+'"]');
  const rows=()=>page.locator(".desktop-issues tbody .pin1 a").allTextContents();
  async function reload(action) {
    const request=page.waitForResponse(r=>new URL(r.url()).pathname==="/api/issues");
    await action();await (await request).finished();
    await page.waitForFunction(()=>document.querySelector("#main")?.getAttribute("aria-busy")!== "true");
  }
  async function open(k) {
    const ctl=picker(k);
    if(!await ctl.isVisible())await form.locator("[data-toggle-filters]").click();
    if(!await ctl.evaluate(el=>el.open))await ctl.locator("summary").click();
    return ctl;
  }
  const apply=k=>reload(()=>picker(k).locator("[type=submit]").click());
  const reset=()=>reload(()=>form.locator("[data-reset-filters]").click());
  await reload(()=>page.goto(origin+"#issues?limit=1"));
  assert.equal(await picker("area").locator("input:checked").count(),4);
  assert.equal(await box("state","closed").isChecked(),false);
  assert.equal(await box("state","zf_only").isChecked(),false);
  assert.equal(await box("responsible","ZF").isChecked(),false);
  assert.equal(await box("responsible","HAOSEN / ZF").isChecked(),true);
  await open("area");await box("area","AGV").uncheck();await apply("area");
  assert.deepEqual(params().getAll("area"),["S1","S2","General"]);
  await reload(()=>page.locator('[data-page="2"]').click());
  assert.equal(params().get("page"),"2");
  assert.equal(await box("area","AGV").isChecked(),false);
  const exportURL=page.waitForRequest(r=>new URL(r.url()).pathname==="/api/issues/export.xlsx");
  const download=page.waitForEvent("download");
  await page.locator('[data-export="xlsx"]').click();
  const exported=new URL((await exportURL).url()).searchParams;
  await download;
  assert.deepEqual(exported.getAll("area"),params().getAll("area"));
  assert.equal(exported.has("page"),false);
  assert.equal(params().get("page"),"2");

  await open("area");await picker("area").locator('[data-pick="none"]').click();await apply("area");
  assert.deepEqual(params().getAll("area"),["__none__"]);assert.deepEqual(await rows(),[]);
  await open("area");await picker("area").locator('[data-pick="all"]').click();await apply("area");
  assert.equal(params().has("area"),false);assert.equal(await picker("area").locator("input:checked").count(),4);
  await form.locator("input[name=q]").fill("1405a,1405b");
  await reload(()=>form.locator(".filters-row > button[type=submit]").click());
  assert.equal(await page.locator(".card-head h2 .tag.num").innerText(),"2");
  await reset();
  const cat=await open("category");
  await cat.locator("[data-option-search]").fill("未填写");
  await cat.locator('[data-pick="only"]').click();
  await apply("category");
  assert.deepEqual(params().getAll("category"),["__blank__"]);
  await reload(()=>form.locator('[data-scope="all"]').click());
  assert.deepEqual(new Set(await rows()),new Set(["#16","#18"]));
  assert(await box("state","closed").isChecked());assert(await box("responsible","ZF").isChecked());
  await reload(()=>form.locator('[data-scope="customer"]').click());
  assert(!await box("state","closed").isChecked());assert(await box("state","zf_only").isChecked());
  assert.deepEqual(await rows(),["#16"]);

  await reset();await open("state");await box("state","closed").check();await apply("state");
  assert.equal(params().get("scope"),"all");assert(!await box("responsible","ZF").isChecked());
  assert((await rows()).includes("#18"));assert(!(await rows()).includes("#16"));
  await open("state");await box("state","zf_only").check();await apply("state");
  assert(await box("responsible","ZF").isChecked());assert((await rows()).includes("#16"));
  await reset();const resp=await open("responsible");await resp.locator('[data-pick="none"]').click();
  await box("responsible","ZF").check();await apply("responsible");
  assert.equal(params().get("scope"),"all");assert(await box("state","zf_only").isChecked());assert(!await box("state","closed").isChecked());
  assert.deepEqual(await rows(),["#16"]);
  const stateButton=page.locator(".desktop-issues .work-cell [data-transition]");
  const id=await stateButton.getAttribute("data-transition");
  await stateButton.click();
  await edit.locator("[name=to]").waitFor();
  assert.deepEqual(await edit.locator("[name=to] option").evaluateAll(options=>options.map(o=>o.value).filter(Boolean)),["closed"]);
  await edit.locator("[name=to]").selectOption("closed");
  await edit.locator("[name=evidence]").waitFor();
  const required=await edit.locator("[required]").evaluateAll(inputs=>inputs.map(i=>i.name).filter(k=>k!=="to").sort());
  assert.deepEqual(required,["actor","day","evidence"]);
  await edit.locator("[name=actor]").fill("现场核查人");
  await edit.locator("[name=evidence]").fill("客户会议纪要 QA-16，确认人 AL");
  await edit.locator('[type="submit"]').click();await edit.waitFor({state:"hidden"});
  const detail=await (await page.request.get(origin+"api/issues/"+id)).json();
  assert.equal(detail.state,"closed");assert.equal(detail.events[0].actor,"现场核查人");
  assert(JSON.stringify(detail.events[0]).includes("QA-16"));
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
    await verifyExcelFilters(page, origin);
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
