/**
 * JobScanner application tracker: paste into Extensions → Apps Script on a Google Sheet.
 * See the README ("Track your applications") for the 5-minute setup.
 *
 * Email links call this with a=applied or a=skip and the job's details; each job is one
 * row (marking it again updates its status). JobScanner reads rows back with a=list.
 * Every request must carry the secret token set in Project Settings → Script properties.
 */

const SHEET = 'Applications';
const HEADERS = ['Marked', 'Status', 'Company', 'Title', 'Location', 'Link', 'Notes', 'ID', 'Role'];
const STATUS = { applied: 'Applied', skip: 'Not interested' };

function doGet(e) {
  const p = e.parameter || {};
  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (!token || p.t !== token) return page('This link isn\'t valid.', '');

  if (p.a === 'list') {
    const rows = sheet().getDataRange().getValues().slice(1).map(r => ({
      status: r[1], id: r[7], role: r[8],
    }));
    return ContentService.createTextOutput(JSON.stringify({ rows })).setMimeType(ContentService.MimeType.JSON);
  }

  const status = STATUS[p.a];
  if (!status || !p.id) return page('Unknown action.', '');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sh = sheet();
    const ids = sh.getRange(1, 8, sh.getLastRow(), 1).getValues().map(r => String(r[0]));
    const row = [new Date(), status, p.company, p.title, p.location, p.link, '', p.id, p.role].map(safe);
    const at = ids.indexOf(p.id);
    if (at > 0) {
      row[6] = sh.getRange(at + 1, 7).getValue();  // keep your notes
      sh.getRange(at + 1, 1, 1, row.length).setValues([row]);
    } else {
      sh.appendRow(row);
    }
  } finally {
    lock.releaseLock();
  }
  return page(`Marked “${p.title || 'this job'}” as ${status}.`, SpreadsheetApp.getActiveSpreadsheet().getUrl());
}

function sheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET);
  if (!sh) {
    sh = ss.insertSheet(SHEET);
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
  }
  return sh;
}

// Text from links goes into cells as text, never as a formula.
function safe(v) {
  if (v instanceof Date) return v;
  const s = String(v || '').slice(0, 500);
  return /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
}

function page(message, sheetUrl) {
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const link = sheetUrl ? `<p><a href="${esc(sheetUrl)}" target="_top">Open your tracker →</a></p>` : '';
  return HtmlService.createHtmlOutput(
    `<div style="font:16px/1.5 -apple-system,Segoe UI,sans-serif;max-width:480px;margin:48px auto;padding:0 16px">
       <p style="font-size:20px;font-weight:600">${esc(message)}</p>${link}</div>`
  ).setTitle('JobScanner tracker');
}
