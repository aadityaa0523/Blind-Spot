const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = s => String(s).split('/').slice(-2).join('/');
const base = s => String(s).split('/').pop();
let overviewData = { nodes: [], links: [] }, repos = [];
/* ---------- theme: colours come from CSS variables so graphs and gauges follow light/dark ---------- */
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COL = { get node() { return cssv('--graph-node'); }, get edge() { return cssv('--graph-edge'); }, get edge2() { return cssv('--graph-edge2'); },
  get font() { return cssv('--graph-font'); }, get mute() { return cssv('--mute'); }, get risk() { return cssv('--risk'); }, get warn() { return cssv('--warn'); },
  get info() { return cssv('--info'); }, get mem() { return cssv('--mem'); }, get go() { return cssv('--go'); }, get track() { return cssv('--track'); } };
let lastGate = null, lastPreview = null;
const THEME_KEY = 'blindspot-theme';
function setTheme(t, persist) {
  document.documentElement.dataset.theme = t;
  if (persist) { try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* private mode: the choice just won't persist */ } }
  const light = t === 'light', b = $('themebtn');
  if (b) { b.setAttribute('aria-label', light ? 'Switch to dark theme' : 'Switch to light theme'); b.title = (light ? 'Switch to dark theme' : 'Switch to light theme') + ' (t)'; }
  const m = document.querySelector('meta[name="theme-color"]'); if (m) m.content = light ? '#f6f4ee' : '#0a0f1a';
}
function onThemeChange() {  // graphs and gauges draw colours into SVG/canvas, so redraw what is on screen
  const tab = (location.hash || '#home').slice(1).split('/')[0];
  if (lastPreview && $('pv-body').querySelector('.gauge')) renderPreview(lastPreview);
  if ($('p-gate').classList.contains('on') && lastGate) renderGate(lastGate);
  if (tab === 'ask') { askLoaded = false; loadAsk(); }
}


/* ---------- routing ---------- */
const TABS = ['home', 'gate', 'board', 'analytics', 'audit', 'ask', 'proof', 'safety', 'integrate'], APP_TABS = ['gate', 'ask', 'proof'], ANCHORS = ['tour', 'faq'];
let homeLoaded = false, lastTab = null;
function showTab() {
  const [t, arg] = (location.hash || '#home').slice(1).split('/'), anchor = ANCHORS.includes(t) ? t : null;
  const tab = anchor ? 'home' : t === 'check' ? 'gate' : TABS.includes(t) ? t : 'home';
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('on', p.id === 'p-' + tab));
  document.querySelectorAll('.nav a').forEach(a => { if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  $('subbar').hidden = !APP_TABS.includes(tab);
  $('nav').classList.remove('open'); $('menubtn').setAttribute('aria-expanded', 'false');
  if (tab === 'home' && !homeLoaded) loadHome();
  if (tab === 'proof') loadProof();
  if (t === 'check' && arg) loadCheck(arg);
  if (tab === 'board') loadBoard();
  if (tab === 'analytics') loadAnalytics();
  if (tab === 'audit') loadAudit();
  if (tab === 'ask' && !askLoaded) loadAsk();
  if (anchor) setTimeout(() => $(anchor)?.scrollIntoView({ behavior: 'smooth' }), 60);
  else if (tab !== lastTab) window.scrollTo(0, 0);
  lastTab = tab;
  document.title = tab === 'home' ? 'Blindspot: never merge blind' : ({ gate: 'Merge gate', board: 'Board', analytics: 'Analytics', audit: 'Audit log', ask: 'Ask', proof: 'Proof', safety: 'Safety', integrate: 'Integrate' }[tab]) + ' · Blindspot';
}
window.addEventListener('hashchange', showTab);
$('menubtn').onclick = () => { const o = $('nav').classList.toggle('open'); $('menubtn').setAttribute('aria-expanded', String(o)); };


/* ---------- graph helper ---------- */
function drawGraph(el, nodes, links, opts = {}) {
  const ids = new Set(nodes.map(n => n.id)), color = opts.color || (() => COL.node), size = opts.size || (() => 9);
  const data = {
    nodes: nodes.map(n => ({ id: n.id, label: opts.label ? opts.label(n) : short(n.id), title: n.id, color: color(n), size: size(n), shape: 'dot', font: { color: COL.font, size: 12 } })),
    edges: links.filter(l => ids.has(l.source) && ids.has(l.target)).map(l => ({ from: l.source, to: l.target, label: l.label || '', arrows: 'to', dashes: !!l.dashed,
      font: { size: 10, color: COL.mute, strokeWidth: 0 }, color: l.color || COL.edge }))
  };
  const n = new vis.Network(el, data, { physics: { solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -80, springLength: 120 }, stabilization: { iterations: 200 } }, interaction: { hover: true } });
  n.once('stabilizationIterationsDone', () => n.fit({ animation: false }));
  if (opts.onClick) n.on('click', p => { if (p.nodes.length) opts.onClick(p.nodes[0]); });
  return n;
}

/* ---------- repos ---------- */
async function loadRepos(select) {
  repos = await fetch('/api/repos').then(r => r.json());
  $('repo').innerHTML = repos.map(r => `<option value="${esc(r.name)}">${esc((r.url || '').replace('https://github.com/', '') || r.name)}</option>`).join('');
  if (select) $('repo').value = select;
}
const curRepo = () => repos.find(r => r.name === $('repo').value) || {};
function renderExamples() {
  const slug = (curRepo().url || '').replace('https://github.com/', '');
  const ex = slug === 'FalkorDB/GraphRAG-SDK' ? [[335, 'dedup fix'], [326, 'big rework'], [343, 'tiny dependency bump']] : [];
  $('examples').innerHTML = ex.length ? '<span>Try a real PR:</span>' : '';
  ex.forEach(([n, label]) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'ghost'; b.textContent = `#${n} ${label}`;
    b.onclick = () => { $('prurl').value = `https://github.com/${slug}/pull/${n}`; runGate(); }; $('examples').appendChild(b); });
  $('prurl').placeholder = slug ? `https://github.com/${slug}/pull/123` : 'https://github.com/owner/name/pull/123';
}

