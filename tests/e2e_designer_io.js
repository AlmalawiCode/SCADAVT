// GUI end-to-end for PLC I/O modules + emulated sensors, driven through real
// clicks/typing (the exact steps the tutorial describes), then a live Run.
const fs = require('fs');
const puppeteer = require('puppeteer-core');
const BASE = process.env.BASE || 'http://127.0.0.1:8143';
const SHOTS = process.env.SHOTS || '.';

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
    defaultViewport: { width: 1400, height: 900 },
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  const ok = (name, cond, detail = '') => console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${name}${detail ? '  — ' + detail : ''}`) || cond;
  const results = [];
  const check = (n, c, d) => results.push(ok(n, c, d));
  const logText = () => page.$eval('#log', e => e.textContent);
  const clickNode = id => page.click(`g.node[data-id="${id}"] rect:not(.halo)`);
  const waitChans = () => page.waitForSelector('#chans table.ch', { timeout: 8000 });
  async function setField(sel, value) {           // type into a sensor field, Tab fires change
    await page.focus(sel);
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type(String(value));
    await page.keyboard.press('Tab');
    await sleep(150);
  }

  try {
    await page.goto(BASE + '/designer/', { waitUntil: 'networkidle0', timeout: 20000 });
    await page.waitForFunction('window.SCADAVT', { timeout: 8000 });
    await sleep(300);                              // catalog fetch

    // 1. add nodes with the toolbar buttons
    await page.click('[data-add="mtu"]');
    await page.click('[data-add="switch"]');
    await page.click('[data-add="plc"]');
    await page.click('[data-add="plc"]');
    // lay them out so clicks are unambiguous
    await page.evaluate(() => {
      const S = window.SCADAVT; S.loadConfig(Object.assign(S.cfg, { _view: {
        MTU: { x: 60, y: 200 }, SW1: { x: 260, y: 200 }, PLC1: { x: 480, y: 110 }, PLC2: { x: 480, y: 300 } } }));
    });

    // 2. wire with Connect mode: click A then B
    await page.click('#connectBtn');
    for (const n of ['MTU', 'PLC1', 'PLC2']) { await clickNode(n); await clickNode('SW1'); }
    await page.keyboard.press('Escape');
    const links = await page.evaluate(() => window.SCADAVT.cfg.links.map(l => `${l.a}-${l.b}`).sort());
    check('wired MTU, PLC1, PLC2 to SW1 with Connect clicks', JSON.stringify(links) === JSON.stringify(['MTU-SW1', 'PLC1-SW1', 'PLC2-SW1']), links.join(' '));

    // 3. select PLC1: its I/O modules and channel table appear
    await clickNode('PLC1');
    await waitChans();
    const mods = await page.$$eval('#props .mod', es => es.map(e => e.textContent.replace(/\s+/g, ' ').trim()));
    check('selecting a PLC shows its I/O modules', mods.length === 3 && /AI · 4 ch/.test(mods[0]), mods.join(' | '));
    const refs = await page.$$eval('#chans table.ch tr', trs => trs.slice(1).map(t => t.children[0].textContent + '=' + t.children[1].textContent));
    check('channel table shows Modbus refs from the server', refs.includes('AI0=30001') && refs.includes('DI0=10001') && refs.includes('DO0=00001'), refs.join(' '));

    // 4. wire sensors on PLC1: temperature on AI0 (constant 28), humidity on AI1, switch on DI0
    await page.select('select[data-ch="AI0"]', 'temperature'); await waitChans(); await sleep(200);
    await page.select('select[data-k="wave"]', 'constant'); await waitChans(); await sleep(200);
    await setField('input[data-k="value"]', 28); await waitChans();
    await page.select('select[data-ch="AI1"]', 'humidity'); await waitChans(); await sleep(200);
    await page.select('select[data-ch="DI0"]', 'switch'); await waitChans(); await sleep(200);
    await page.screenshot({ path: `${SHOTS}/io_plc1.png` });

    // 5. PLC2: pressure on AI0 (constant 2.5), fan on DO0
    await clickNode('PLC2'); await waitChans(); await sleep(200);
    await page.select('select[data-ch="AI0"]', 'pressure'); await waitChans(); await sleep(200);
    await page.select('select[data-k="wave"]', 'constant'); await waitChans(); await sleep(200);
    await setField('input[data-k="value"]', 2.5); await waitChans();
    await page.select('select[data-ch="DO0"]', 'fan'); await waitChans(); await sleep(200);
    await page.screenshot({ path: `${SHOTS}/io_plc2.png` });

    const wiring = await page.evaluate(() => ({ s: window.SCADAVT.cfg.sensors, a: window.SCADAVT.cfg.actuators }));
    const tags = [...wiring.s, ...wiring.a].map(f => `${f.id}:${f.device}.${f.channel}`).sort();
    check('sensors/actuators wired with auto tags', JSON.stringify(tags) === JSON.stringify(
      ['FAN1:PLC2.DO0', 'HS1:PLC1.DI0', 'HT1:PLC1.AI1', 'PT1:PLC2.AI0', 'TT1:PLC1.AI0']), tags.join(' '));
    const tt = wiring.s.find(f => f.id === 'TT1');
    check('TT1 set to constant 28 via the panel', tt.wave === 'constant' && tt.value === 28, JSON.stringify(tt));

    // 6. rules, typed into the box
    await page.click('#rules');
    await page.keyboard.type('PLC1.AI0 > 25 => PLC2.DO0 on\nPLC1.AI0 < 24 => PLC2.DO0 off\nPLC2.AI0 < 3 => PLC1.DO1 on');
    await page.click('#applyRules');
    // 7. validate
    await page.click('#validateBtn');
    await page.waitForFunction(() => /VALID|INVALID/.test(document.getElementById('log').textContent), { timeout: 8000 });
    const v = await logText();
    check('Validate says VALID', /VALID ✓/.test(v), v.split('\n')[0]);
    fs.writeFileSync(`${SHOTS}/gui_io_export.json`, await page.evaluate(() => window.SCADAVT.exportJSON()));

    // 8. Run live on CORE
    await page.click('#runBtn');
    await page.waitForFunction(() => /RUN ✓|RUN failed/.test(document.getElementById('log').textContent), { timeout: 240000 });
    const r = await logText();
    const run = r.slice(r.indexOf('RUN ✓')).split(/\n\d+:\d+:\d+/)[0];
    console.log('---- GUI Output panel ----\n' + run.replace(/^.*?(RUN)/, '$1') + '\n--------------------------');
    check('Run reached 2/2', /reached 2\/2/.test(r));
    check('PLC1.AI0 = 28.00 TT1 temperature', /AI0\s+=\s+28\.00\s+TT1 temperature/.test(r));
    check('PLC2.DO0 = 1.00 FAN1 fan (rule fired)', /DO0\s+=\s+1\.00\s+FAN1 fan/.test(r));
    await page.screenshot({ path: `${SHOTS}/io_run.png` });
    check('no page errors', errs.length === 0, errs.join(' | '));
  } catch (e) {
    console.log('E2E error:', e); results.push(false);
    await page.screenshot({ path: `${SHOTS}/io_error.png` }).catch(() => {});
  } finally {
    await browser.close();
  }
  const passed = results.filter(Boolean).length;
  console.log(`RESULT ${passed}/${results.length}`);
  process.exit(passed === results.length ? 0 : 1);
})();
