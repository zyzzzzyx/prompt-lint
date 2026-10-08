// 在 node 沙箱里加载 web/app.js，用同一批样例跑出分析结果，供 Python 端比对。
// 用法：node tests/parity_runner.js   （输出 .tmp/parity_js.json）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const root = path.resolve(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'web', 'app.js'), 'utf8');
const rules = JSON.parse(fs.readFileSync(path.join(root, 'web', 'rules.json'), 'utf8'));
const cases = JSON.parse(fs.readFileSync(path.join(root, '.tmp', 'parity_cases.json'), 'utf8'));

// 最小 DOM stub：app.js 只在 init 里碰 DOM，这里让它跑完即可
const el = new Proxy({}, { get: () => () => el, set: () => true });
const sandbox = {
  console,
  document: { querySelector: () => el, createElement: () => el, addEventListener: () => {} },
  location: { hostname: 'example.com' },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(rules) }),
  setTimeout,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

setTimeout(() => {
  const out = {};
  for (const [k, v] of Object.entries(cases)) {
    const r = sandbox.analyze(v, rules);
    out[k] = { score: r.score, grade: r.grade, ids: r.issues.map((i) => `${i.id}:${i.penalty}`) };
  }
  fs.writeFileSync(path.join(root, '.tmp', 'parity_js.json'), JSON.stringify(out, null, 1));
  process.exit(0);
}, 50);
