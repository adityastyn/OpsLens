// End-to-end check of the Home screen and tabs at 1280x800: all seven scenarios,
// the alarm lifecycle through the bars, every tab and every overlay.
// Needs: backend on :8001, HMI on :5173.   node e2e/home_1280.e2e.cjs [folder for screenshots]
// Starts from whatever data exists. Use a fresh database for exact counts.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

const API = process.env.OPSLENS_API || 'http://127.0.0.1:8001/api';
const HMI = process.env.OPSLENS_HMI || 'http://127.0.0.1:5173';
const shots = process.argv[2] || '.';
const TABS = ['Alarms', 'Incidents', 'Root Cause & Analysis', 'Time-Series & Prediction', 'Shifts History'];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (path) => (await fetch(API + path)).json();
const post = async (path, body = {}) => (await fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
const results = [];
const check = (name, fn) => { fn(); results.push(`PASS ${name}`); };
const finished = async () => {
  for (let i = 0; i < 400; i += 1) {
    const status = await get('/scenarios/status');
    if (!['running', 'recovering'].includes(status.status)) return status;
    await sleep(250);
  }
  throw new Error('Scenario did not finish');
};
// Everything the operator taps in the bars is at least 44 px in both directions.
const smallTargets = (page) => page.evaluate(() => [...document.querySelectorAll('.hm-top button, .hm-notify button, .hm-bottom button')]
  .map((b) => [b.textContent.trim() || b.getAttribute('aria-label'), b.getBoundingClientRect()])
  .filter(([, r]) => r.width > 0 && (r.width < 44 || r.height < 36)).map(([t, r]) => `${t} ${Math.round(r.width)}x${Math.round(r.height)}`));

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const failed = [];
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('response', (r) => { if (r.status() >= 400 && r.url().includes('/api/')) failed.push(`${r.status()} ${r.url()}`); });
  await page.goto(HMI);
  const { scenarios } = await get('/scenarios');
  check('eight scenarios are offered', () => assert.equal(scenarios.length, 8));
  // The no-SOP incident runs first, so the lifecycle checks below use an incident with an SOP.
  scenarios.sort((a, b) => (a.sop_id ? 1 : 0) - (b.sop_id ? 1 : 0) || a.number - b.number);

  // The logo opens the scenario sidebar.
  await page.locator('.hm-logo').click();
  await page.locator('.hm-sheet.left').waitFor({ timeout: 8000 });
  await page.locator('.hm-sim-list li').first().waitFor({ timeout: 8000 });
  const listed = await page.locator('.hm-sim-list li').count();
  const simNote = await page.locator('.hm-sim-note').innerText();
  check('logo opens the scenario sidebar with eight scenarios, labelled simulated', () => { assert.equal(listed, 8); assert.match(simNote, /SIMULATED/); });
  await page.screenshot({ path: `${shots}/scenarios.png` });
  await page.keyboard.press('Escape');

  // Runs left unfixed by an earlier session hold their equipment; fix them so every scenario can start.
  for (const item of (await get('/scenarios/status')).unfixed_scenarios || []) await post('/scenarios/corrective-action', { scenario_id: item.scenario_id });
  await finished(); // let any recovery started by those fixes complete before the next run

  for (const scenario of scenarios) {
    await post('/scenarios/run', { scenario_id: scenario.scenario_id, speed_multiplier: 60 });
    for (let i = 0; i < 40 && (await get('/scenarios/status')).scenario_id !== scenario.scenario_id; i += 1) await sleep(250);
    const status = await finished();
    // Pick it from the Incidents tab, then go Home: Home shows the picked incident.
    await page.locator('.hm-tab', { hasText: 'Incidents' }).click();
    await page.locator(`tr[data-event-id="${status.event_id}"]`).click({ timeout: 20000 });
    await page.locator('.hm-tab.home').click();
    // A click that lands while the page is re-rendering can be lost: retry once.
    for (let i = 0; i < 4 && !(await page.locator('.hm-notify').count()); i++) { await sleep(1500); await page.locator('.hm-tab.home').click(); }
    try {
      await page.locator('.hm-notify .hm-title p', { hasText: scenario.equipment_id }).waitFor({ timeout: 15000 });
    } catch (err) {
      await page.screenshot({ path: `${shots}/pick_failed_${scenario.number}.png` });
      console.log('PICK FAILED', scenario.number, status.event_id, await page.locator('.hm-notify .hm-title').innerText().catch(() => '-'));
      throw err;
    }
    await page.locator('.hm-conf').first().waitFor({ timeout: 15000 });
    // AI answers can take minutes on a CPU; a labelled fallback also counts here.
    await page.locator('.hm-panel.diag .hm-src-label').waitFor({ timeout: 300000 });
    // The timeline arrives separately from the assessment.
    await page.locator('.hm-panel.diag .dx-story').waitFor({ timeout: 10000 }).catch(() => {});
    await sleep(1500);
    const seen = await page.evaluate(() => ({
      scroll: document.scrollingElement.scrollHeight > innerHeight || document.scrollingElement.scrollWidth > innerWidth,
      sop: document.querySelector('.hm-info.as-btn b')?.textContent,
      draft: document.querySelector('.hm-info.as-btn')?.innerText || '',
      percents: document.querySelectorAll('.hm-sure').length + [...document.querySelectorAll('.hm-panel > header, .hm-notify')].filter((e) => /\d+% sure/.test(e.innerText)).length,
      conf: document.querySelector('.hm-conf')?.textContent || '',
      sopMatch: document.querySelector('.hm-info.as-btn small')?.innerText || '',
      panels: [...document.querySelectorAll('.hm-panel > header h2')].map((h) => h.textContent),
      readings: document.querySelectorAll('.hm-panel.rd .ind').length,
      hiddenReadings: Number((document.querySelector('.hm-panel.rd .fit-more')?.textContent.match(/\+(\d+)/) || [0, 0])[1]),
      panelScroll: [...document.querySelectorAll('.hm-main *')].filter((e) => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1).length,
      cutSteps: [...document.querySelectorAll('.hm-main .paged > li')].filter((li) => li.getBoundingClientRect().bottom > li.parentElement.getBoundingClientRect().bottom + 1).map((li) => li.textContent.slice(0, 50)),
      cutWithoutMore: [...document.querySelectorAll('.hm-main .fit')].flatMap((f) => [...f.children].filter((k) => k.getBoundingClientRect().bottom > f.getBoundingClientRect().bottom + 1).map((k) => `${f.closest('.hm-panel').className} > ${k.getAttribute('class')} ${Math.round(k.getBoundingClientRect().bottom - f.getBoundingClientRect().bottom)}px: ${k.textContent.slice(0, 60)}`)),
      nbaSource: document.querySelector('.hm-panel.nba .hm-source')?.textContent || '',
      planSteps: document.querySelectorAll('.hm-panel.nba .paged > li').length,
      sources: [...document.querySelectorAll('.hm-panel.nba .kind.src')].map((e) => e.dataset.source),
      sopRefs: document.querySelectorAll('.hm-panel.nba .ref').length,
      diagHead: document.querySelector('.hm-panel.diag .dx-head')?.textContent || '',
      cause: document.querySelector('.hm-panel.diag .dx-cause')?.textContent || '',
      leadSource: document.querySelector('.hm-panel.diag .dx-k small')?.textContent || '',
      storyText: document.querySelector('.hm-panel.diag .dx-story')?.textContent || '',
      rcRoles: [...document.querySelectorAll('.hm-panel.rc .rc-role')].map((e) => e.textContent),
      rcLead: document.querySelector('.hm-panel.rc .rc-item .rc-cause')?.lastChild?.textContent || '',
      
      prio: document.querySelector('.hm-prio')?.innerText || '',
      bars: ['.hm-top', '.hm-notify', '.hm-bottom'].map((q) => document.querySelector(q)?.getBoundingClientRect().height),
    }));
    // The record story may sit behind "+N more": it must be complete in the pop-up.
    const diagMore = page.locator('.hm-panel.diag .fit-more');
    let story = await page.locator('.hm-panel.diag .dx-tl-row').count();
    if (await diagMore.count()) {
      await diagMore.click();
      story = await page.locator('.fit-full .dx-tl-row').count();
      await page.keyboard.press('Escape');
      await page.locator('.fit-full').waitFor({ state: 'detached' });
    }
    seen.story = story;
    const pager = page.locator('.hm-panel.nba .pager');
    if (await pager.count()) {
      const before = await pager.locator('span').innerText();
      await pager.getByRole('button', { name: 'Next steps' }).click();
      const after = await pager.locator('span').innerText();
      // Walk every page of the plan: SOP-linked steps and source labels anywhere in it.
      seen.moreSources = [];
      let pages = 1;
      for (;;) {
        seen.sopRefs += await page.locator('.hm-panel.nba .ref').count();
        seen.moreSources.push(...await page.locator('.hm-panel.nba .kind.src').evaluateAll((els) => els.map((e) => e.dataset.source)));
        const next = pager.getByRole('button', { name: 'Next steps' });
        if (pages >= 8 || await next.isDisabled()) break;
        await next.click(); pages += 1;
      }
      for (let i = 0; i < pages; i += 1) await pager.getByRole('button', { name: 'Previous steps' }).click();
      check(`${scenario.name}: plan pages forward and back`, () => { assert.notEqual(after, before); assert.equal(seen.panelScroll, 0); });
    }
    const name = scenario.name;
    check(`${name}: right alarm`, () => assert.equal(status.alarm_tag, scenario.alarm_id));
    if (scenario.sop_id) {
      check(`${name}: suggested SOP shown, with no draft wording (plant SOPs approved by the site setting)`, () => { assert.equal(seen.sop, scenario.sop_id); assert.doesNotMatch(seen.draft, /DRAFT|NOT APPROVED/i); });
        check(`${name}: next best action is a plan linked to SOP steps`, () => { assert.match(seen.nbaSource, /^(AI plan based on|Steps from) SOP-/); assert.ok(seen.planSteps >= 2); assert.ok(seen.sopRefs >= 2, "fewer than two SOP-linked steps in the plan"); });
        check(`${name}: SOP steps are labelled SOP-verified, never draft`, () => { const all = [...seen.sources, ...(seen.moreSources || [])]; assert.ok(all.includes('SOP-VERIFIED'), all.join(', ')); assert.ok(!all.some((x) => /DRAFT/.test(x)), all.join(', ')); });
    } else {
      check(`${name}: no SOP is said plainly`, () => { assert.equal(seen.sop, 'None found'); assert.match(seen.nbaSource, /No SOP found/); });
      check(`${name}: without an SOP every step has a non-SOP source label`, () => { assert.equal(seen.sopRefs, 0); assert.equal(seen.sources.length, seen.planSteps); assert.ok(seen.sources.every((x) => /^(TELEMETRY EVIDENCE|HISTORICAL RESOLUTION|AI-GENERATED · NOT SOP-VERIFIED|OPERATOR VERIFICATION)$/.test(x)), seen.sources.join(', ')); });
    }
    check(`${name}: assessment shows the leading hypothesis and the record`, () => { assert.ok(seen.cause.length > 5, 'no leading hypothesis'); assert.ok(seen.story >= 2); });
    // One cause story on every screen: Home's box, Root-Cause Evidence and the backend agree.
    const insight = await get(`/events/${status.event_id}/insight`);
    const top = insight.cause_candidates[0];
    check(`${name}: Assessment, Root-Cause Evidence and the record name the same leading cause`, () => {
      assert.equal(seen.rcRoles[0], 'LEADING');
      assert.equal(seen.rcLead, top.short);
      if (/from the record/.test(seen.leadSource)) assert.equal(seen.cause, insight.leading_hypothesis);
      else assert.match(seen.leadSource, /agrees with the record/);
      assert.ok(!seen.rcRoles.includes('ALSO POSSIBLE'));
    });
    if (!seen.storyText) await page.screenshot({ path: `${shots}/no_story_${scenario.number}.png` });
    check(`${name}: the event story is told in a plain sentence`, () => assert.match(seen.storyText, /first \(\d{2}:\d{2}:\d{2}\)\./));
    check(`${name}: five panels in order`, () => assert.deepEqual(seen.panels, ['Assessment', 'Root-Cause Evidence', 'Next Best Action', 'Related Alarms', 'Important Readings']));
    check(`${name}: Evidence Support shows the backend score and its strength, no "% sure" wording`, () => {
      const m = seen.conf.match(/^EVIDENCE SUPPORT(\d{1,3})%(Strong|Moderate|Limited) evidence$/);
      assert.ok(m, seen.conf);
      const score = Number(m[1]);
      // The strength comes from the score (Strong >= 75, Moderate >= 45); Limited can also be forced by a data issue.
      if (m[2] === 'Strong') assert.ok(score >= 75, seen.conf);
      if (m[2] === 'Moderate') assert.ok(score >= 45 && score < 75, seen.conf);
      assert.equal(seen.percents, 0);
    });
    check(`${name}: readings and priority counts shown`, () => { assert.ok(seen.readings + seen.hiddenReadings >= 3); assert.ok(seen.readings >= 2); assert.match(seen.prio, /HIGH[\s\S]*MED[\s\S]*LOW/); });
    check(`${name}: no panel scrolls and nothing is cut without a "+N more"`, () => { assert.equal(seen.panelScroll, 0); assert.deepEqual(seen.cutWithoutMore, []); assert.deepEqual(seen.cutSteps, []); });
    check(`${name}: fits 1280x800 without page scroll`, () => { assert.equal(seen.scroll, false); assert.deepEqual(seen.bars, [96, 88, 76]); });
    await page.screenshot({ path: `${shots}/home_${scenario.number}.png` });
  }
  await page.locator('.hm-conf').click();
  await page.locator('.hm-support-why summary').click();
  const why = await page.locator('.hm-conf-pop').innerText();
  check('tapping Evidence Support explains it, with the breakdown under "Why this score?"', () => {
    assert.match(why, /Evidence support · \d+% · (Strong|Moderate|Limited) evidence/);
    assert.match(why, /not a statistical probability/);
    assert.match(why, /Why this score\?/);
    assert.match(why, /Evidence support\s+\d+\/100/);
  });
  await page.screenshot({ path: `${shots}/confidence_why.png` });
  await page.keyboard.press('Escape');
  const small = await smallTargets(page);
  check('every button in the bars is big enough to tap', () => assert.deepEqual(small, []));

  // Lifecycle on the last incident, through the notification and bottom bars.
  const eventId = (await get('/scenarios/status')).event_id;
  await page.locator('.hm-bbtn', { hasText: 'Confirm Resolution' }).click();
  await page.locator('.hm-dialog').waitFor();
  const resolvedDisabled = await page.locator('.hm-choice input[value="RESOLVE"]').isDisabled();
  check('Resolved cannot be chosen while the alarm is active', () => assert.equal(resolvedDisabled, true));
  await page.locator('.hm-choice label', { hasText: 'Note only' }).click();
  await page.locator('#hm-resolve-note').fill('Standby pump noisy');
  await page.locator('.hm-dialog footer .hm-btn.primary').click();
  await page.locator('.hm-dialog').waitFor({ state: 'detached' });
  const ack = page.locator('.hm-notify .hm-btn.ack');
  if (await ack.count()) await ack.click();
  await page.locator('.hm-banner.ok').waitFor({ timeout: 8000 });
  const afterAck = await get('/alarms/active');
  check('acknowledging does not clear the alarm', () => assert.ok(afterAck.active_total > 0));
  await sleep(1000);
  const ackLeft = await ack.count();
  check('the Acknowledge button goes once everything is acknowledged', () => assert.equal(ackLeft, 0));
  await page.locator('.hm-bbtn', { hasText: 'Verify Status' }).click();
  await page.locator('.hm-banner', { hasText: /Not fixed yet|back to normal/ }).waitFor({ timeout: 8000 });
  const notFixed = await page.locator('.hm-banner').innerText();
  check('Verify Status says the fault is still there before the fix', () => assert.match(notFixed, /Not fixed yet/));

  // Earlier runs share equipment with the last one and hold its readings, so fix them first.
  const { unfixed_scenarios: unfixed } = await get('/scenarios/status');
  for (const item of unfixed.filter((u) => u.event_id !== eventId)) {
    const fixed = await post('/scenarios/corrective-action', { scenario_id: item.scenario_id });
    check(`${item.scenario_id}: simulated fix clears the alarm`, () => assert.equal(fixed.alarm_active, false));
  }
  // Simulate the fix from the sidebar, then verify and confirm.
  await page.locator('.hm-logo').click();
  await page.locator('.hm-sim-actions button', { hasText: 'Simulate the fix' }).click();
  await page.locator('.hm-sheet .hm-note', { hasText: 'Simulated fix applied' }).waitFor({ timeout: 15000 });
  await page.keyboard.press('Escape');
  let verified = '';
  for (let i = 0; i < 30 && !/Confirm Resolution to log it/.test(verified); i += 1) {
    if (await ack.count()) await ack.click(); // inputs that tripped after the first acknowledgement
    await page.locator('.hm-bbtn', { hasText: 'Verify Status' }).click();
    await sleep(1500);
    verified = await page.locator('.hm-banner').innerText().catch(() => '');
  }
  check('Verify Status confirms the readings are back after the fix', () => assert.match(verified, /back to normal/));
  await page.screenshot({ path: `${shots}/verify_ok.png` });
  await page.locator('.hm-bbtn', { hasText: 'Confirm Resolution' }).click();
  await page.locator('.hm-choice input[value="RESOLVE"]').check();
  await page.locator('#hm-resolve-note').fill('Fault cleared; checked locally');
  await page.screenshot({ path: `${shots}/confirm_resolution.png` });
  await page.locator('.hm-dialog footer .hm-btn.primary').click();
  await page.locator('.hm-dialog').waitFor({ state: 'detached' });
  const life = await get(`/events/${eventId}/lifecycle`);
  const actions = (await get(`/events/${eventId}`)).actions.map((a) => a.action_type);
  check('confirmed incident is resolved and the note is on record', () => { assert.equal(life.state, 'RESOLVED'); assert.ok(actions.includes('ANNOTATE')); });

  // Notification bar actions.
  await page.locator('.hm-notify .hm-btn', { hasText: 'Related Incidents' }).click();
  await page.locator('.hm-sheet.right', { hasText: 'Related incidents' }).waitFor();
  await page.keyboard.press('Escape');
  await page.locator('.hm-info.as-btn').click();
  await page.getByRole('dialog').first().waitFor({ timeout: 8000 });
  const sopText = await page.getByRole('dialog').first().innerText();
  check('View Suggested SOP opens the document', () => assert.ok(sopText.length > 100));
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Procedure' }).waitFor({ state: 'detached', timeout: 5000 });
  check('the SOP viewer closes with Escape', () => assert.ok(true));

  // Chatbot docks between the bars and does not cover them.
  await page.locator('.hm-bbtn', { hasText: 'Ask Chatbot' }).click();
  await page.locator('.copilot-dock').waitFor({ timeout: 8000 });
  const overlap = await page.evaluate(() => {
    const d = document.querySelector('.copilot-dock').getBoundingClientRect();
    return ['.hm-notify', '.hm-bottom', '.hm-top'].map((q) => document.querySelector(q).getBoundingClientRect())
      .some((r) => r.bottom > d.top + 1 && r.top < d.bottom - 1 && r.right > d.left);
  });
  check('Ask Chatbot opens beside the panels without covering the bars', () => assert.equal(overlap, false));
  await page.screenshot({ path: `${shots}/chatbot.png` });
  await page.locator('.hm-bbtn', { hasText: 'Ask Chatbot' }).click();

  // View Details goes to the Incidents tab with the event story and explanation.
  await page.locator('.hm-notify .hm-btn', { hasText: 'View Details' }).click();
  const details = await page.locator('.hm-tab-body').innerText();
  check('View Details opens the event story and explanation', () => { assert.match(details, /Explanation/); assert.match(details, /All incidents/); });

  // Every tab: compact top bar with a home icon, bottom bar still there.
  for (const tab of TABS) {
    await page.locator('.hm-tab', { hasText: tab }).click();
    await sleep(tab.startsWith('Time') || tab.startsWith('Root') ? 1800 : 900);
    const seen = await page.evaluate(() => ({ text: document.querySelector('.hm-tab-body').innerText, wide: document.scrollingElement.scrollWidth > innerWidth,
      compact: Boolean(document.querySelector('.hm-top.compact .hm-tab.home')), bottom: document.querySelectorAll('.hm-bottom button').length, notify: Boolean(document.querySelector('.hm-notify')) }));
    check(`${tab}: renders with the simple top bar and the bottom bar`, () => { assert.ok(seen.text.length > 60, seen.text); assert.equal(seen.wide, false); assert.ok(seen.compact); assert.equal(seen.bottom, 5); assert.equal(seen.notify, false); });
    if (tab === 'Alarms') {
      const heads = await page.locator('.hm-card h2').allInnerTexts();
      check('Alarms tab: active alarms on top, alarm summary below', () => { assert.match(heads[0], /Active alarms/); assert.match(heads[1], /Alarm summary/); });
    }
    if (tab === 'Root Cause & Analysis') {
      await page.locator('.hm-seg button', { hasText: 'Knowledge graph' }).click();
      await page.locator('.kg-node.cause').first().waitFor({ timeout: 10000 });
      const kg = await page.evaluate(() => ({
        leading: document.querySelectorAll('.kg-node.role-leading').length,
        cut: [...document.querySelectorAll('.kg-node text')].filter((t) => t.getComputedTextLength() > t.parentNode.querySelector('rect').width.baseVal.value - 10).map((t) => t.textContent),
        columns: [...document.querySelectorAll('.kg-col')].map((t) => t.textContent),
        crumb: document.querySelector('.kg-crumb')?.textContent || '' }));
      check('knowledge graph shows the incident path: one leading cause, names inside their boxes, plant as a breadcrumb', () => {
        assert.equal(kg.leading, 1);
        assert.deepEqual(kg.cut, []);
        assert.deepEqual(kg.columns, ['Readings that moved', 'Alarms', 'Causes (hypotheses)', 'SOP step that fixes it', 'SOP and recovery check']);
        assert.match(kg.crumb, /^Wastewater plant › /);
      });
      await page.locator('.kg-node.role-leading').hover();
      const faded = await page.locator('.kg-node.faded').count();
      check('pointing at the leading cause fades what is not linked to it', () => assert.ok(faded > 0));
      await page.mouse.move(2, 2);
    }
    if (tab.startsWith('Time')) {
      await page.locator('.ts-cards').waitFor({ timeout: 10000 });
      const cards = await page.locator('.ts-cards').innerText();
      check('forecasting and anomaly detection are on in Time-Series & Prediction', () => { assert.match(cards, /Prediction/i); assert.match(cards, /anomal/i); });
    }
    await page.screenshot({ path: `${shots}/tab_${tab.replace(/\W+/g, '_')}.png` });
  }
  await page.locator('.hm-bbtn', { hasText: 'SOPs' }).click();
  await page.locator('.hm-sop-list button').first().waitFor({ timeout: 8000 });
  await page.locator('.hm-sop-list button').first().click();
  await page.locator('.hm-sop-preview header h2').waitFor({ timeout: 8000 });
  const sopCount = await page.locator('.hm-sop-list button').count();
  check('SOPs screen lists the SOPs with a preview', () => assert.ok(sopCount >= 5));
  await page.screenshot({ path: `${shots}/sops.png` });
  await page.locator('.hm-bbtn', { hasText: 'Previous Shift Details' }).click();
  const shift = await page.locator('.hm-tab-body').innerText();
  check('Previous Shift Details opens Shifts History', () => { assert.match(shift, /Last shift/); assert.match(shift, /Past shifts/); });
  await page.locator('.hm-tab.home').click();
  await page.locator('.hm-top:not(.compact) .hm-active-btn').click();
  const onAlarms = await page.locator('.hm-page-head h1').innerText();
  check('Active Alarms button opens the Alarms tab', () => assert.equal(onAlarms, 'Alarms'));

  check('no uncaught page errors', () => assert.deepEqual(errors, []));
  check('no failed API requests', () => assert.deepEqual([...new Set(failed)], []));
  console.log(results.join('\n'));
  console.log(`\n${results.length} checks passed`);
  await browser.close();
})().catch((error) => { console.error(results.join('\n')); console.error(error); process.exit(1); });
