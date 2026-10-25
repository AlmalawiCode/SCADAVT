// Genuine end-to-end: build an arbitrary multi-PLC topology with cross-PLC
// rules THROUGH THE GUI, validate, and EXECUTE it live on CORE via /run,
// retrieving the actual simulation results. Also writes the exported config to
// a file so the identical config can be run through the CLI for comparison.
const fs = require('fs');
const puppeteer = require('puppeteer-core');
const BASE = process.env.BASE || 'http://127.0.0.1:8141';
const OUT = process.env.OUT || '/tmp/gui_exported.json';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage();
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(BASE + '/designer/', { waitUntil: 'networkidle0', timeout: 20000 });
    await page.waitForFunction('window.SCADAVT && typeof window.SCADAVT.addNode === "function"', { timeout: 8000 });

    const out = await page.evaluate(async () => {
      const S = window.SCADAVT;
      // arbitrary topology built by hand in the GUI: MTU + 3 PLCs + 1 switch
      S.addNode('mtu', 60, 60);
      S.addNode('switch', 160, 120);
      S.addNode('plc', 260, 40);   // PLC1
      S.addNode('plc', 260, 120);  // PLC2
      S.addNode('plc', 260, 200);  // PLC3
      ['MTU', 'PLC1', 'PLC2', 'PLC3'].forEach(n => S.addLink(n, 'SW1'));
      // cross-PLC control ring entered as a user would type it
      document.getElementById('rules').value = 'PLC1.s < 3 => PLC2.a open\nPLC2.s < 3 => PLC3.a open\nPLC3.s < 3 => PLC1.a open';
      const exported = JSON.parse(S.exportJSON());
      const v = await (await fetch('/validate', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: exported }) })).json();
      const runRes = await fetch('/run', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: exported, cycles: 60 }) });
      const run = await runRes.json();
      return { exported, validate: v, run_status: runRes.status, run };
    });

    fs.writeFileSync(OUT, JSON.stringify(stripView(out.exported), null, 2));
    console.log(JSON.stringify({
      pageerrors: errs,
      devices: out.exported.devices.map(d => d.id),
      links: out.exported.links.length,
      rules: out.exported.rules.length,
      validate_ok: out.validate.ok,
      run_status: out.run_status,
      run: out.run,
      exported_to: OUT,
    }, null, 2));
  } finally {
    await browser.close();
  }
  function stripView(c) { const o = Object.assign({}, c); delete o._view; return o; }
})().catch(e => { console.error('E2E ERROR:', e); process.exit(2); });
