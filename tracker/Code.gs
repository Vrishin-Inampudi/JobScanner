/**
 * JobScanner application tracker and dashboard: paste into Extensions → Apps Script on a
 * Google Sheet. See the README ("Track your applications") for the 5-minute setup.
 *
 * - Email links call this with a=applied or a=skip and the job's details; each job is one
 *   row in the "Applications" tab (marking it again updates its status).
 * - JobScanner sends every match here (POST a=matches); they're kept in the "Matches" tab.
 * - a=dashboard shows every match with its status, where you can move an application on
 *   to Interview, Offer or Rejected. You can also just edit the Status cell in the Sheet.
 * - JobScanner reads your marks back with a=list.
 * Every request must carry the secret token set in Project Settings → Script properties.
 *
 * After changing this file: Deploy → Manage deployments → edit → Version: New version.
 */

const SHEET = 'Applications';
const HEADERS = ['Marked', 'Status', 'Company', 'Title', 'Location', 'Link', 'Notes', 'ID', 'Role'];
const STATUS = { applied: 'Applied', skip: 'Not interested', interview: 'Interview', offer: 'Offer', rejected: 'Rejected' };
const MATCHES = 'Matches';
const MATCH_HEADERS = ['Found', 'Company', 'Title', 'Location', 'Pay', 'Fit', 'Why', 'Tags', 'Top', 'Source', 'Link', 'ID', 'Role', 'Draft'];
const KEEP_MATCHES_DAYS = 60;
const MAX_ROWS_PER_POST = 200;

function tokenOk(t) {
  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  return Boolean(token) && t === token;
}

function doGet(e) {
  const p = e.parameter || {};
  if (!tokenOk(p.t)) return page('This link isn\'t valid.', '');

  if (p.a === 'list') {
    const rows = sheet().getDataRange().getValues().slice(1).map(r => ({
      marked: r[0] instanceof Date ? r[0].toISOString() : '', status: r[1], company: r[2], title: r[3], link: r[5],
      id: r[7], role: r[8],
    }));
    return ContentService.createTextOutput(JSON.stringify({ rows })).setMimeType(ContentService.MimeType.JSON);
  }
  if (p.a === 'dashboard') return dashboard(p.t);

  if (!STATUS[p.a] || !p.id) return page('Unknown action.', '');
  mark(p.a, p);
  return page(`Marked “${p.title || 'this job'}” as ${STATUS[p.a]}.`, SpreadsheetApp.getActiveSpreadsheet().getUrl());
}

/** JobScanner posts each run's matches here as JSON: {t, a: 'matches', rows: [...]}. */
function doPost(e) {
  let body = {};
  try { body = JSON.parse(e.postData.contents); } catch (err) { return json({ ok: false, error: 'bad request' }); }
  if (!tokenOk(body.t)) return json({ ok: false, error: 'bad token' });
  if (body.a !== 'matches' || !Array.isArray(body.rows)) return json({ ok: false, error: 'unknown action' });

  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = tab(MATCHES, MATCH_HEADERS);
    const ids = sh.getRange(1, 12, sh.getLastRow(), 1).getValues().map(r => String(r[0]));
    body.rows.slice(0, MAX_ROWS_PER_POST).forEach(m => {
      if (!m || !m.id) return;
      const row = [new Date(m.found || Date.now()), m.company, m.title, m.location, num(m.pay), num(m.fit), m.why, m.tags,
        m.top ? 'yes' : '', m.source, m.link, m.id, m.role, String(m.draft || '').slice(0, 20000)].map(safe);
      const at = ids.indexOf(String(m.id));
      if (at > 0) {
        row[0] = sh.getRange(at + 1, 1).getValue();                         // keep when it was first found
        if (!row[13]) row[13] = sh.getRange(at + 1, 14).getValue();         // ... and an earlier draft
        sh.getRange(at + 1, 1, 1, row.length).setValues([row]);
      } else {
        sh.appendRow(row);
        ids.push(String(m.id));
      }
    });
    prune(sh);
  } finally {
    lock.releaseLock();
  }
  return json({ ok: true });
}

/** Called from the dashboard page (google.script.run) to move a job to another status. */
function setStatus(t, action, job) {
  if (!tokenOk(t) || !STATUS[action] || !job || !job.id) throw new Error('Not allowed.');
  mark(action, job);
  return STATUS[action];
}

