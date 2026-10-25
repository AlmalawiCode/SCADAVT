// Real-browser E2E of the SCADAVT generic designer: load the served page,
// build an arbitrary topology THROUGH THE GUI's own JS (window.SCADAVT),
// export the canonical config, and validate it via the shared service — all
// same-origin. Proves "created in the GUI, exported, validated by L1".
const puppeteer = require('puppeteer-core');
const BASE = process.env.BASE || 'http://127.0.0.1:8161';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage();
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(BASE + '/designer/', { waitUntil: 'networkidle0', timeout: 15000 });
    await page.waitForFunction('window.SCADAVT && typeof window.SCADAVT.addNode === "function"', { timeout: 8000 });

    // --- build an ARBITRARY topology via the GUI's own operations ---
    const result = await page.evaluate(async () => {
      const S = window.SCADAVT;
      S.addNode('mtu', 60, 60);
      S.addNode('plc', 200, 60);     // PLC1
      S.addNode('plc', 200, 160);    // PLC2
      S.addNode('switch', 130, 110); // SW1
      // wire them (arbitrary, user-drawn): all on SW1
      S.addLink('MTU', 'SW1');
      S.addLink('PLC1', 'SW1');
      S.addLink('PLC2', 'SW1');
      // a cross-PLC control rule on the PLCs' I/O channels, typed as a user would
      document.getElementById('rules').value = 'PLC1.AI0 > 25 => PLC2.DO0 on';
      const exported = JSON.parse(S.exportJSON());   // exportJSON() syncs the box
      // validate via the shared service, same-origin (as the Validate button does)
      const res = await fetch('/validate', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: exported }) });
      const data = await res.json();
      return { exported, validate_status: res.status, validate: data };
    });

    const ex = result.exported;
    const nodeCount = (ex.devices || []).length + (ex.mtu ? 1 : 0) +
                      (ex.switches || []).length + (ex.routers || []).length;
    console.log(JSON.stringify({
      pageerrors: errs,
      built_nodes: nodeCount,
      devices: (ex.devices || []).map(d => d.id),
      links: (ex.links || []).length,
      io_modules: (ex.devices || []).map(d => (d.io_modules || []).map(m => m.type + m.channels).join('+')),
      logic: (ex.logic || []).length,
      validate_status: result.validate_status,
      validate_ok: result.validate && result.validate.ok,
      issues: (result.validate && result.validate.issues) || [],
    }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('E2E ERROR:', e); process.exit(2); });
