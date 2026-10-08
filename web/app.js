/* PromptLint 浏览器端
 * 与 Python 引擎共用同一份 rules.json，检测逻辑一一对应。
 * 全部计算在本地完成，不发送任何网络请求（除加载规则表本身）。
 */

const RULE_URLS = ['./rules.json', '../prompt_lint/rules.json'];

let RULES = null;
const $ = (s) => document.querySelector(s);

/* ============ 规则加载 ============ */
async function loadRules() {
  for (const url of RULE_URLS) {
    try {
      const r = await fetch(url, { cache: 'no-cache' });
      if (r.ok) return await r.json();
    } catch (e) { /* 换下一个路径 */ }
  }
  throw new Error('未能加载 rules.json，请通过本地服务器或 GitHub Pages 打开本页面。');
}

/* ============ 扫描原语 ============ */
function scanKeywords(text, keywords) {
  const occ = new Array(text.length).fill(false);
  const hits = [];
  const kws = [...keywords].sort((a, b) => b.length - a.length);
  for (const kw of kws) {
    if (!kw) continue;
    let start = 0;
    for (;;) {
      const idx = text.indexOf(kw, start);
      if (idx === -1) break;
      const end = idx + kw.length;
      let free = true;
      for (let i = idx; i < end; i++) if (occ[i]) { free = false; break; }
      if (free) {
        for (let i = idx; i < end; i++) occ[i] = true;
        hits.push({ start: idx, end, word: kw });
      }
      start = idx + 1;
    }
  }
  hits.sort((a, b) => a.start - b.start);
  return hits;
}

function scanPatterns(text, patterns) {
  const out = [];
  for (const pat of patterns || []) {
    let re;
    try { re = new RegExp(pat, 'gm'); } catch (e) { continue; }
    let m;
    while ((m = re.exec(text)) !== null) {
      const frag = m[0].trim();
      if (frag) out.push(frag);
      if (m.index === re.lastIndex) re.lastIndex++;
    }
  }
  return out;
}