function mark(action, p) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sh = sheet();
    const ids = sh.getRange(1, 8, sh.getLastRow(), 1).getValues().map(r => String(r[0]));
    const row = [new Date(), STATUS[action], p.company, p.title, p.location, p.link, '', p.id, p.role].map(safe);
    let at = ids.indexOf(String(p.id));
    if (at > 0) {
      row[6] = sh.getRange(at + 1, 7).getValue();  // keep your notes
      sh.getRange(at + 1, 1, 1, row.length).setValues([row]);
    } else {
      sh.appendRow(row);
      at = sh.getLastRow() - 1;
    }
    // A dropdown on the Status cell, so stages can also be changed right in the Sheet.
    sh.getRange(at + 1, 2).setDataValidation(
      SpreadsheetApp.newDataValidation().requireValueInList(Object.values(STATUS), true).setAllowInvalid(true).build());
  } finally {
    lock.releaseLock();
  }
}

function sheet() { return tab(SHEET, HEADERS); }

function tab(name, headers) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(headers);
    sh.setFrozenRows(1);
  }
  return sh;
}

/** Drop matches older than KEEP_MATCHES_DAYS (they're sorted oldest first, so they're at the top). */
function prune(sh) {
  const cutoff = Date.now() - KEEP_MATCHES_DAYS * 86400000;
  const found = sh.getRange(2, 1, Math.max(sh.getLastRow() - 1, 1), 1).getValues();
  let old = 0;
  while (old < found.length && found[old][0] instanceof Date && found[old][0].getTime() < cutoff) old++;
  if (old > 0 && old < found.length) sh.deleteRows(2, old);
}

function num(v) { return typeof v === 'number' && isFinite(v) ? v : ''; }