/* ---------- merge gate ---------- */
const RING = 2 * Math.PI * 54;
function gaugeHTML(score, verdict, id = 'ring') {
  const v = verdict === 'UNKNOWN' ? 0 : score;
  const col = { LOW: COL.go, MEDIUM: COL.warn, HIGH: COL.risk, UNKNOWN: COL.mute }[verdict];
  return `<div class="gauge" role="img" aria-label="Risk score ${v} out of 100, ${verdict}"><svg width="132" height="132" viewBox="0 0 132 132">
    <circle cx="66" cy="66" r="54" fill="none" stroke="${COL.track}" stroke-width="12"/>
    <circle class="ring" id="${id}" cx="66" cy="66" r="54" fill="none" stroke="${col}" stroke-width="12" stroke-linecap="round" stroke-dasharray="${RING}" stroke-dashoffset="${RING}"/></svg>
    <div class="num"><div>${verdict === 'UNKNOWN' ? '?' : v}<small>/ 100</small></div></div></div>`;
}
function renderGate(d) {
  lastGate = d;
  const pr = d.pr, v = d.verdict;
  const actions = `<div class="vactions">${d.check_id ? '<button class="ghost" id="copylink">Copy link</button>' : ''}<button class="ghost" id="copycomment">Copy as PR comment</button></div>` +
    (d.check_id ? `<div class="auditid">Audit record <code>${esc(d.check_id)}</code> · <a href="#audit">verify the chain</a></div>` : '');
  const factors = d.factors.map(f => `<div class="factor"><div class="top"><span>${esc(f.name)}</span><b>${f.points} / ${f.max}</b></div>
      <div class="bar2"><i style="width:${Math.round(100 * f.points / f.max)}%"></i></div><div class="detail">${esc(f.detail)}</div></div>`).join('');
  const comps = d.companions.length ? d.companions.map(c => `<div class="comp"><div class="file">${esc(c.file)}</div>
      <div class="conf"><div class="bar2"><i style="width:${Math.round(c.confidence * 100)}%"></i></div><b>${Math.round(c.confidence * 100)}%</b></div>
      <div class="why">changed together with <code>${esc(base(c.because))}</code> in ${c.times_together} of ${c.of_commits} commits, but isn't in this change</div></div>`).join('')
    : '<div class="empty">Nothing frequent is missing. These files usually travel alone.</div>';
  const revs = d.reviewers.length ? d.reviewers.map(r => `<div class="person"><span><b>${esc(r.person)}</b></span><span>${r.files_covered}/${r.of} files · ${r.commits} commits · last active ${esc(r.last_active)}</span></div>`).join('')
    : '<div class="empty">No reviewer history for these files.</div>';
  const decs = d.decisions.length ? d.decisions.map(x => `<blockquote>${esc(x.rationale.slice(0, 260))}${x.rationale.length > 260 ? '…' : ''}
      <cite>${esc(x.author)} · ${esc(x.date)} · commit ${esc(x.commit)}${x.pr ? ' · PR #' + esc(x.pr) : ''} · ${esc(base(x.file))}</cite></blockquote>`).join('')
    : '<div class="empty">No commit explains why these files look the way they do.</div>';
  const unknown = d.new_or_unknown.length ? `<p class="empty">Not in the graph (new, renamed or deleted): ${d.new_or_unknown.slice(0, 6).map(f => '<code>' + esc(base(f)) + '</code>').join(', ')}${d.new_or_unknown.length > 6 ? ' …' : ''}</p>` : '';
  $('gresult').innerHTML = `<div class="grid"><section>
    <div class="card"><h2>Verdict</h2><div class="verdict">${gaugeHTML(d.score, v)}<div style="flex:1;min-width:200px">
      <span class="tag ${v}">${v} RISK</span><p class="summary">${esc(d.summary)}</p>
      ${pr ? `<div class="prmeta">PR #${esc(pr.number)} · ${esc(pr.title)} · by ${esc(pr.author)} · ${d.files.length} file(s)${pr.truncated ? ' (first 100 shown)' : ''}</div>` : `<div class="prmeta">${d.files.length} file(s)</div>`}</div></div>${unknown}${actions}</div>
    <div class="card"><h2>Why this score <span class="badge">no LLM · graph queries only</span></h2>${factors}</div>
    <div class="card"><h2>Probably forgotten</h2>${comps}</div>
    <div class="card"><h2>Who should review</h2>${revs}</div>
    <div class="card"><h2>Why the code looks like this</h2>${decs}</div></section>
    <section><div class="card" style="position:sticky;top:78px"><h2>Evidence graph</h2><div id="ggraph" role="img" aria-label="Graph linking changed files to missing companions and reviewers"></div>
      <div class="legend"><span><i style="background:${COL.risk}"></i>changed in this PR</span><span><i style="background:${COL.warn}"></i>usually changes too, missing</span><span><i style="background:${COL.info}"></i>reviewer</span></div></div></section></div>`;
  requestAnimationFrame(() => { const r = $('ring'); if (r && d.score != null) r.style.strokeDashoffset = RING * (1 - d.score / 100); });
  const lk = $('copylink'); if (lk) lk.onclick = () => copyText(location.origin + '/#check/' + d.check_id, lk, 'Link copied');
  const cm = $('copycomment'); if (cm) cm.onclick = () => copyText(prComment(d), cm, 'Copied');
  const touched = d.known.slice(0, 12), nodes = new Map();
  touched.forEach(f => nodes.set(f, { id: f, kind: 't' }));
  d.companions.forEach(c => nodes.set(c.file, { id: c.file, kind: 'm' }));
  d.reviewers.forEach(r => nodes.set('@' + r.person, { id: '@' + r.person, kind: 'p' }));
  const links = d.companions.map(c => ({ source: c.because, target: c.file, label: Math.round(c.confidence * 100) + '%', color: COL.warn, dashed: true }));
  d.reviewers.forEach(r => touched.slice(0, 3).forEach(f => links.push({ source: '@' + r.person, target: f, color: COL.edge2 })));
  drawGraph($('ggraph'), [...nodes.values()], links, {
    color: n => ({ t: COL.risk, m: COL.warn, p: COL.info }[n.kind]), size: n => n.kind === 'p' ? 13 : 11,
    label: n => n.kind === 'p' ? n.id.slice(1) : short(n.id) });
}
async function runGate(body) {
  body = body || { pr_url: $('prurl').value.trim(), repo: $('repo').value };
  if (!body.pr_url && !(body.files || []).length) return;
  $('gatego').disabled = true; $('gresult').innerHTML = '<div class="card empty" style="max-width:760px;margin:0 auto">Reading the pull request and walking the graph…</div>';
  try {
    const r = await fetch('/api/gate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) {
      const det = String(d.detail || r.statusText);
      if (det.startsWith('repo_not_ingested:')) {
        const slug = det.split(':')[1];
        $('gresult').innerHTML = `<div class="card err" style="max-width:760px;margin:0 auto">I haven't read <b>${esc(slug)}</b> yet. Build its graph first (about 30 seconds), then check the PR.<br>
          <button id="buildnow">Build graph for ${esc(slug)}</button></div>`;
        $('buildnow').onclick = async () => { try { await buildRepo('https://github.com/' + slug, null); runGate(body); } catch (e) { $('gresult').innerHTML = `<div class="card err">${esc(e.message)}</div>`; } };
        return;
      }
      throw new Error(det);
    }
    renderGate(d);
    if (d.check_id) history.replaceState(null, '', '#check/' + d.check_id);
  } catch (e) { $('gresult').innerHTML = `<div class="card err" style="max-width:760px;margin:0 auto">${esc(e.message)}</div>`; }
  finally { $('gatego').disabled = false; }
}
$('gateform').onsubmit = e => { e.preventDefault(); runGate(); };
$('gfbtn').onclick = () => runGate({ files: $('gfiles').value.split('\n'), repo: $('repo').value });

/* ---------- add repo ---------- */
async function buildRepo(url, logEl) {
  const r = await fetch('/api/repos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
  if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  const { name } = await r.json();
  if (!logEl) { $('joblog').textContent = ''; $('dlg').showModal(); logEl = $('joblog'); }
  for (;;) {
    await new Promise(res => setTimeout(res, 1500));
    const j = await fetch('/api/jobs/' + name).then(x => x.json());
    logEl.textContent = j.log.join('\n') + (j.status === 'error' ? '\nERROR: ' + j.error : ''); logEl.scrollTop = logEl.scrollHeight;
    if (j.status === 'done') { await loadRepos(name); onRepoChange(); if ($('dlg').open) $('dlg').close(); return name; }
    if (j.status === 'error') throw new Error(j.error);
  }
}
$('addbtn').onclick = () => { $('joblog').textContent = ''; $('dlg').showModal(); $('url').focus(); };
$('ingest').onclick = async () => {
  const url = $('url').value.trim(); if (!url) return; $('ingest').disabled = true; $('joblog').textContent = 'starting…';
  try { await buildRepo(url, $('joblog')); } catch (e) { $('joblog').textContent += '\nError: ' + e.message; }
  $('ingest').disabled = false;
};

/* ---------- ask (streaming agent crew) ---------- */
let askLoaded = false;
function pickNode(id) { $('nodeinfo').textContent = id; $('q').value = `Tell me about ${id}: who knows it best, what changes with it, and why is it built this way?`; $('q').focus(); }
let askReady = null;
function loadAsk() { askLoaded = true; askReady = _loadAsk(); return askReady; }
async function _loadAsk() {
  const name = $('repo').value; if (!name) { askLoaded = false; return; }
  const o = await fetch('/api/overview?repo_name=' + name).then(r => r.json()); overviewData = o;
  drawGraph($('graph'), o.nodes, o.links, { label: () => '', onClick: pickNode });
  const a = o.nodes[0]?.id || 'main', b = o.nodes[1]?.id || a;
  const samples = [`Who knows ${short(a)} best, and why was it built the way it is?`, `I'm changing ${short(b)}: what else might break and who should review?`,
                   'What are the most load-bearing files in this repo?', `How is ${short(a)} connected to ${short(o.nodes[2]?.id || b)}?`];
  $('chips').innerHTML = ''; samples.forEach(t => { const x = document.createElement('button'); x.className = 'ghost'; x.textContent = t.length > 46 ? t.slice(0, 44) + '…' : t; x.title = t; x.onclick = () => { $('q').value = t; }; $('chips').appendChild(x); });
  loadMemory();
}
async function loadMemory() {
  const u = $('user').value.trim() || 'anon'; $('mu').textContent = u;
  const rows = await fetch('/api/memory/' + encodeURIComponent(u)).then(r => r.json()), el = $('memgraph');
  if (!rows.length) { el.innerHTML = '<p class="empty" style="padding:14px">Empty. Ask a question and this user\'s memory graph grows.</p>'; return; }
  el.innerHTML = ''; const nodes = {}, links = [];
  rows.forEach(r => { nodes[r.a] = { id: r.a, fact: r.a_label === 'Fact' }; nodes[r.b] = nodes[r.b] || { id: r.b }; links.push({ source: r.a, target: r.b, label: r.rel }); });
  drawGraph(el, Object.values(nodes), links, { color: n => n.fact ? COL.mem : COL.node });
}
function addStep(ev) {
  const el = document.createElement('div'); el.className = 'step ' + ev.agent;
  el.innerHTML = `<span class="who">${esc(ev.agent)}</span><div><b>${esc(ev.action)}</b>${ev.ok === false ? ' <span class="err">(failed)</span>' : ''}<code>${esc((ev.summary || '').slice(0, 220))}</code></div>${ev.ms != null ? `<span class="ms">${esc(ev.ms)} ms</span>` : '<span></span>'}`;
  $('trace').appendChild(el);
}
const ui = { status: null };
function resetAsk(msg) { ui.status = null; $('trace').innerHTML = ''; $('gstate').innerHTML = ''; $('answer').className = 'empty'; $('answer').textContent = msg; }
function handleEvent(ev, replay) {
  if (ui.status) { ui.status.remove(); ui.status = null; }
  if (ev.type === 'status') { ui.status = document.createElement('div'); ui.status.className = 'status'; ui.status.innerHTML = `<span class="dot"></span>${esc(ev.text)}`; $('trace').appendChild(ui.status); }
  else if (ev.type === 'step') addStep(ev);
  else if (ev.type === 'error') throw new Error(ev.text);
  else if (ev.type === 'final') {
    $('answer').className = ''; $('answer').textContent = ev.answer;
    $('gstate').innerHTML = (ev.grounded ? '<span class="badge ok">grounded in graph</span>' : '<span class="badge bad">ungrounded: withheld</span>') + (replay ? ' <span class="badge">recorded run</span>' : '');
    const hot = new Set(ev.graph.nodes.map(n => n.id)), all = [...new Map([...overviewData.nodes, ...ev.graph.nodes].map(n => [n.id, n])).values()];
    drawGraph($('graph'), all, [...overviewData.links, ...ev.graph.links.map(l => ({ ...l, color: COL.risk }))],
      { color: n => hot.has(n.id) ? COL.risk : COL.node, size: n => hot.has(n.id) ? 14 : 9, label: n => hot.has(n.id) ? short(n.id) : '', onClick: pickNode });
    if (!replay) loadMemory();
  }
}
async function ask() {
  const q = $('q').value.trim(); if (!q) return;
  $('go').disabled = $('replay').disabled = true; resetAsk('Agents working…');
  try {
    const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user: $('user').value, question: q, repo: $('repo').value }) });
    if (!r.ok) throw new Error(await r.text());
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { done, value } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ''); buf = buf.slice(i + 2);
        if (line.trim()) handleEvent(JSON.parse(line), false);
      }
    }
  } catch (e) { $('answer').className = 'err'; $('answer').textContent = 'Error: ' + e.message; }
  $('go').disabled = $('replay').disabled = false;
}
let demo = null, demoIdx = 0;
async function replay() {
  $('go').disabled = $('replay').disabled = true;
  try {
    demo = demo || await fetch('/static/demo.json').then(r => r.json());
    const run = demo.runs[demoIdx++ % demo.runs.length];
    if (run.repo !== $('repo').value) throw new Error('The recording was made on the demo repository. Switch the repository selector back to it.');
    $('q').value = run.question; resetAsk('Replaying a recording from ' + demo.recorded + ' (no live model call)…');
    let prev = 0;
    for (const ev of run.events) { await new Promise(res => setTimeout(res, Math.min(1500, (ev.t - prev) * 250 + 250))); prev = ev.t; handleEvent(ev, true); }
  } catch (e) { $('answer').className = 'err'; $('answer').textContent = e.message; }
  $('go').disabled = $('replay').disabled = false;
}
$('replay').onclick = replay;
$('go').onclick = ask;
$('q').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) ask(); });
$('user').onchange = loadMemory;

