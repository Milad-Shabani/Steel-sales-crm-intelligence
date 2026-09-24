// Render docs/bpmn/*.bpmn to SVG with the same bpmn-js viewer the dashboard uses,
// so the README shows exactly what the dashboard draws.
//
//   python -m steel_crm.cli bpmn        # write the .bpmn files
//   node scripts/render_bpmn.js         # needs Playwright: npm install playwright
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.join(__dirname, '..');
const viewer = fs.readFileSync(path.join(root, 'dashboard', 'bpmn-viewer.production.min.js'), 'utf8');
const dir = path.join(root, 'docs', 'bpmn');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 2400, height: 1400 } });
  await page.setContent('<div id="canvas" style="width:2400px;height:1400px"></div>');
  await page.addScriptTag({ content: viewer });
  for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.bpmn')).sort()) {
    const xml = fs.readFileSync(path.join(dir, file), 'utf8');
    const { svg, warnings } = await page.evaluate(async xml => {
      document.getElementById('canvas').innerHTML = '';
      const bpmn = new BpmnJS({ container: '#canvas' });
      const { warnings } = await bpmn.importXML(xml);
      const { svg } = await bpmn.saveSVG();
      return { svg, warnings: warnings.map(w => w.message) };
    }, xml);
    if (warnings.length) throw new Error(`${file}: ${warnings.join('; ')}`);
    const out = path.join(dir, file.replace(/\.bpmn$/, '.svg'));
    // a white page behind the diagram, so it stays readable on GitHub's dark theme
    const [x, y, w, h] = svg.match(/viewBox="([^"]+)"/)[1].split(/\s+/);
    const withBackground = svg.replace(/(<svg[^>]*>)/, `$1<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#ffffff"/>`);
    fs.writeFileSync(out, withBackground.replace(/^<\?xml[^>]*>\s*/, '') + '\n');
    console.log('Wrote', path.relative(root, out));
  }
  await browser.close();
})();