function hasMaterial(text) {
  if (text.indexOf('```') !== -1) return true;
  if (/https?:\/\//.test(text)) return true;
  const re = /["'“‘「『]([^"'”’」』]{20,})["'”’」』]/g;
  let m;
  while ((m = re.exec(text)) !== null) if (m[1].trim()) return true;
  return false;
}

function splitSentences(text) {
  return text.split(/[。！？；;\n]+/).map((s) => s.trim()).filter(Boolean);
}

/* ============ 分析 ============ */
function analyze(text, rules) {
  text = (text || '').trim();
  const issues = [];
  const bonuses = [];

  if (!text) return { score: 0, grade: 'F', verdict: '提示词为空', issues: bonuses, stats: {}, rewrite: '' };

  const sc = rules.scoring;
  const material = hasMaterial(text);

  for (const det of rules.detectors) {
    let hits = [];
    let sev = det.severity;

    if (det.skip_if_material_present && material) continue;

    if (det.kind === 'keyword') {
      hits = scanKeywords(text, det.keywords || []);
      if (det.boost_if_early && hits.length) {
        const ratio = det.boost_ratio || 1 / 3;
        if (hits[0].start < text.length * ratio) sev = det.boost_severity || sev;
      }
    } else if (det.kind === 'regex') {
      hits = scanPatterns(text, det.patterns).map((f) => ({ start: 0, end: f.length, word: f }));
    } else if (det.kind === 'absence') {
      let present = scanKeywords(text, det.keywords || []);
      if (!present.length && det.regex_positive && new RegExp(det.regex_positive).test(text)) {
        present = [{ start: 0, end: 0, word: 'regex' }];
      }
      if (!present.length) hits = [{ start: 0, end: 0, word: '<全文未出现>' }];
    } else if (det.kind === 'counter') {
      const found = scanKeywords(text, det.keywords || []);
      if (found.length >= (det.threshold || 2)) hits = found;
    } else if (det.kind === 'conflict') {
      for (const [a, b] of det.pairs || []) {
        const ha = scanKeywords(text, [a]);
        const hb = scanKeywords(text, [b]);
        if (ha.length && hb.length) { hits.push(ha[0]); hits.push(hb[0]); }
      }
    } else if (det.kind === 'structure') {
      const lines = text.split('\n').filter((l) => l.trim());
      if (lines.length <= 1 && text.length >= (det.min_length || 100)) {
        hits = [{ start: 0, end: 0, word: text.slice(0, 40) + (text.length > 40 ? '…' : '') }];
      }
    }

    if (!hits.length) continue;

    let count = Math.max(1, hits.length);
    if (det.count_mode === 'excess') count = Math.max(1, hits.length - (det.threshold || 2) + 1);

    const w = sc.severity_weight[sev];
    const penalty = Math.min(w * Math.min(count, sc.hit_cap), det.max_penalty || 99);

    const shown = [];
    for (const h of hits.slice(0, 6)) if (!shown.includes(h.word)) shown.push(h.word);

    issues.push({
      id: det.id, name: det.name, dimension: det.dimension, severity: sev,
      penalty, count, hits: shown,
      model_view: det.model_view, suggestion: det.suggestion, ask: det.ask || null,
    });
  }

  for (const [key, sig] of Object.entries(rules.bonus_signals || {})) {
    if (scanKeywords(text, sig.keywords).length) {
      bonuses.push({ key, label: sig.label, score: sc.bonus[key] || 0 });
    }
  }

  let score = sc.base;
  score -= issues.reduce((a, i) => a + i.penalty, 0);
  score += bonuses.reduce((a, b) => a + b.score, 0);
  score = Math.max(0, Math.min(100, score));

  const g = rules.scoring.grades.find((x) => score >= x.min);
  const order = { high: 3, medium: 2, low: 1 };
  issues.sort((a, b) => (order[b.severity] - order[a.severity]) || (b.penalty - a.penalty));

  const sentences = splitSentences(text);
  const stats = {
    chars: text.length,
    sentences: sentences.length,
    lines: text.split('\n').filter((l) => l.trim()).length,
    issue_count: issues.length,
    high: issues.filter((i) => i.severity === 'high').length,
    medium: issues.filter((i) => i.severity === 'medium').length,
    low: issues.filter((i) => i.severity === 'low').length,
  };

  return {
    score, grade: g.label, verdict: g.verdict, issues, bonuses, stats,
    rewrite: buildRewrite(text, rules, issues),
  };
}

/* ============ 重写（RCFT） ============ */
const PLACEHOLDER = (h) => `[待补充：${h}]`;
const ROLE_PAT = /(你是|你是一位|作为|扮演|充当|假设你|请你作为|请以)[^。！？\n]{0,60}/;
const CONTEXT_PAT = /[^。！？\n]*?(受众|读者|用户|对象|面向|背景|场景|目的|目标|用途|行业|公司|产品|项目|客户|小白|新手|专家|学生|领导|年龄|水平)[^。！？\n]*/;
const FORMAT_PAT = /[^。！？\n]*?(表格|列表|分点|条列|分段|markdown|Markdown|json|JSON|yaml|csv|代码块|用表格|分条|编号|项目符号|标题|章节|模板)[^。！？\n]*/;
const LENGTH_PAT = /[^。！？\n]*?\d+\s*(字|词|句|段|条|行|页|个|点|分钟)[^。！？\n]*/;
const NEG_PAT = /[^。！？\n]*?(不要|别|不能|不可以|禁止|避免|切勿|不许|无需|不用)[^。！？\n]*/;
const EXAMPLE_PAT = /[^。！？\n]*?(例如|比如|示例|样例|像这样|参考这个|打个比方)[^。！？\n]*/;

const NOISE_WORDS = ['麻烦你', '麻烦', '拜托', '劳烦', '不好意思', '打扰了', '谢谢', '感谢',
  '辛苦了', '就是说', '你知道的', '你懂吧', '嗯', '啊', '哦', '好吧', '先这样', '那个什么',
  '随便', '呗', '对吧', '是吧', '请帮'];

function cleanNoise(s) {
  const removed = [];
  let out = s;
  for (const w of [...NOISE_WORDS].sort((a, b) => b.length - a.length)) {
    if (out.includes(w)) {
      const n = out.split(w).length - 1;
      out = out.split(w).join('');
      removed.push(`「${w}」×${n}`);
    }
  }
  out = out.replace(/\s{2,}/g, ' ').trim().replace(/^[，,。]+|[，,。]+$/g, '');
  return [out, removed];
}

const firstMatch = (re, t) => { const m = t.match(re); return m ? m[0].replace(/^[，,、\s]+|[，,、\s]+$/g, '') : ''; };
const byId = (issues, id) => issues.find((i) => i.id === id);

function findTaskSentence(text, rules) {
  const vd = rules.detectors.find((d) => d.id === 'missing_verb');
  const verbs = vd ? vd.keywords : [];
  const scored = [];
  for (const s of splitSentences(text)) {
    const n = scanKeywords(s, verbs).length;
    if (n) scored.push([n + s.length / 200, s]);
  }
  if (!scored.length) return '';
  scored.sort((a, b) => b[0] - a[0]);
  return scored[0][1];
}

function buildRewrite(text, rules, issues) {
  const tpl = rules.rewrite_template;
  const [taskSent, removed] = cleanNoise(findTaskSentence(text, rules));

  const roleLine = firstMatch(ROLE_PAT, text);
  const role = roleLine || `你是 ${PLACEHOLDER('领域 + 身份，例如「有 5 年经验的 B 端产品经理」')}。\n判断标准是 ${PLACEHOLDER('它该按什么标准做取舍')}。`;

  const ctxLine = firstMatch(CONTEXT_PAT, text);
  const context = ctxLine ||
    `受众是 ${PLACEHOLDER('谁')}；用途是 ${PLACEHOLDER('用在哪个场景')}。\n他们已经知道 ${PLACEHOLDER('')}，不知道 ${PLACEHOLDER('')}。`;

  const mi = byId(issues, 'missing_input');
  let inputBlock;
  if (mi) {
    inputBlock = `> ⚠️ 原文提到了「${mi.hits.slice(0, 3).join('、')}」，但这里没有附上内容。\n` +
      `> 模型看不到你本地的文件，也不会说「我没看到」——它会直接编一份然后作答。\n\n` +
      '```text\n（在此粘贴原文/数据/代码）\n```';
  } else if (text.includes('```')) {
    inputBlock = '已识别到以下附带材料，请确认内容完整：\n\n' +
      (text.match(/```[\s\S]*?```/g) || []).join('\n\n');
  } else {
    inputBlock = `（无外部材料则留空）\n${PLACEHOLDER('如需模型处理既有文本，粘贴在此')}`;
  }

  let task;
  if (taskSent) {
    task = taskSent;
    if (byId(issues, 'verb_fuzzy')) {
      task += '\n\n> ⚠️ 这句话里的动作仍然模糊。补一句判定标准：改哪个维度（长度/准确性/语气/结构）？改完怎么算合格？';
    }
  } else {
    task = PLACEHOLDER('用祈使句写清唯一主动作，例如「把下面的会议记录整理成 5 条待办，每条含负责人和截止时间」');
  }

  const fmtLine = firstMatch(FORMAT_PAT, text);
  const lenLine = firstMatch(LENGTH_PAT, text);
  const fmt = (fmtLine || lenLine)
    ? [fmtLine, lenLine].filter(Boolean).join(' ｜ ')
    : `形态：${PLACEHOLDER('Markdown 表格 / 编号列表 / JSON / 分段标题，选一个')}\n长度：${PLACEHOLDER('数字边界，例如「300 字以内」或「5 条」')}`;

  const constraints = [];
  const neg = firstMatch(NEG_PAT, text);
  if (neg) {
    constraints.push(`- ⚠️ 原文用了否定式：${neg}\n  → 改写成正向表述：必须 ${PLACEHOLDER('把「不要 X」翻成「只允许 Y」')}`);
  }
  const hed = byId(issues, 'hedging');
  if (hed) constraints.push(`- ⚠️ 「${hed.hits.slice(0, 3).join('、')}」没有可执行边界 → 换成数字或明确条件`);
  const subj = byId(issues, 'subjective');
  if (subj) constraints.push(`- ⚠️ 「${subj.hits.slice(0, 3).join('、')}」不可验证 → 拆成可检查特征，或给一个参考样板`);
  const con = byId(issues, 'contradiction');
  if (con) constraints.push(`- ⚠️ 原文同时要求「${con.hits[0]}」和「${con.hits[con.hits.length - 1]}」，两者冲突 → 排出优先级，明说放弃哪一个`);
  const mt = byId(issues, 'multi_task');
  if (mt) constraints.push('- ⚠️ 一段话里塞了多个任务 → 拆成编号列表，一次只交付一件事');
  if (!constraints.length) constraints.push(`- ${PLACEHOLDER('必须满足什么？用正向表述')}`);

  const exLine = firstMatch(EXAMPLE_PAT, text);
  const example = exLine || `（可选但收益最高）${PLACEHOLDER('给一个输入→输出样例，模型的对齐精度会明显提升')}`;

  const body = [
    `# 角色\n${role}`,
    `# 背景与目标\n${context}`,
    `# 输入材料\n${inputBlock}`,
    `# 任务\n${task}`,
    `# 输出格式与长度\n${fmt}`,
    `# 约束\n${constraints.join('\n')}`,
    `# 示例\n${example}`,
  ].join('\n\n');

  const askMap = {};
  for (const d of rules.detectors) if (d.ask) askMap[d.id] = d.ask;
  const questions = [];
  for (const i of issues.filter((x) => x.severity === 'high')) {
    if (askMap[i.id]) {
      const hits = (i.hits.length && i.hits[0] !== '<全文未出现>') ? i.hits.slice(0, 3).join('、') : '（未写明）';
      questions.push(askMap[i.id].replace('{hits}', hits));
    }
  }
  for (const i of issues.filter((x) => x.severity === 'medium')) {
    if (questions.length >= 6) break;
    if (askMap[i.id]) {
      const hits = (i.hits.length && i.hits[0] !== '<全文未出现>') ? i.hits.slice(0, 3).join('、') : '（未写明）';
      questions.push(askMap[i.id].replace('{hits}', hits));
    }
  }

  const foot = [];
  if (removed.length) foot.push(`## 已清理的噪声\n${removed.map((r) => `- ${r}`).join('\n')}`);
  if (questions.length) foot.push(`## 动手前先回答（按优先级）\n${questions.slice(0, 6).map((q, n) => `${n + 1}. ${q}`).join('\n')}`);

  return `### 重写后的提示词（${tpl.name}）\n\n${body}` + (foot.length ? `\n\n---\n\n${foot.join('\n\n')}` : '');
}

/* ============ 极简 Markdown 渲染 ============ */
function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderMD(src) {
  const blocks = [];
  src = src.replace(/```([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${esc(code)}</code></pre>`);
    return `\u0000B${blocks.length - 1}\u0000`;
  });
  const lines = src.split('\n');
  const out = [];
  let inList = false;

  const inline = (t) => {
    t = esc(t);
    t = t.replace(/\[待补充：([^\]]*)\]/g, '<span class="ph">[待补充：$1]</span>');
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    return t;
  };

  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };

  for (let raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (/^\u0000B\d+\u0000$/.test(line.trim())) { closeList(); out.push(line.trim()); continue; }
    if (!line.trim()) { closeList(); continue; }

    let m;
    if ((m = line.match(/^###\s+(.*)/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); continue; }
    if ((m = line.match(/^##\s+(.*)/))) { closeList(); out.push(`<h2>${inline(m[1])}</h2>`); continue; }
    if ((m = line.match(/^#\s+(.*)/))) { closeList(); out.push(`<h1>${inline(m[1])}</h1>`); continue; }
    if (/^---+$/.test(line.trim())) { closeList(); out.push('<hr>'); continue; }
    if ((m = line.match(/^>\s?(.*)/))) { closeList(); out.push(`<blockquote><p>${inline(m[1])}</p></blockquote>`); continue; }
    if ((m = line.match(/^[-*]\s+(.*)/))) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^\d+\.\s+(.*)/))) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();

  return out.join('')
    .replace(/\u0000B(\d+)\u0000/g, (_, i) => blocks[+i])
    .replace(/<\/blockquote>\s*<blockquote>/g, '');
}

/* ============ 视图 ============ */
const SEV_LABEL = { high: '高危', medium: '中等', low: '轻微' };
let DIM_NAME = {};

function ringSVG(score) {
  const R = 42, C = 2 * Math.PI * R;
  const off = C * (1 - score / 100);
  const color = score >= 88 ? '#14804a' : score >= 75 ? '#2f5fe0' : score >= 60 ? '#d97706' : '#d92d20';
  return `<div class="ring">
    <svg width="96" height="96" viewBox="0 0 96 96">
      <circle class="track" cx="48" cy="48" r="${R}" fill="none" stroke-width="9"/>
      <circle class="bar" cx="48" cy="48" r="${R}" fill="none" stroke-width="9"
        stroke="${color}" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/>
    </svg>
    <div class="ring-label"><div><div class="ring-num">${score}</div><div class="ring-grade">评级 ${current.grade}</div></div></div>
  </div>`;
}

let current = null;

function render(rep) {
  current = rep;
  const box = $('#results');
  if (!rep.issues.length) {
    box.innerHTML = `<div class="score-card">${ringSVG(rep.score)}
      <div><p class="verdict">${esc(rep.verdict)}</p>
      <p class="verdict-sub">未检出明显歧义，可以直接发给模型。</p></div></div>`;
    return;
  }

  const stats = rep.stats;
  const bonusHTML = rep.bonuses.length
    ? rep.bonuses.map((b) => `<span class="stat good">${esc(b.label)} +${b.score}</span>`).join('')
    : '';

  const issueHTML = rep.issues.map((i) => `
    <div class="issue ${i.severity}">
      <div class="issue-top">
        <span class="badge ${i.severity}">${SEV_LABEL[i.severity]}</span>
        <span class="issue-name">${esc(i.name)}</span>
        <span class="issue-dim">${esc(DIM_NAME[i.dimension] || '')}</span>
        <span class="issue-pen">-${i.penalty}</span>
      </div>
      ${i.hits.length && i.hits[0] !== '<全文未出现>' ? `<div class="hits">${i.hits.map((h) => `<span class="hit">${esc(h)}</span>`).join('')}</div>` : ''}
      <p class="mv"><span class="lbl">模型为什么会误解</span>${esc(i.model_view)}</p>
      <p><span class="lbl fix">怎么改</span>${esc(i.suggestion)}</p>
    </div>`).join('');

  box.innerHTML = `
    <div class="score-card">
      ${ringSVG(rep.score)}
      <div>
        <p class="verdict">${esc(rep.verdict)}</p>
        <p class="verdict-sub">${stats.chars} 字 / ${stats.sentences} 句 / ${stats.lines} 段，共检出 ${stats.issue_count} 处歧义风险</p>
      </div>
    </div>
    <div class="stat-row">
      <span class="stat">高危 <b>${stats.high}</b></span>
      <span class="stat">中等 <b>${stats.medium}</b></span>
      <span class="stat">轻微 <b>${stats.low}</b></span>
      ${bonusHTML}
    </div>

    <div class="section-title">模型会在这些地方猜错</div>
    ${issueHTML}

    <div class="section-title">模型更希望收到的话</div>
    <div class="rewrite-box">
      <div class="rewrite-head">
        <span>重写后的提示词 · RCFT 结构</span>
        <button id="btn-copy">复制</button>
      </div>
      <div class="md">${renderMD(rep.rewrite)}</div>
    </div>`;

  $('#btn-copy').onclick = () => {
    navigator.clipboard.writeText(rep.rewrite).then(() => {
      const b = $('#btn-copy'); b.textContent = '已复制 ✓';
      setTimeout(() => { b.textContent = '复制'; }, 1600);
    });
  };
}

/* ============ 示例 ============ */
const SAMPLES = [
  ['典型糟糕', '麻烦你帮我把这篇文章优化一下，写得好看点、高级一点，另外顺便帮我起个标题，尽量简洁全面，不要太多专业术语，谢谢！'],
  ['缺上下文', '帮我写个方案。'],
  ['多任务混杂', '帮我总结一下这个文档，另外顺便提炼出 3 个金句，还有帮我翻译成英文，对了再看看有没有错别字。'],
  ['否定式约束', '给我写一段产品介绍，不要用专业术语，别太长，不要那种营销腔，别提竞品。'],
  ['规范示例', '你是一位有 8 年经验的 B 端 SaaS 产品经理。\n\n## 背景\n受众是公司 CTO，用途是季度汇报；他们已了解产品现状，不了解竞品动态。\n\n## 任务\n对比 A、B、C 三家竞品的定价策略，列出差异点。\n\n## 输出\n用 Markdown 表格，列为 竞品 / 入门价 / 企业价 / 差异点，不超过 8 行。\n\n## 标准\n每条差异必须有官网来源支撑，找不到来源就写「未公开」。'],
];

function buildSamples() {
  const box = $('#samples');
  box.innerHTML = '';
  SAMPLES.forEach(([name, text]) => {
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = name;
    b.onclick = () => { $('#input').value = text; run(); };
    box.appendChild(b);
  });
}

function run() {
  const text = $('#input').value;
  if (!text.trim()) {
    $('#results').innerHTML = '<div class="empty-state"><p>先粘贴一段提示词。</p></div>';
    return;
  }
  render(analyze(text, RULES));
}

/* ============ 启动 ============ */
(async function init() {
  try {
    RULES = await loadRules();
    DIM_NAME = Object.fromEntries((RULES.dimensions || []).map((d) => [d.id, d.name]));
  } catch (e) {
    $('#results').innerHTML = `<div class="err">${esc(e.message)}</div>`;
    return;
  }
  buildSamples();
  $('#btn-run').onclick = run;
  $('#btn-clear').onclick = () => { $('#input').value = ''; $('#results').innerHTML = '<div class="empty-state"><p>已清空。</p></div>'; };
  $('#btn-sample').onclick = () => { $('#input').value = SAMPLES[0][1]; run(); };
  $('#input').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run(); }
  });
  const a = $('#repo-link');
  const h = location.hostname;
  a.href = h.endsWith('github.io') ? `https://github.com/${h.split('.')[0]}/prompt-lint` : '#';
  if (a.href === '#') a.style.display = 'none';
})();