// Text from links goes into cells as text, never as a formula.
function safe(v) {
  if (v instanceof Date || typeof v === 'number') return v;
  const s = String(v || '').slice(0, 20000);
  return /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function page(message, sheetUrl) {
  const link = sheetUrl ? `<p><a href="${esc(sheetUrl)}" target="_top">Open your tracker →</a></p>` : '';
  return HtmlService.createHtmlOutput(
    `<div style="font:16px/1.5 -apple-system,Segoe UI,sans-serif;max-width:480px;margin:48px auto;padding:0 16px">
       <p style="font-size:20px;font-weight:600">${esc(message)}</p>${link}</div>`
  ).setTitle('JobScanner tracker');
}

/** Every match with its status, newest first, as data for the dashboard page. */
function dashboardData() {
  const status = {};
  sheet().getDataRange().getValues().slice(1).forEach(r => { status[String(r[7])] = String(r[1]); });
  return tab(MATCHES, MATCH_HEADERS).getDataRange().getValues().slice(1).filter(r => r[11]).map(r => ({
    found: r[0] instanceof Date ? r[0].getTime() : 0, company: String(r[1]), title: String(r[2]), location: String(r[3]),
    pay: typeof r[4] === 'number' ? r[4] : null, fit: typeof r[5] === 'number' ? r[5] : null, why: String(r[6]),
    tags: String(r[7]), top: r[8] === 'yes', source: String(r[9]), link: String(r[10]), id: String(r[11]),
    role: String(r[12]), draft: String(r[13] || ''), status: status[String(r[11])] || '',
  })).reverse();
}

function dashboard(token) {
  // Data goes into the page as JSON; "<" is escaped so no job title can close the script tag.
  const data = JSON.stringify({ jobs: dashboardData(), token: token, statuses: STATUS,
    sheet: SpreadsheetApp.getActiveSpreadsheet().getUrl() }).replace(/</g, '\\u003c');
  return HtmlService.createHtmlOutput(DASHBOARD.replace('__DATA__', () => data))
    .setTitle('JobScanner dashboard').addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

const DASHBOARD = `<!doctype html><html><head><base target="_blank"><style>
  :root { --bg:#191919; --panel:#202020; --line:#2F2F2F; --text:#ECECEC; --muted:#9B9B9B; --red:#EB5757; --blue:#4DA3FF;
          --green:#8FD6A4; --amber:#E9BE74; }
  * { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--text);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif }
  main { max-width:900px; margin:0 auto; padding:24px 16px 48px }
  h1 { font-size:26px; margin:0 0 4px; letter-spacing:-.3px } .sub { color:var(--muted); margin-bottom:16px }
  .bar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px }
  input, select, button { font:inherit; color:var(--text); background:var(--panel); border:1px solid var(--line);
    border-radius:7px; padding:7px 10px } input { flex:1; min-width:160px } button { cursor:pointer }
  button:hover { border-color:#555 }
  .job { background:var(--panel); border-left:4px solid var(--blue); border-radius:8px; padding:12px 14px; margin-bottom:10px;
    overflow-wrap:anywhere } .job.top { border-left-color:var(--red) } .job.done { opacity:.55 }
  .co { font-weight:700; font-size:13px; color:var(--muted) } .title { font-weight:600; font-size:16px; color:var(--text);
    text-decoration:none } .title:hover { text-decoration:underline } .facts, .why { color:var(--muted); font-size:13px }
  .chips { margin:8px 0 4px } .chip { display:inline-block; margin:0 6px 6px 0; padding:2px 8px; border-radius:6px;
    background:var(--line); font-size:12px; font-weight:500 } .chip.fit { color:var(--green) } .chip.st { color:var(--amber) }
  .acts button { font-size:12px; padding:4px 9px; margin:0 6px 4px 0 } .empty { color:var(--muted); padding:32px 0; text-align:center }
  dialog { background:var(--panel); color:var(--text); border:1px solid var(--line); border-radius:10px; max-width:720px; width:92% }
  dialog pre { white-space:pre-wrap; font:13px/1.55 ui-monospace,Menlo,monospace } dialog::backdrop { background:rgba(0,0,0,.6) }
  a { color:var(--blue) }
</style></head><body><main>
  <h1>Your matches</h1><div class="sub" id="sub"></div>
  <div class="bar">
    <input id="q" placeholder="Filter by company, title or city" aria-label="Filter">
    <select id="st" aria-label="Status"></select>
    <select id="sort" aria-label="Sort"><option value="found">Newest first</option><option value="fit">Best fit first</option>
      <option value="pay">Highest pay first</option></select>
  </div>
  <div id="list"></div>
  <dialog id="dlg"><pre id="draft"></pre><button id="copy">Copy</button> <button id="close">Close</button></dialog>
</main><script>
  const D = __DATA__;
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const OPEN = 'To do';
  $('st').append(...['All', OPEN, ...Object.values(D.statuses)].map(s => el('option', '', s)));
  $('st').value = OPEN;
  const ago = t => { const d = Math.floor((Date.now() - t) / 86400000); return d <= 0 ? 'today' : d === 1 ? 'yesterday' : d + ' days ago'; };

  function draw() {
    const q = $('q').value.trim().toLowerCase(), st = $('st').value, by = $('sort').value;
    const jobs = D.jobs.filter(j => (st === 'All' || (st === OPEN ? !j.status : j.status === st)) &&
      (!q || (j.company + ' ' + j.title + ' ' + j.location).toLowerCase().includes(q)))
      .sort((a, b) => (b[by] ?? -1) - (a[by] ?? -1));
    const todo = D.jobs.filter(j => !j.status).length;
    $('sub').replaceChildren(D.jobs.length + ' matches · ' + todo + ' to do · ', Object.assign(el('a', '', 'open the Sheet'), { href: D.sheet }));
    $('list').replaceChildren(...(jobs.length ? jobs.map(card) : [el('div', 'empty', 'Nothing here.')]));
  }

  function card(j) {
    const box = el('div', 'job' + (j.top ? ' top' : '') + (j.status === D.statuses.skip || j.status === D.statuses.rejected ? ' done' : ''));
    box.append(el('div', 'co', j.company + ' · found ' + ago(j.found)));
    const title = el('a', 'title', j.title);
    // Only ever link to web pages: a job link is data from a job site.
    if (/^https:\\/\\//.test(j.link)) { title.href = j.link; title.rel = 'noopener noreferrer'; }
    box.append(title, el('div', 'facts', [j.location.replace(/; /g, ' / '), j.source].filter(Boolean).join(' · ')));
    const chips = el('div', 'chips');
    if (j.status) chips.append(el('span', 'chip st', j.status));
    if (j.fit != null) chips.append(el('span', 'chip fit', 'Fit ' + j.fit));
    if (j.pay != null) chips.append(el('span', 'chip', '$' + Math.round(j.pay) + '/hr'));
    if (j.top) chips.append(el('span', 'chip', '★ Top company'));
    j.tags.split(', ').filter(Boolean).forEach(t => chips.append(el('span', 'chip', t)));
    box.append(chips);
    if (j.why) box.append(el('div', 'why', j.why));
    const acts = el('div', 'acts');
    Object.entries(D.statuses).filter(([, label]) => label !== j.status).forEach(([action, label]) => {
      const b = el('button', '', label);
      b.onclick = () => {
        acts.querySelectorAll('button').forEach(x => x.disabled = true);
        google.script.run.withSuccessHandler(s => { j.status = s; draw(); })
          .withFailureHandler(err => { alert('Could not save: ' + err.message); draw(); })
          .setStatus(D.token, action, { id: j.id, role: j.role, company: j.company, title: j.title, location: j.location, link: j.link });
      };
      acts.append(b);
    });
    if (j.draft) { const b = el('button', '', 'View draft'); b.onclick = () => { $('draft').textContent = j.draft; $('dlg').showModal(); }; acts.append(b); }
    box.append(acts);
    return box;
  }

  $('close').onclick = () => $('dlg').close();
  $('copy').onclick = () => navigator.clipboard.writeText($('draft').textContent);
  ['q', 'st', 'sort'].forEach(id => $(id).addEventListener('input', draw));
  draw();
</script></body></html>`;