/* ---------- proof ---------- */
async function loadProof() {
  loadBench();
  const name = $('repo').value; if (!name) return;
  const [s, p] = await Promise.all([fetch('/api/stats?repo_name=' + name).then(r => r.json()), fetch('/api/proof?repo_name=' + name).then(r => r.json())]);
  $('tiles').innerHTML = ['nodes', 'edges', 'commits', 'files', 'people'].map(k => `<span class="tile"><b>${s[k]}</b> ${k}</span>`).join('');
  const pct = x => (100 * x).toFixed(0) + '%';
  $('proofstats').innerHTML = `<div class="stat"><b>${pct(p.recall_at_3)}</b><span>of forgotten files caught in the gate's top 3</span></div>
    <div class="stat"><b>${pct(p.baseline_recall_at_3)}</b><span>caught by guessing the 3 most-changed files</span></div>
    <div class="stat"><b>×${p.lift.toFixed(1)}</b><span>lift over that baseline</span></div>
    <div class="stat"><b>${p.trials}</b><span>held-out-file trials</span></div>`;
  $('proofnote').textContent = `Walk-forward backtest on ${name}: history is replayed commit by commit; for each commit touching 3-10 files, one file is hidden and the gate sees only co-change statistics from earlier commits. ` +
    (p.lift < 1.5 ? 'This repo is small or young, so the signal is weak here. That is expected, and why the gap is listed below.' : '');
}

/* ---------- home (landing) ---------- */
const BUILT_ON = ['FalkorDB', 'Cypher', 'PageRank', 'Shortest path', 'MCP', 'FastAPI', 'Docker', 'git log', 'litellm', 'GitHub API'];
$('track').innerHTML = [...BUILT_ON, ...BUILT_ON].map(x => `<span>${esc(x)}</span>`).join('');
const fmt = n => n == null ? '–' : n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + 'k' : String(n);
function renderPreview(d) {
  lastPreview = d;
  const top = d.companions.slice(0, 3).map(c => `<div class="comp"><div class="file">${esc(short(c.file))}</div><div class="conf"><div class="bar2"><i style="width:${Math.round(c.confidence * 100)}%"></i></div><b>${Math.round(c.confidence * 100)}%</b></div>
      <div class="why">usually changes with <code>${esc(base(c.because))}</code>, not in this PR</div></div>`).join('');
  $('pv-body').innerHTML = `<div><div class="verdict">${gaugeHTML(d.score, d.verdict, 'pvring')}<div style="flex:1;min-width:150px"><span class="tag ${d.verdict}">${d.verdict} RISK</span>
      <div class="prmeta">PR #${esc(d.pr.number)} · ${d.files.length} files</div></div></div><p class="summary">${esc(d.summary)}</p></div>
    <div><h2 style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--mute);margin:0 0 12px">Probably forgotten</h2>${top || '<div class="empty">Nothing frequent is missing.</div>'}</div>`;
  $('pv-title').textContent = `merge gate · ${d.pr.owner || ''}${d.pr.owner ? '/' : ''}${d.pr.name || ''} · PR #${d.pr.number}`;
  requestAnimationFrame(() => { const r = $('pvring'); if (r) r.style.strokeDashoffset = RING * (1 - d.score / 100); });
  $('pv-note').textContent = `Real output for a real pull request, ${d.cached ? 'cached' : 'computed just now'}. ${d.analysis_ms} ms of graph queries, no LLM.`;
}
async function loadHome() {
  homeLoaded = true;
  fillHow();
  const [sum, proof] = await Promise.all([fetch('/api/summary').then(r => r.json()).catch(() => null), fetch('/api/proof?repo_name=repo').then(r => r.json()).catch(() => null)]);
  if (sum) { $('s-repos').textContent = sum.repos; $('s-commits').textContent = fmt(sum.commits); $('s-edges').textContent = fmt(sum.edges);
    $('livetext').textContent = `live · ${sum.repos} repos · FalkorDB`; }
  if (proof) $('s-lift').textContent = '×' + proof.lift.toFixed(1);
  const demoRepo = repos.find(r => (r.url || '').toLowerCase().endsWith('/graphrag-sdk'));
  let ms = sum?.gate_avg_ms;
  if (demoRepo) {
    try {
      const r = await fetch('/api/gate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pr_url: demoRepo.url + '/pull/335', repo: demoRepo.name }) });
      if (!r.ok) throw new Error(r.status);
      const d = await r.json(); renderPreview(d); ms = d.analysis_ms;
    } catch (e) { document.querySelector('.window').hidden = true; $('pv-note').textContent = 'Live preview unavailable right now (GitHub rate limit). Open the merge gate to try a pull request.'; }
  } else { document.querySelector('.window').hidden = true; }
  if (ms != null) $('s-ms').textContent = ms + ' ms';
}

/* ---------- actions (buttons on the landing page) ---------- */
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-action]'); if (!b) return;
  const act = b.dataset.action;
  if (act === 'replay') { location.hash = '#ask'; await new Promise(r => setTimeout(r, 80)); if (askReady) await askReady; replay(); }
  else if (act === 'gate335') {
    const r = repos.find(x => (x.url || '').toLowerCase().endsWith('/graphrag-sdk')); if (!r) return;
    location.hash = '#gate'; $('repo').value = r.name; renderExamples(); $('prurl').value = r.url + '/pull/335'; runGate({ pr_url: r.url + '/pull/335', repo: r.name });
  } else if (act === 'addrepo') { location.hash = '#gate'; $('joblog').textContent = ''; $('dlg').showModal(); $('url').focus(); }
});

/* ---------- helpers ---------- */
const ago = ts => { const s = Math.max(1, Math.round(Date.now() / 1000 - ts)); return s < 60 ? s + ' s ago' : s < 3600 ? Math.round(s / 60) + ' min ago' : s < 86400 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago'; };
const repoLabel = name => { const r = repos.find(x => x.name === name); return r && r.url ? r.url.replace('https://github.com/', '') : name; };
const copyText = async (text, btn, label) => {
  try { await navigator.clipboard.writeText(text); } catch (e) { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
  if (btn) { const old = btn.textContent; btn.textContent = label || 'Copied'; setTimeout(() => { btn.textContent = old; }, 1400); }
};
function prComment(d) {
  const L = [`### Blindspot: ${d.verdict} risk${d.score != null ? ` (${d.score}/100)` : ''}`, '', d.summary, ''];
  if (d.companions && d.companions.length) {
    L.push('**Probably forgotten**', '');
    d.companions.slice(0, 5).forEach(c => L.push(`- \`${c.file}\`: changes with \`${base(c.because)}\` in ${c.times_together} of ${c.of_commits} commits (${Math.round(c.confidence * 100)}%)`));
    L.push('');
  }
  if (d.reviewers && d.reviewers.length) L.push('**Suggested reviewers:** ' + d.reviewers.slice(0, 3).map(r => r.person).join(', '), '');
  L.push(`<sub>Computed from a FalkorDB graph of this repository's history. No LLM.${d.check_id ? ` Check \`${d.check_id}\`.` : ''}</sub>`);
  return L.join('\n');
}

/* ---------- board ---------- */
let boardV = '';
async function loadBoard() {
  const list = await fetch('/api/checks?limit=60' + (boardV ? '&verdict=' + boardV : '')).then(r => r.json());
  $('boardlist').innerHTML = list.length ? list.map(c => `<a class="bcard" href="#check/${esc(c.id)}"><div class="top"><span class="tag ${esc(c.verdict)}">${esc(c.verdict)}</span><b>${c.score ?? '?'}</b></div>
      <div class="t">${esc(c.pr_title || c.n_files + ' files, pasted')}</div>
      <div class="m">${esc(repoLabel(c.repo))}${c.pr_number ? ' · PR #' + esc(c.pr_number) : ''} · ${c.n_files} files · ${esc(ago(c.ts))}</div></a>`).join('')
    : '<div class="empty">No checks yet. Run one on the Merge gate page.</div>';
}
$('boardfilters').onclick = e => {
  const b = e.target.closest('button[data-v]'); if (!b) return; boardV = b.dataset.v;
  document.querySelectorAll('#boardfilters button').forEach(x => x.classList.toggle('on', x === b)); loadBoard();
};

/* ---------- analytics ---------- */
async function loadAnalytics() {
  loadKnowledge();
  const a = await fetch('/api/analytics').then(r => r.json());
  if (!a.total) { $('anstats').innerHTML = '<div class="empty">No checks yet. Run one on the Merge gate page.</div>'; return; }
  const v = a.verdicts || {}, tot = a.total;
  $('anstats').innerHTML = [[a.total, 'checks recorded'], [a.median_ms + ' ms', 'median analysis time'], [a.p95_ms + ' ms', '95th percentile'], [a.mean_score, 'mean risk score']]
    .map(([n, l]) => `<div class="stat"><b>${esc(n)}</b><span>${l}</span></div>`).join('');
  const seg = ['HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'].filter(k => v[k]);
  $('andist').innerHTML = `<div class="stack" role="img" aria-label="${seg.map(k => k + ' ' + v[k]).join(', ')}">${seg.map(k => `<i class="${k}" style="width:${100 * v[k] / tot}%"></i>`).join('')}</div>
    <div class="legend">${seg.map(k => `<span><i class="${k}"></i>${k} ${v[k]}</span>`).join('')}</div>`;
  const list = (arr, f) => arr.length ? arr.map(f).join('') : '<div class="empty">None yet.</div>';
  $('anmissing').innerHTML = list(a.most_forgotten, m => `<div class="person"><span class="mono">${esc(short(m.path))}</span><span>${m.times} checks · avg ${Math.round(m.avg_confidence * 100)}%</span></div>`);
  $('anflagged').innerHTML = list(a.most_flagged_files, m => `<div class="person"><span class="mono">${esc(short(m.path))}</span><span>${m.checks} checks</span></div>`);
  $('anrev').innerHTML = list(a.reviewer_load, m => `<div class="person"><span>${esc(m.name)}</span><span>${m.checks} checks</span></div>`);
  $('anrepos').innerHTML = list(a.repos, m => `<div class="person"><span>${esc(repoLabel(m.repo))}</span><span>${m.checks} checks</span></div>`);
  $('reportlink').href = '/api/report?repo_name=' + encodeURIComponent($('repo').value || 'repo');
}

/* ---------- audit ---------- */
async function loadAudit() {
  const rows = await fetch('/api/checks?limit=200').then(r => r.json());
  document.querySelector('#audittable tbody').innerHTML = rows.length ? rows.map(c => `<tr><td>${c.seq + 1}</td><td>${esc(new Date(c.ts * 1000).toLocaleString())}</td><td>${esc(repoLabel(c.repo))}</td>
      <td><a href="#check/${esc(c.id)}">${esc(c.pr_title ? '#' + c.pr_number + ' ' + c.pr_title.slice(0, 48) : c.n_files + ' files')}</a></td>
      <td><span class="tag ${esc(c.verdict)}">${esc(c.verdict)}</span> ${c.score ?? ''}</td><td>${c.analysis_ms}</td><td class="mono" title="${esc(c.hash)}">${esc(c.hash.slice(0, 10))}…</td></tr>`).join('')
    : '<tr><td colspan="7" class="empty">No checks yet.</td></tr>';
}
$('verifybtn').onclick = async () => {
  $('verifybtn').disabled = true; $('chainstate').innerHTML = '<div class="empty">Recomputing every hash…</div>';
  try {
    const r = await fetch('/api/audit/verify').then(x => x.json());
    $('chainstate').innerHTML = r.ok ? `<b class="yes">✓ ${r.checked} records verified.</b> Every hash matches the record before it.`
      : `<b class="no">✗ The chain is broken at check ${esc(r.broken_at)}.</b> A record before it was edited or removed.`;
  } catch (e) { $('chainstate').innerHTML = '<span class="err">Could not verify: ' + esc(e.message) + '</span>'; }
  $('verifybtn').disabled = false;
};

/* ---------- a stored check (permalink) ---------- */
async function loadCheck(id) {
  $('gresult').innerHTML = '<div class="card empty" style="max-width:760px;margin:0 auto">Opening check…</div>';
  const r = await fetch('/api/checks/' + encodeURIComponent(id));
  if (!r.ok) { $('gresult').innerHTML = '<div class="card err" style="max-width:760px;margin:0 auto">No check with that id.</div>'; return; }
  const c = await r.json();
  if (c.pr_url) $('prurl').value = c.pr_url;
  renderGate({ ...c.result, verdict: c.verdict, score: c.score, check_id: c.id, repo: c.repo, analysis_ms: c.analysis_ms,
    pr: c.pr_number ? { number: c.pr_number, title: c.pr_title, author: c.pr_author } : null });
}

/* ---------- copy buttons on code snippets ---------- */
document.addEventListener('click', e => { const b = e.target.closest('[data-copy]'); if (b) copyText(b.parentElement.querySelector('pre').innerText, b, 'Copied'); });

/* ---------- theme toggle ---------- */
setTheme(document.documentElement.dataset.theme || 'dark', false);
$('themebtn').onclick = () => { setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light', true); onThemeChange(); };
try {  // follow the operating system until the user picks a theme
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', e => { try { if (localStorage.getItem(THEME_KEY)) return; } catch (_) { /* ignore */ } setTheme(e.matches ? 'light' : 'dark', false); onThemeChange(); });
} catch (e) { /* very old browsers */ }

/* ---------- status line, help, keyboard shortcuts ---------- */
function tickClock() { $('livetext').textContent = `live · ${repos.length || '–'} repos · ` + new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }); }
setInterval(tickClock, 1000); tickClock();
$('helpbtn').onclick = () => $('helpdlg').showModal();
let gPending = 0;
document.addEventListener('keydown', e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
  if (e.key === 't') { $('themebtn').click(); return; }
  if (e.key === '?') { $('helpdlg').showModal(); return; }
  if (e.key === '/') { e.preventDefault(); location.hash = '#gate'; setTimeout(() => $('prurl').focus(), 60); return; }
  if (gPending && Date.now() - gPending < 1200) {
    const map = { h: 'home', m: 'gate', b: 'board', a: 'analytics', u: 'audit', k: 'ask', p: 'proof', s: 'safety', i: 'integrate' };
    if (map[e.key]) location.hash = '#' + map[e.key];
    gPending = 0; return;
  }
  if (e.key === 'g') gPending = Date.now();
});

/* ---------- how it works: scroll-driven steps with a sticky preview ---------- */
function initHow() {
  const steps = [...document.querySelectorAll('.hiw-step')], tabs = [...document.querySelectorAll('.hiw-tab')], panes = [...document.querySelectorAll('.hiw-left .hiw-pane')];
  if (!steps.length || steps.length !== panes.length) return;
  steps.forEach((s, i) => { s.querySelector('.hiw-inline').innerHTML = panes[i].innerHTML; });   // on phones each preview sits inside its own step
  const set = i => {
    tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.tabIndex = k === i ? 0 : -1; });
    panes.forEach((p, k) => { p.hidden = k !== i; });
  };
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) set(steps.indexOf(e.target)); }), { rootMargin: '-42% 0px -50% 0px' });
    steps.forEach(s => io.observe(s));
  }
  const go = i => steps[i].scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
  tabs.forEach((t, i) => {
    t.onclick = () => { set(i); go(i); };
    t.onkeydown = e => {
      const k = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key]; if (!k) return;
      e.preventDefault(); const n = (i + k + tabs.length) % tabs.length; tabs[n].focus(); set(n); go(n);
    };
  });
  set(0);
}
async function fillHow() {  // the first preview shows the real size of the demo graph
  try {
    const s = await fetch('/api/stats?repo_name=repo').then(r => r.json());
    for (const [cls, v] of [['.js-commits', s.commits], ['.js-files', s.files], ['.js-people', s.people]]) document.querySelectorAll(cls).forEach(e => { e.textContent = v; });
  } catch (e) { /* keep the static numbers */ }
}

/* ---------- knowledge risk, hidden modules, tenant benchmark ---------- */
let codeowners = '';
async function loadKnowledge() {
  const name = $('repo').value || 'repo'; $('knrepo').textContent = repoLabel(name);
  try {
    const [k, cl] = await Promise.all([fetch('/api/knowledge?repo_name=' + name).then(r => r.json()), fetch('/api/clusters?repo_name=' + name).then(r => r.json())]);
    codeowners = k.codeowners;
    $('knmods').innerHTML = k.modules.length ? k.modules.map(m => `<div class="kn-row"><span class="mono">${esc(m.module)}</span><span><b class="${m.bus_factor === 1 ? 'kn-bus1' : ''}">bus factor ${m.bus_factor}</b> · ${m.people} people · ${esc(m.owners[0].name)} ${Math.round(m.owners[0].share * 100)}%</span></div>`).join('') : '<div class="empty">No module history.</div>';
    $('knfiles').innerHTML = k.single_owner_files.length ? k.single_owner_files.slice(0, 8).map(f => `<div class="kn-row"><span class="mono">${esc(short(f.path))}</span><span>${esc(f.owner)} · ${Math.round(f.share * 100)}% of ${f.commits} commits</span></div>`).join('') : '<div class="empty">No file is dominated by one person.</div>';
    $('knclusters').innerHTML = cl.length ? cl.slice(0, 5).map(c => `<div class="kn-cluster"><div class="head"><b>${c.size} files</b> · ${c.cross_folder ? 'spans ' + c.modules.length + ' folders' : 'one folder'}</div><code>${c.files.slice(0, 5).map(f => esc(base(f))).join(', ')}${c.files.length > 5 ? ' +' + (c.files.length - 5) + ' more' : ''}</code></div>`).join('') : '<div class="empty">No tight clusters found.</div>';
  } catch (e) { for (const id of ['knmods', 'knfiles', 'knclusters']) $(id).innerHTML = '<div class="empty">Could not load: ' + esc(e.message) + '</div>'; }
}
$('co-copy').onclick = () => codeowners && copyText(codeowners, $('co-copy'), 'Copied');
$('co-dl').onclick = () => { if (!codeowners) return; const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([codeowners], { type: 'text/plain' })); a.download = 'CODEOWNERS'; a.click(); URL.revokeObjectURL(a.href); };
async function loadBench() {
  try {
    const b = await fetch('/api/bench').then(r => { if (!r.ok) throw new Error('not run yet'); return r.json(); });
    $('benchstats').innerHTML = [[b.graphs_in_one_instance.toLocaleString(), 'isolated memory graphs in one instance'], [b.p50_ms + ' ms', 'median read'], [b.p95_ms + ' ms', '95th percentile'],
      [b.reads_per_second.toLocaleString(), 'reads per second, ' + b.workers + ' clients'], [b.isolation_violations, 'isolation violations in ' + b.reads.toLocaleString() + ' reads']]
      .map(([n, l]) => `<div class="stat"><b>${esc(n)}</b><span>${esc(l)}</span></div>`).join('');
    $('benchnote').textContent = `Each tenant is its own FalkorDB graph holding a unique token; every read must return only its own. Measured ${b.measured} on one machine (${b.machine}), so treat it as a sanity check, not a capacity claim. Reproduce with python bench_tenants.py.`;
  } catch (e) { $('benchstats').innerHTML = '<div class="empty">Run <code>python bench_tenants.py</code> to produce this.</div>'; }
}

/* ---------- boot ---------- */
function onRepoChange() { renderExamples(); askLoaded = false; $('gresult').innerHTML = ''; showTab(); }
$('repo').onchange = onRepoChange;
initHow();
(async () => { await loadRepos(); onRepoChange(); })();
