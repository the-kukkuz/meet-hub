/* ════════════════════════════════════════════
   MeetHub — Full Operational Logic
   ════════════════════════════════════════════ */
// Ensure API connects to backend on port 8000 even if opened via file:// or Live Server
const API = (window.location.protocol === 'file:' || (window.location.port && window.location.port !== '8000'))
  ? 'http://localhost:8000'
  : window.location.origin;

// ─── State ───
const state = {
  meetings: [],
  currentMeeting: null,
  uploadedFiles: [],
  report: null,
};

// ─── Safe JSON Helper ───
function safeJsonParse(val, fallback = {}) {
  if (val === null || val === undefined) return fallback;
  if (typeof val === 'object') return val;
  try {
    const parsed = JSON.parse(val);
    return parsed !== null && parsed !== undefined ? parsed : fallback;
  } catch {
    return fallback;
  }
}

// ─── DOM Helpers ───
const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);

// ─── UI Transitions ───
function switchView(viewName) {
  $$('.view-section').forEach(v => v.classList.add('hidden'));
  $(`view-${viewName}`).classList.remove('hidden');
  
  if (viewName === 'dashboard') {
    loadMeetings();
  } else if (viewName === 'calendar-section') {
    renderGlobalCalendar();
  } else if (viewName === 'knowledge') {
    initKnowledgeGraphEvents();
    loadKnowledgeEntities();
  }
}

$$('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    switchView(btn.dataset.target);
  });
});

$('btn-back').addEventListener('click', () => {
  switchView('dashboard');
});

// ─── API Helpers ───
async function apiPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

async function apiPatch(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(`${API}${path}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Fetch failed (${res.status})`);
  }
  return res.json();
}

function toast(message, type = 'info') {
  const container = $('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.style.backgroundColor = '#121212';
  el.style.color = '#fff';
  el.style.padding = '12px 24px';
  el.style.border = '1px solid #fff';
  el.style.marginBottom = '10px';
  el.style.fontWeight = '600';
  el.style.fontSize = '0.8rem';
  el.style.textTransform = 'uppercase';
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ─── Dashboard Loading ───
async function loadMeetings() {
  try {
    const meetings = await apiGet('/meetings');
    state.meetings = Array.isArray(meetings) ? meetings : [];
    renderDashboard(state.meetings);
  } catch (err) {
    toast('Error loading dashboard: ' + err.message, 'error');
  }
}

function renderDashboard(meetings) {
  const grid = $('meeting-grid');
  grid.innerHTML = '';
  
  if (!meetings || meetings.length === 0) {
    grid.innerHTML = '<div class="empty-state">No meetings processed yet. Click "Add Meeting" to start.</div>';
    $('global-stats').innerHTML = `
      <div class="stat-mini"><span>0</span> Meetings</div>
      <div class="stat-mini"><span>0</span> Action Items</div>
    `;
    return;
  }

  let totalActions = 0;

  meetings.forEach(m => {
    const speakers = safeJsonParse(m.speakers, []);
    const procData = safeJsonParse(m.processed_data, {});
    const actionItems = Array.isArray(procData.action_items) ? procData.action_items : [];
    const speakerCount = Array.isArray(speakers) ? speakers.length : 0;
    totalActions += actionItems.length;

    const card = document.createElement('div');
    card.className = 'meeting-card';
    card.innerHTML = `
      <h3>${escapeHtml(m.title)}</h3>
      <div class="card-meta">📅 ${new Date(m.date).toLocaleDateString()} • 🔊 ${speakerCount} Speakers</div>
      <div class="card-stats">
        <span>${actionItems.length} Action Items</span>
      </div>
      <button class="btn-card-view" data-id="${m.id}">View Results</button>
    `;
    card.querySelector('button').addEventListener('click', () => {
      showMeetingDetail(m);
    });
    grid.appendChild(card);
  });

  $('global-stats').innerHTML = `
    <div class="stat-mini"><span>${meetings.length}</span> Meetings</div>
    <div class="stat-mini"><span>${totalActions}</span> Action Items</div>
  `;
}

let globalEvents = [];
let calendarViewMode = 'month'; // 'month' | 'timeline'
let calendarCurrentMonth = new Date();

async function renderGlobalCalendar() {
  const grid = $('calendar-grid-cells');
  const timeline = $('calendar-timeline');
  if (grid) grid.innerHTML = '<div class="loading-mini" style="grid-column: span 7;">Syncing calendar...</div>';
  if (timeline) timeline.innerHTML = '<div class="loading-mini">Syncing calendar...</div>';

  try {
    let events = await apiGet('/calendar');

    // If empty, try syncing from saved meetings first
    if (events.length === 0) {
      const syncResult = await apiPost('/calendar/sync', {});
      if (syncResult.events_created > 0) {
        toast(`Synced ${syncResult.events_created} event(s) from saved meetings.`, 'success');
        events = await apiGet('/calendar');
      }
    }

    globalEvents = events;
    renderCalendarView();

  } catch (err) {
    if (grid) grid.innerHTML = '<div class="empty-state" style="grid-column: span 7;">Failed to load calendar events.</div>';
    if (timeline) timeline.innerHTML = '<div class="empty-state">Failed to load calendar events.</div>';
    toast('Calendar Error: ' + err.message, 'error');
  }
}

// ─── Date Resolution ───
const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
  'august', 'september', 'october', 'november', 'december'];
const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function addDays(d, n) { const c = new Date(d); c.setDate(c.getDate() + n); return c; }
function dayDiff(a, b) { return Math.round((startOfDay(a) - startOfDay(b)) / 86400000); }
function monthIndex(token) { return MONTHS.findIndex(m => m.startsWith(token)); }

function safeDate(y, m, d) {
  const date = new Date(y, m, d);
  return (date.getFullYear() === y && date.getMonth() === m && date.getDate() === d) ? date : null;
}

function formatDateKey(d) {
  if (!d) return null;
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

// "17 April" / "December 3" — assume the nearest plausible year to today.
function nearestYear(month, day, today) {
  const candidate = safeDate(today.getFullYear(), month, day);
  if (!candidate) return today.getFullYear();
  const diff = dayDiff(candidate, today);
  if (diff > 183) return today.getFullYear() - 1;
  if (diff < -183) return today.getFullYear() + 1;
  return today.getFullYear();
}

function nextWeekday(today, token, forceNextWeek) {
  const target = WEEKDAYS.findIndex(w => w.startsWith(token));
  if (target < 0) return null;
  let delta = (target - today.getDay() + 7) % 7;
  if (forceNextWeek) delta = delta === 0 ? 7 : delta + 7;
  return addDays(today, delta);
}

function parseExactDate(text) {
  const m = text.match(/\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b/);
  if (m) return safeDate(+m[1], +m[2] - 1, +m[3]);

  const dm = text.match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?\s*,?\s*(\d{4})?\b/i);
  if (dm) {
    const month = monthIndex(dm[2].toLowerCase());
    if (month >= 0) return safeDate(dm[3] ? +dm[3] : new Date().getFullYear(), month, +dm[1]);
  }

  const md = text.match(/\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b/i);
  if (md) {
    const month = monthIndex(md[1].toLowerCase());
    if (month >= 0) return safeDate(new Date().getFullYear(), month, +md[2]);
  }

  const numeric = text.match(/\b(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2,4}))?\b/);
  if (numeric) {
    const a = +numeric[1], b = +numeric[2];
    const month = a > 12 ? b - 1 : a - 1;
    const day = a > 12 ? a : b;
    let year = numeric[3] ? +numeric[3] : null;
    if (year !== null && year < 100) year += 2000;
    if (year !== null) return safeDate(year, month, day);
    const ref = new Date();
    return safeDate(nearestYear(month, day, ref), month, day);
  }

  return null;
}

function parseEventDate(raw, today = new Date()) {
  const text = String(raw ?? '').trim();
  const base = { date: null, raw: text || 'No date stated', inferred: false };

  if (!text) return { ...base, kind: 'unresolved' };

  // Strip prefixes like "[UNRESOLVED]" and "NLP extracted:" to extract underlying dates
  const cleaned = text.replace(/\[UNRESOLVED\]/gi, '').replace(/NLP extracted:\s*/gi, '').trim();
  const hadUnresolvedTag = /\[UNRESOLVED\]/i.test(text);

  const lower = cleaned.toLowerCase();
  const hint = /\b(eod|cob|eo week|eow|end of (the )?day|close of business|end of (the )?week|weekend)\b/.test(lower);

  const exact = parseExactDate(cleaned);
  if (exact) {
    return { date: exact, kind: 'exact', raw: text, cleanDateStr: formatDateKey(exact), timeHint: hint, inferred: false, needsReview: hadUnresolvedTag };
  }

  const today_ = startOfDay(today);

  if (/\bday after tomorrow\b/.test(lower)) return { date: addDays(today_, 2), kind: 'relative', raw: text, cleanDateStr: formatDateKey(addDays(today_, 2)), timeHint: hint, inferred: true };
  if (/\btomorrow\b/.test(lower)) return { date: addDays(today_, 1), kind: 'relative', raw: text, cleanDateStr: formatDateKey(addDays(today_, 1)), timeHint: hint, inferred: true };
  if (/\bend of (the )?month\b|\beom\b/.test(lower)) {
    const eom = new Date(today_.getFullYear(), today_.getMonth() + 1, 0);
    return { date: eom, kind: 'relative', raw: text, cleanDateStr: formatDateKey(eom), timeHint: true, inferred: true };
  }
  if (/\bnext month\b/.test(lower)) {
    const nm = new Date(today_.getFullYear(), today_.getMonth() + 1, today_.getDate());
    return { date: nm, kind: 'relative', raw: text, cleanDateStr: formatDateKey(nm), timeHint: hint, inferred: true };
  }
  if (/\bnext week\b/.test(lower)) {
    const nw = addDays(today_, 7);
    return { date: nw, kind: 'relative', raw: text, cleanDateStr: formatDateKey(nw), timeHint: hint, inferred: true };
  }
  if (/\bnext (mon|tue|wed|thu|fri|sat|sun)[a-z]*\b/.test(lower)) {
    const token = lower.match(/\bnext (mon|tue|wed|thu|fri|sat|sun)[a-z]*\b/)[1];
    const d = nextWeekday(today_, token, true);
    return { date: d, kind: 'relative', raw: text, cleanDateStr: formatDateKey(d), timeHint: hint, inferred: true };
  }
  if (/\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b/.test(lower)) {
    const token = lower.match(/\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b/)[1];
    const d = nextWeekday(today_, token, false);
    return { date: d, kind: 'relative', raw: text, cleanDateStr: formatDateKey(d), timeHint: hint, inferred: true };
  }
  if (/\b(today|asap|now|immediately)\b/.test(lower)) {
    return { date: today_, kind: 'relative', raw: text, cleanDateStr: formatDateKey(today_), timeHint: hint, inferred: true };
  }

  return { ...base, kind: 'unresolved', raw: cleaned || 'Unresolved' };
}

// ─── Helpers ───
const PRIORITY_WEIGHT = { HIGH: 3, MEDIUM: 2, LOW: 1 };

function priorityOf(event) {
  return (event.priority || '').toUpperCase();
}

function relativeLabel(date, today) {
  const diff = dayDiff(date, today);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  if (diff < 0) return `${Math.abs(diff)}d overdue`;
  if (diff < 14) return `in ${diff}d`;
  return `in ${Math.round(diff / 7)}w`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

// ─── Calendar View Switcher ───
function setCalendarView(mode) {
  calendarViewMode = mode;
  const btnMonth = $('btn-view-month');
  const btnTimeline = $('btn-view-timeline');
  const monthView = $('calendar-month-view');
  const timelineView = $('calendar-timeline-view');
  const monthNav = $('month-nav-controls');
  const rangeWrapper = $('timeline-range-filter-wrapper');

  if (mode === 'month') {
    if (btnMonth) btnMonth.classList.add('active');
    if (btnTimeline) btnTimeline.classList.remove('active');
    if (monthView) monthView.classList.remove('hidden');
    if (timelineView) timelineView.classList.add('hidden');
    if (monthNav) monthNav.style.display = 'inline-flex';
    if (rangeWrapper) rangeWrapper.style.display = 'none';
  } else {
    if (btnMonth) btnMonth.classList.remove('active');
    if (btnTimeline) btnTimeline.classList.add('active');
    if (monthView) monthView.classList.add('hidden');
    if (timelineView) timelineView.classList.remove('hidden');
    if (monthNav) monthNav.style.display = 'none';
    if (rangeWrapper) rangeWrapper.style.display = 'flex';
  }
  renderCalendarView();
}

// ─── Master Calendar Renderer ───
function renderCalendarView() {
  const today = startOfDay(new Date());
  const prioFilter = $('calendar-priority-filter') ? $('calendar-priority-filter').value : 'all';
  const statusFilter = $('calendar-status-filter') ? $('calendar-status-filter').value : 'ACTIVE';

  const items = globalEvents.map(e => {
    const parsed = parseEventDate(e.date, today);
    return { event: e, ...parsed, overdue: parsed.date ? dayDiff(parsed.date, today) < 0 : false };
  });

  // Apply status and priority filters
  const filteredItems = items.filter(i => {
    const itemStatus = (i.event.status || 'ACTIVE').toUpperCase();
    if (statusFilter !== 'all') {
      if (statusFilter === 'ACTIVE' && itemStatus !== 'ACTIVE') return false;
      if (statusFilter === 'RESOLVED' && itemStatus === 'ACTIVE') return false;
    }
    if (prioFilter !== 'all') {
      if ((priorityOf(i.event) || '').toUpperCase() !== prioFilter) return false;
    }
    return true;
  });

  // Update header summary
  const summary = $('timeline-summary');
  if (summary) {
    const dated = items.filter(i => i.date);
    const overdueCount = items.filter(i => i.overdue && (i.event.status || 'ACTIVE') === 'ACTIVE').length;
    const resolvedCount = items.filter(i => (i.event.status || '').toUpperCase() === 'RESOLVED').length;
    const undatedCount = items.filter(i => !i.date && (i.event.status || 'ACTIVE') === 'ACTIVE').length;
    const nextUp = dated.filter(i => !i.overdue && (i.event.status || 'ACTIVE') === 'ACTIVE').sort((a, b) => a.date - b.date)[0];

    summary.textContent = [
      `${items.length} total item${items.length === 1 ? '' : 's'}`,
      resolvedCount ? `${resolvedCount} resolved` : null,
      overdueCount ? `${overdueCount} overdue` : null,
      undatedCount ? `${undatedCount} unscheduled` : null,
      nextUp ? `next due: ${nextUp.date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}` : null,
    ].filter(Boolean).join(' · ');
  }

  // Render appropriate view
  if (calendarViewMode === 'month') {
    renderMonthGrid(filteredItems, calendarCurrentMonth, today);
  } else {
    renderTimelineWithItems(filteredItems, today);
  }

  // Always render unscheduled items tray
  renderUnscheduledTray(filteredItems.filter(i => !i.date));
}

// ─── Month Grid View (Work present on each day) ───
function renderMonthGrid(items, currentMonthDate, today) {
  const grid = $('calendar-grid-cells');
  const label = $('cal-month-label');
  if (!grid) return;

  const year = currentMonthDate.getFullYear();
  const month = currentMonthDate.getMonth();

  if (label) {
    label.textContent = currentMonthDate.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  }

  grid.innerHTML = '';

  // Group dated items by YYYY-MM-DD key
  const dateMap = new Map();
  items.filter(i => i.date).forEach(i => {
    const key = formatDateKey(i.date);
    if (!dateMap.has(key)) dateMap.set(key, []);
    dateMap.get(key).push(i);
  });

  const firstDay = new Date(year, month, 1);
  const startDayOfWeek = (firstDay.getDay() + 6) % 7; // Monday = 0
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrevMonth = new Date(year, month, 0).getDate();

  const totalCells = (startDayOfWeek + daysInMonth > 35) ? 42 : 35;

  for (let idx = 0; idx < totalCells; idx++) {
    let cellDate;
    let isOtherMonth = false;

    if (idx < startDayOfWeek) {
      const prevDateNum = daysInPrevMonth - startDayOfWeek + idx + 1;
      cellDate = new Date(year, month - 1, prevDateNum);
      isOtherMonth = true;
    } else if (idx >= startDayOfWeek + daysInMonth) {
      const nextDateNum = idx - (startDayOfWeek + daysInMonth) + 1;
      cellDate = new Date(year, month + 1, nextDateNum);
      isOtherMonth = true;
    } else {
      const curDateNum = idx - startDayOfWeek + 1;
      cellDate = new Date(year, month, curDateNum);
      isOtherMonth = false;
    }

    const key = formatDateKey(cellDate);
    const dayTasks = dateMap.get(key) || [];
    const isToday = dayDiff(cellDate, today) === 0;
    const hasHighPrio = dayTasks.some(t => priorityOf(t.event) === 'HIGH' && (t.event.status || 'ACTIVE') === 'ACTIVE');

    const cell = document.createElement('div');
    cell.className = `cal-day-cell ${isOtherMonth ? 'other-month' : ''} ${isToday ? 'is-today' : ''} ${dayTasks.length > 0 ? 'has-events' : ''}`;
    cell.setAttribute('role', 'button');
    cell.setAttribute('tabindex', '0');

    // Header with Day number and optional task badge
    const header = document.createElement('div');
    header.className = 'cal-cell-header';
    header.innerHTML = `
      <span class="cal-day-num">${cellDate.getDate()}</span>
      ${dayTasks.length > 0 ? `<span class="cal-day-badge ${hasHighPrio ? 'has-high' : ''}">${dayTasks.length}</span>` : ''}
    `;
    cell.appendChild(header);

    // Tasks container for work present on this day
    const tasksContainer = document.createElement('div');
    tasksContainer.className = 'cal-day-tasks';

    const maxVisible = 3;
    dayTasks.slice(0, maxVisible).forEach(task => {
      const chip = document.createElement('div');
      const prio = (priorityOf(task.event) || 'none').toLowerCase();
      const status = (task.event.status || 'ACTIVE').toLowerCase();
      chip.className = `cal-task-chip prio-${prio} status-${status}`;
      chip.title = `${task.event.title} (${task.event.owner || 'Unassigned'}) [${status.toUpperCase()}]`;
      chip.innerHTML = `
        <span class="cal-chip-title">${escapeHtml(task.event.title)}</span>
        ${task.event.owner ? `<span class="cal-chip-owner">@${escapeHtml(task.event.owner)}</span>` : ''}
      `;
      chip.addEventListener('click', (e) => {
        e.stopPropagation();
        openDayModal(cellDate, dayTasks);
      });
      tasksContainer.appendChild(chip);
    });

    if (dayTasks.length > maxVisible) {
      const more = document.createElement('div');
      more.className = 'cal-more-chip';
      more.textContent = `+${dayTasks.length - maxVisible} more`;
      tasksContainer.appendChild(more);
    }

    cell.appendChild(tasksContainer);

    cell.addEventListener('click', () => {
      openDayModal(cellDate, dayTasks);
    });

    cell.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDayModal(cellDate, dayTasks);
      }
    });

    grid.appendChild(cell);
  }
}

// ─── Day Detail Modal ───
function openDayModal(date, itemsForDay) {
  const modal = $('cal-day-modal');
  const dateEl = $('cal-modal-date');
  const subEl = $('cal-modal-subtitle');
  const bodyEl = $('cal-modal-items');
  if (!modal || !dateEl || !bodyEl) return;

  const dateStr = date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  dateEl.textContent = dateStr;
  subEl.textContent = `${itemsForDay.length} action item${itemsForDay.length === 1 ? '' : 's'} on this day`;

  bodyEl.innerHTML = '';

  if (itemsForDay.length === 0) {
    bodyEl.innerHTML = '<div class="empty-state">No action items scheduled for this day.</div>';
  } else {
    itemsForDay.forEach(item => {
      const prio = (priorityOf(item.event) || 'none').toLowerCase();
      const status = (item.event.status || 'ACTIVE').toLowerCase();
      const isResolved = status === 'resolved' || status === 'cancelled';

      const card = document.createElement('div');
      card.className = `modal-task-item prio-${prio} status-${status}`;
      card.innerHTML = `
        <div class="modal-task-top">
          <h4 class="modal-task-title" style="${isResolved ? 'text-decoration: line-through; opacity: 0.8;' : ''}">${escapeHtml(item.event.title)}</h4>
          <div style="display:flex; gap:6px; align-items:center;">
            <span class="status-badge ${status}">${escapeHtml(status.toUpperCase())}</span>
            <span class="priority-badge ${escapeHtml(prio)}">${escapeHtml(item.event.priority || 'unset')}</span>
          </div>
        </div>
        <div class="modal-task-meta">
          <span class="modal-task-owner">👤 ${escapeHtml(item.event.owner || 'Unassigned')}</span>
          ${item.timeHint ? '<span class="tl-tag">Time-bound</span>' : ''}
          ${item.needsReview ? '<span class="tl-tag" style="background:#FFF3E0;color:#E65100;">Needs Review</span>' : ''}
        </div>
        ${item.event.description ? `<p class="modal-task-desc">${escapeHtml(item.event.description)}</p>` : ''}
        <div class="modal-task-evidence">Transcript context: “${escapeHtml(item.raw)}”</div>
        ${item.event.resolution_reason ? `
          <div class="resolution-box ${status}">
            <strong>Reasoning for Resolution:</strong>
            ${escapeHtml(item.event.resolution_reason)}
          </div>
        ` : ''}
        <div style="margin-top:10px; display:flex; gap:8px;">
          ${!isResolved ? `<button class="btn-small btn-resolve-task" data-id="${item.event.id}" style="cursor:pointer;">✓ Resolve</button>` : ''}
          <button class="btn-small btn-delete-task" data-id="${item.event.id}" style="cursor:pointer; background:#5c1d1d; color:#fff;">Delete</button>
        </div>
      `;

      const btnResolve = card.querySelector('.btn-resolve-task');
      if (btnResolve) {
        btnResolve.addEventListener('click', async (e) => {
          e.stopPropagation();
          try {
            await apiPatch(`/calendar/${item.event.id}/resolve`, {
              reason: 'Manually marked as resolved in calendar view.',
              status: 'RESOLVED'
            });
            toast('Task resolved!', 'success');
            closeDayModal();
            renderGlobalCalendar();
          } catch (err) {
            toast('Failed to resolve task: ' + err.message, 'error');
          }
        });
      }

      const btnDelete = card.querySelector('.btn-delete-task');
      if (btnDelete) {
        btnDelete.addEventListener('click', async (e) => {
          e.stopPropagation();
          try {
            await apiDelete(`/calendar/${item.event.id}`);
            toast('Task deleted!', 'success');
            closeDayModal();
            renderGlobalCalendar();
          } catch (err) {
            toast('Failed to delete task: ' + err.message, 'error');
          }
        });
      }

      bodyEl.appendChild(card);
    });
  }

  modal.classList.remove('hidden');
}

function closeDayModal() {
  const modal = $('cal-day-modal');
  if (modal) modal.classList.add('hidden');
}

// ─── Unscheduled Tasks Tray ───
function renderUnscheduledTray(undatedItems) {
  const tray = $('unscheduled-tray');
  const countEl = $('unscheduled-count');
  const listEl = $('unscheduled-list');
  if (!tray || !listEl) return;

  if (countEl) countEl.textContent = `(${undatedItems.length})`;

  if (undatedItems.length === 0) {
    tray.style.display = 'none';
    return;
  }

  tray.style.display = 'block';
  listEl.innerHTML = '';

  undatedItems.forEach(item => {
    const card = document.createElement('div');
    const prio = (priorityOf(item.event) || 'none').toLowerCase();
    card.className = 'unscheduled-card';
    card.innerHTML = `
      <div class="unscheduled-card-top">
        <span class="unscheduled-card-title">${escapeHtml(item.event.title)}</span>
        <span class="priority-badge ${escapeHtml(prio)}">${escapeHtml(item.event.priority || 'unset')}</span>
      </div>
      <div class="unscheduled-card-meta">
        👤 ${escapeHtml(item.event.owner || 'Unassigned')} · “${escapeHtml(item.raw)}”
      </div>
    `;
    listEl.appendChild(card);
  });
}

// ─── Timeline / Agenda View ───
function matchesRange(item, range, today) {
  if (range === 'all') return true;
  if (!item.date) return range !== 'overdue';
  const diff = dayDiff(item.date, today);
  if (range === 'overdue') return diff < 0;
  if (range === 'upcoming') return diff >= 0;
  return diff >= 0 && diff <= Number(range);
}

function buildTimelineItem(item) {
  const el = document.createElement('article');
  el.className = `tl-card prio-${(priorityOf(item.event) || 'none').toLowerCase()}`;
  el.innerHTML = `
    <div class="tl-card-top">
      <h3 class="tl-card-title">${escapeHtml(item.event.title)}</h3>
      <span class="priority-badge ${escapeHtml((priorityOf(item.event) || 'none').toLowerCase())}">${escapeHtml(item.event.priority || 'unset')}</span>
    </div>
    <div class="tl-card-meta">
      ${item.event.owner ? `<span class="tl-owner">${escapeHtml(item.event.owner)}</span>` : ''}
      <span class="tl-raw" title="As stated in the transcript">“${escapeHtml(item.raw)}”</span>
      ${item.timeHint ? '<span class="tl-tag">Time-bound</span>' : ''}
    </div>
    ${item.event.description ? `<p class="tl-card-desc">${escapeHtml(item.event.description)}</p>` : ''}
  `;
  return el;
}

function buildMonthHeader(date, count, overrideLabel) {
  const el = document.createElement('div');
  el.className = 'tl-month';
  const label = overrideLabel
    ? overrideLabel
    : date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  el.innerHTML = `
    <div class="tl-month-rail"></div>
    <div class="tl-month-head">
      <span class="tl-month-label">${escapeHtml(label)}</span>
      <span class="tl-month-count">${count} item${count === 1 ? '' : 's'}</span>
    </div>
  `;
  return el;
}

function buildNowMarker(today) {
  const el = document.createElement('div');
  el.className = 'tl-now';
  const label = today.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  el.innerHTML = `<div class="tl-now-rail"></div><div class="tl-now-pill">Now · ${escapeHtml(label)}</div>`;
  return el;
}

function buildDayRow(day, today) {
  const row = document.createElement('div');
  row.className = 'tl-row';

  const diff = dayDiff(day.date, today);
  const dateCell = document.createElement('div');
  dateCell.className = 'tl-date';
  dateCell.innerHTML = `
    <div class="tl-date-day">${day.date.getDate()}</div>
    <div class="tl-date-month">${day.date.toLocaleDateString(undefined, { month: 'short' })}</div>
    <div class="tl-date-week">${day.date.toLocaleDateString(undefined, { weekday: 'short' })}</div>
    <div class="tl-date-rel ${diff < 0 ? 'overdue' : diff === 0 ? 'today' : ''}">${escapeHtml(relativeLabel(day.date, today))}</div>
  `;

  const rail = document.createElement('div');
  rail.className = 'tl-rail';
  const dot = document.createElement('span');
  dot.className = `tl-dot${diff === 0 ? ' today' : diff < 0 ? ' overdue' : ''}`;
  rail.appendChild(dot);

  const items = document.createElement('div');
  items.className = 'tl-items';
  day.items.forEach(item => items.appendChild(buildTimelineItem(item)));

  row.append(dateCell, rail, items);
  return row;
}

function buildUndatedRow(item) {
  const row = document.createElement('div');
  row.className = 'tl-row tl-row-undated';

  const dateCell = document.createElement('div');
  dateCell.className = 'tl-date';
  dateCell.innerHTML = '<div class="tl-date-rel undated">Unscheduled</div>';

  const rail = document.createElement('div');
  rail.className = 'tl-rail dashed';
  const dot = document.createElement('span');
  dot.className = 'tl-dot open';
  rail.appendChild(dot);

  const items = document.createElement('div');
  items.className = 'tl-items';
  items.appendChild(buildTimelineItem(item));

  row.append(dateCell, rail, items);
  return row;
}

function renderTimelineWithItems(items, today) {
  const root = $('calendar-timeline');
  if (!root) return;
  root.innerHTML = '';

  const range = $('calendar-range') ? $('calendar-range').value : 'all';
  const visible = items.filter(i => matchesRange(i, range, today));

  if (visible.length === 0) {
    root.innerHTML = '<div class="empty-state">No items match this filter.</div>';
    return;
  }

  const dated = visible.filter(i => i.date).sort((a, b) => a.date - b.date);
  const undated = visible.filter(i => !i.date);

  const days = [];
  dated.forEach(item => {
    const key = item.date.toDateString();
    let group = days.find(d => d.key === key);
    if (!group) {
      group = { key, date: item.date, items: [] };
      days.push(group);
    }
    group.items.push(item);
  });

  let nowMarkerPlaced = false;
  let lastMonth = '';

  days.forEach(day => {
    const monthKey = `${day.date.getFullYear()}-${day.date.getMonth()}`;
    if (monthKey !== lastMonth) {
      lastMonth = monthKey;
      root.appendChild(buildMonthHeader(day.date, day.items.length));
    }

    if (!nowMarkerPlaced && dayDiff(day.date, today) >= 0) {
      nowMarkerPlaced = true;
      root.appendChild(buildNowMarker(today));
    }

    root.appendChild(buildDayRow(day, today));
  });

  if (!nowMarkerPlaced && dated.length) root.appendChild(buildNowMarker(today));

  if (undated.length) {
    root.appendChild(buildMonthHeader(null, undated.length, 'Needs a date'));
    undated.forEach(item => root.appendChild(buildUndatedRow(item)));
  }
}

// ─── Attach Calendar Controls Event Listeners ───
document.addEventListener('DOMContentLoaded', () => {
  const btnMonth = $('btn-view-month');
  const btnTimeline = $('btn-view-timeline');
  if (btnMonth) btnMonth.addEventListener('click', () => setCalendarView('month'));
  if (btnTimeline) btnTimeline.addEventListener('click', () => setCalendarView('timeline'));

  const prevBtn = $('cal-prev-month');
  const nextBtn = $('cal-next-month');
  const todayBtn = $('cal-today-btn');

  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      calendarCurrentMonth.setMonth(calendarCurrentMonth.getMonth() - 1);
      renderCalendarView();
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      calendarCurrentMonth.setMonth(calendarCurrentMonth.getMonth() + 1);
      renderCalendarView();
    });
  }
  if (todayBtn) {
    todayBtn.addEventListener('click', () => {
      calendarCurrentMonth = new Date();
      renderCalendarView();
    });
  }

  const prioSelect = $('calendar-priority-filter');
  if (prioSelect) prioSelect.addEventListener('change', renderCalendarView);

  const statusSelect = $('calendar-status-filter');
  if (statusSelect) statusSelect.addEventListener('change', renderCalendarView);

  const rangeSelect = $('calendar-range');
  if (rangeSelect) rangeSelect.addEventListener('change', renderCalendarView);

  const modalClose = $('cal-modal-close');
  const modalBackdrop = $('cal-modal-backdrop');
  if (modalClose) modalClose.addEventListener('click', closeDayModal);
  if (modalBackdrop) modalBackdrop.addEventListener('click', closeDayModal);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDayModal();
  });
});


// ─── File Handling ───
const dropZone = $('drop-zone');
const fileInput = $('file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  handleFiles(e.dataTransfer.files);
});

fileInput.addEventListener('change', () => handleFiles(fileInput.files));

function handleFiles(files) {
  const list = $('file-list');
  for (const file of files) {
    const nameLower = file.name.toLowerCase();
    if (!nameLower.endsWith('.txt') && !nameLower.endsWith('.vtt') && !nameLower.endsWith('.md')) {
      toast(`${file.name} is not a supported format. Use .txt, .vtt, or .md.`, 'error');
      continue;
    }
    state.uploadedFiles.push(file);
    const item = document.createElement('div');
    item.className = 'file-item';
    item.textContent = `📄 ${file.name} (${Math.round(file.size / 1024)} KB)`;
    list.appendChild(item);
  }
}

// ─── Main Submit Event ───
$('btn-submit').addEventListener('click', async () => {
  if (state.uploadedFiles.length > 0) {
    toast(`Reading ${state.uploadedFiles.length} files...`);
  }

  const transcripts = await collectTranscripts();

  if (transcripts.length === 0) {
    toast('Please upload or enter a transcript.', 'error');
    return;
  }

  const btn = $('btn-submit');
  const spinner = $('spinner');
  btn.disabled = true;
  spinner.classList.remove('hidden');

  try {
    for (let i = 0; i < transcripts.length; i++) {
        const transcript = transcripts[i];
        toast(`Processing transcript ${i+1}/${transcripts.length}...`);
        
        const [processData, extractData] = await Promise.all([
          apiPost('/transcripts/process', { transcript }),
          apiPost('/transcripts/extract', { transcript })
        ]);

        const sentimentData = await apiPost('/meetings/sentiment', { 
          transcript: processData.clean_text 
        });

        const summaryData = await apiPost('/meetings/summary', {
          decisions: extractData.decisions,
          action_items: extractData.action_items,
          sentiment_timeline: sentimentData.timeline,
          speaker_summary: sentimentData.speaker_summary,
        });

        const titleMatch = transcript.match(/^\s*(?:meeting|agenda|topic|title)\s*[:\-]\s*(.+)$/im);
        const detectedTitle = titleMatch ? titleMatch[1].trim() : (extractData.decisions && extractData.decisions[0] ? extractData.decisions[0].text : `Meeting ${new Date().toLocaleDateString()}`);

        const meetingObject = {
          title: detectedTitle,
          transcript: transcript,
          word_count: processData.metadata.word_count,
          speakers: processData.metadata.speakers,
          processed_data: {
            ...extractData,
            summary: summaryData.summary,
            sentiment: sentimentData
          }
        };

        const saveResult = await apiPost('/meetings', meetingObject);
        if (saveResult && saveResult.resolutions && saveResult.resolutions.length > 0) {
          saveResult.resolutions.forEach(r => {
            toast(`Resolved calendar item: "${r.title}" (${r.reason})`, 'success');
          });
        }
    }

    toast('Analysis Complete! Returning to Dashboard.', 'success');
    state.uploadedFiles = [];
    $('file-list').innerHTML = '';
    $('transcript-input').value = '';
    switchView('dashboard');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    spinner.classList.add('hidden');
  }
});

// ─── Structured Report ───
async function collectTranscripts() {
  const transcripts = [];

  if (state.uploadedFiles.length > 0) {
    for (const file of state.uploadedFiles) {
      transcripts.push(await file.text());
    }
  } else {
    const raw = $('transcript-input').value.trim();
    if (raw) transcripts.push(raw);
  }

  return transcripts;
}

function reportItem(list, { title, meta = [], description = null, evidence = null }) {
  const item = document.createElement('div');
  item.className = 'report-item';

  const head = document.createElement('div');
  head.className = 'report-item-head';
  head.innerHTML = `<span class="report-item-title">${title}</span>`;
  item.appendChild(head);

  if (meta.length) {
    const metaRow = document.createElement('div');
    metaRow.className = 'report-item-meta';
    meta.forEach(({ label, value }) => {
      const tag = document.createElement('span');
      tag.className = value ? 'meta-tag' : 'meta-tag null-tag';
      tag.innerHTML = `<em>${label}</em> ${value || 'not stated'}`;
      metaRow.appendChild(tag);
    });
    item.appendChild(metaRow);
  }

  if (description) {
    const desc = document.createElement('p');
    desc.className = 'report-item-desc';
    desc.textContent = description;
    item.appendChild(desc);
  }

  if (evidence) {
    const quote = document.createElement('blockquote');
    quote.className = 'evidence';
    quote.textContent = `“${evidence}”`;
    item.appendChild(quote);
  }

  list.appendChild(item);
  return item;
}

function reportSection(title, count, emptyMessage) {
  const card = document.createElement('div');
  card.className = 'result-card';

  const header = document.createElement('div');
  header.className = 'card-header';
  header.innerHTML = `<h3 class="section-title">${title} (${count})</h3>`;
  card.appendChild(header);

  const list = document.createElement('div');
  list.className = 'report-list';
  if (count === 0) {
    list.innerHTML = `<div class="report-empty">${emptyMessage}</div>`;
  }
  card.appendChild(list);
  return { card, list };
}

function renderReport(report) {
  const output = $('report-output');
  output.innerHTML = '';

  // ── Title + summary ──
  const header = document.createElement('div');
  header.className = 'result-card report-header-card';
  header.innerHTML = `
    <h3 class="report-title">${report.meeting_title || 'Untitled Meeting'}</h3>
    <p class="report-summary">${report.summary}</p>
  `;
  output.appendChild(header);

  // ── Decisions ──
  const decisions = reportSection(
    'Key Decisions', report.decisions.length,
    'No finalized decisions were stated in the transcript.'
  );
  report.decisions.forEach(d => reportItem(decisions.list, {
    title: d.decision,
    evidence: d.evidence,
  }));
  output.appendChild(decisions.card);

  // ── Tasks ──
  const tasks = reportSection(
    'Action Items', report.tasks.length,
    'No confirmed action items were found in the transcript.'
  );
  report.tasks.forEach(t => reportItem(tasks.list, {
    title: t.title,
    meta: [
      { label: 'Assignee', value: t.assignee },
      { label: 'Due', value: t.due_date },
    ],
    description: t.description,
    evidence: t.evidence,
  }));
  output.appendChild(tasks.card);

  // ── Blockers ──
  const blockers = reportSection(
    'Risks, Blockers & Open Questions', report.blockers.length,
    'No risks, blockers or open questions were raised.'
  );
  report.blockers.forEach(b => reportItem(blockers.list, {
    title: b.issue,
    evidence: b.evidence,
  }));
  output.appendChild(blockers.card);

  // ── Raw JSON ──
  $('report-json').textContent = JSON.stringify(report, null, 2);
  $('report-json-card').classList.remove('hidden');
  $('btn-copy-json').classList.remove('hidden');
}

async function generateReport({ navigate = false } = {}) {
  if (navigate) switchView('report');

  const btn = $('btn-generate-report');
  const spinner = $('report-spinner');
  btn.disabled = true;
  spinner.classList.remove('hidden');

  try {
    const transcripts = await collectTranscripts();
    if (transcripts.length === 0) {
      toast('Add a transcript file or paste text first.', 'error');
      return;
    }

    const reports = [];
    for (const transcript of transcripts) {
      reports.push(await apiPost('/transcripts/analyze', { transcript }));
    }

    state.report = reports.length === 1 ? reports[0] : reports;
    renderReport(state.report);
    if (reports.length > 1) {
      toast(`Generated ${reports.length} reports.`, 'success');
    }
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    spinner.classList.add('hidden');
  }
}

$('btn-generate-report').addEventListener('click', () => generateReport());
$('btn-report-from-intake').addEventListener('click', () => generateReport({ navigate: true }));

$('btn-copy-json').addEventListener('click', async () => {
  if (!state.report) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.report, null, 2));
    toast('Report JSON copied to clipboard.', 'success');
  } catch (err) {
    toast('Clipboard unavailable: ' + err.message, 'error');
  }
});

// ─── Detail View ───
function showMeetingDetail(m) {
  state.currentMeeting = m;
  const data = safeJsonParse(m.processed_data, {});
  const speakersArr = safeJsonParse(m.speakers, []);
  const summary = data.summary || {};
  const actionItems = Array.isArray(data.action_items) ? data.action_items : [];

  $('meeting-title').textContent = m.title || 'Untitled Meeting';
  $('meeting-meta').textContent = `${new Date(m.date).toLocaleDateString()} • ${m.word_count || 0} Words • ${speakersArr.length} Speakers`;
  
  // Render Main Points
  const hList = $('main-points-list');
  hList.innerHTML = '';
  const highlights = [
    { label: 'Key Decision', value: summary.key_decision },
    { label: 'Urgent Action', value: summary.urgent_action },
    { label: 'Vibe', value: summary.overall_sentiment }
  ];
  highlights.forEach(h => {
    if (!h.value) return;
    const div = document.createElement('div');
    div.className = 'highlight-item';
    div.innerHTML = `<h4>${h.label}</h4><p>${h.value}</p>`;
    hList.appendChild(div);
  });

  // Render Work
  const workBody = $('work-body');
  workBody.innerHTML = '';
  if (actionItems.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="3" style="text-align:center; opacity:0.6;">No action items recorded for this meeting.</td>`;
    workBody.appendChild(tr);
  } else {
    actionItems.forEach(a => {
      const tr = document.createElement('tr');
      const task = a.task || a.what || 'Unnamed task';
      const owner = a.owner || a.who || 'Unassigned';
      const when = a.deadline || a.when || '-';
      tr.innerHTML = `<td>${escapeHtml(task)}</td><td>${escapeHtml(owner)}</td><td>${escapeHtml(when)}</td>`;
      workBody.appendChild(tr);
    });
  }

  // Render Sentiment Chart (Simplified color bars)
  const timeline = $('sentiment-timeline');
  timeline.innerHTML = '';
  if (data.sentiment && Array.isArray(data.sentiment.timeline)) {
    data.sentiment.timeline.forEach(t => {
      const bar = document.createElement('div');
      const sent = (t.sentiment || 'Neutral').toLowerCase();
      bar.className = `sentiment-bar ${sent}`;
      bar.title = `${t.speaker || 'Speaker'}: ${t.sentiment || 'Neutral'}`;
      bar.addEventListener('click', () => {
        toast(`${t.speaker || 'Speaker'}: "${t.segment || ''}"`);
      });
      timeline.appendChild(bar);
    });
  }

  // Render Speaker Sentiment
  const sSentiment = $('speakers-sentiment');
  sSentiment.innerHTML = '';
  if (data.sentiment && data.sentiment.speaker_summary) {
    const list = Array.isArray(data.sentiment.speaker_summary)
      ? data.sentiment.speaker_summary
      : Object.values(data.sentiment.speaker_summary);
    list.forEach(item => {
      const spk = item.speaker || 'Unknown';
      const sent = item.overall_sentiment || 'Neutral';
      const tone = item.dominant_tone ? ` (${item.dominant_tone})` : '';
      const badge = document.createElement('div');
      badge.className = 'speaker-sentiment-badge';
      badge.innerHTML = `<strong>${escapeHtml(spk)}</strong>: ${escapeHtml(sent)}${escapeHtml(tone)}`;
      sSentiment.appendChild(badge);
    });
  }

  // Reset Chat
  $('chat-history').innerHTML = '<div class="msg bot">Ask me anything about this meeting or project context.</div>';
  switchView('detail');
}

// ─── Chatbot Logic ───
$('btn-chat').addEventListener('click', async () => {
  const query = $('chat-input').value.trim();
  if (!query) return;

  appendMessage('user', query);
  $('chat-input').value = '';

  try {
    if (!state.currentMeeting) {
      appendMessage('bot', 'No meeting selected. Please view a meeting first.');
      return;
    }
    const currentData = safeJsonParse(state.currentMeeting.processed_data, {});
    const summaryData = currentData.summary || {};
    const contextList = [summaryData.key_decision, state.currentMeeting.transcript].filter(Boolean);
    const res = await apiPost('/meetings/query', { 
      query, 
      context: contextList.length ? contextList : [state.currentMeeting.transcript || ""] 
    });
    let answerText = res.answer || "No response received.";
    if (res.sources && res.sources.length > 0) {
      const sourcesText = res.sources
        .map(s => `• [${s.meeting}] "${s.snippet}"`)
        .join('\n');
      answerText += `\n\nSources:\n${sourcesText}`;
    }
    appendMessage('bot', answerText);
  } catch (err) {
    appendMessage('bot', 'Error contacting intelligence: ' + err.message);
  }
});

function appendMessage(sender, text) {
  const hist = $('chat-history');
  const msg = document.createElement('div');
  msg.className = `msg ${sender}`;
  msg.textContent = text;
  hist.appendChild(msg);
  hist.scrollTop = hist.scrollHeight;
}

// ─── Export Logic ───
$('btn-export').addEventListener('click', () => {
  if (!state.currentMeeting) return;
  const data = safeJsonParse(state.currentMeeting.processed_data, {});
  const actionItems = Array.isArray(data.action_items) ? data.action_items : [];
  let csv = 'Task,Owner,Deadline,Priority\n';
  actionItems.forEach(a => {
    const task = a.task || a.what || '';
    const owner = a.owner || a.who || '';
    const deadline = a.deadline || a.when || '';
    const priority = a.priority || 'MEDIUM';
    csv += `"${task}","${owner}","${deadline}","${priority}"\n`;
  });
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `meeting_actions_${state.currentMeeting.id}.csv`;
  a.click();
});

// ─── Knowledge Graph & Entity Explorer ───
let kgState = {
  entities: [],
  activeTypeFilter: '',
  searchQuery: '',
  selectedEntityId: null,
  initialized: false
};

function initKnowledgeGraphEvents() {
  if (kgState.initialized) return;

  const filterContainer = $('kg-type-filters');
  if (filterContainer) {
    filterContainer.addEventListener('click', (e) => {
      const pill = e.target.closest('.kg-pill');
      if (!pill) return;

      filterContainer.querySelectorAll('.kg-pill').forEach(b => b.classList.remove('active'));
      pill.classList.add('active');

      kgState.activeTypeFilter = pill.dataset.type || '';
      loadKnowledgeEntities(kgState.activeTypeFilter, kgState.searchQuery);
    });
  }

  const searchInput = $('kg-search-input');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      kgState.searchQuery = e.target.value.trim();
      renderKnowledgeEntityList();
    });
  }

  kgState.initialized = true;
}

function getEntityBadgeClass(type) {
  const normalized = (type || '').toLowerCase();
  if (['person', 'decision', 'task', 'blocker', 'meeting'].includes(normalized)) {
    return `entity-badge ${normalized}`;
  }
  return 'entity-badge';
}

async function loadKnowledgeEntities(typeFilter = kgState.activeTypeFilter, searchQuery = kgState.searchQuery) {
  kgState.activeTypeFilter = typeFilter;
  kgState.searchQuery = searchQuery;

  const listEl = $('kg-entity-list');
  if (!listEl) return;

  try {
    const queryParam = typeFilter ? `?type=${encodeURIComponent(typeFilter)}` : '';
    const data = await apiGet(`/knowledge/entities${queryParam}`);
    kgState.entities = Array.isArray(data) ? data : [];
    renderKnowledgeEntityList();

    if (kgState.entities.length > 0 && !kgState.selectedEntityId) {
      inspectEntity(kgState.entities[0].id);
    }
  } catch (err) {
    listEl.innerHTML = `<div class="empty-state">Failed to load knowledge entities.</div>`;
    toast('Could not load knowledge graph entities', 'error');
  }
}

function renderKnowledgeEntityList() {
  const listEl = $('kg-entity-list');
  const countEl = $('kg-entity-count');
  if (!listEl) return;

  const q = (kgState.searchQuery || '').toLowerCase();
  const filtered = kgState.entities.filter(ent => {
    if (!q) return true;
    const nameMatch = (ent.name || '').toLowerCase().includes(q);
    const descMatch = (ent.description || '').toLowerCase().includes(q);
    return nameMatch || descMatch;
  });

  if (countEl) countEl.textContent = String(filtered.length);

  if (filtered.length === 0) {
    listEl.innerHTML = `<div class="empty-state">No matching entities found.</div>`;
    return;
  }

  listEl.innerHTML = filtered.map(ent => {
    const isSelected = ent.id === kgState.selectedEntityId ? 'selected' : '';
    const badgeClass = getEntityBadgeClass(ent.type);
    const connCount = ent.connection_count ?? 0;
    const descHtml = ent.description
      ? `<div class="kg-card-desc">${escapeHtml(ent.description)}</div>`
      : '';

    return `
      <button type="button" class="kg-entity-card ${isSelected}" data-entity-id="${ent.id}" onclick="inspectEntity(${ent.id})">
        <div class="kg-card-main">
          <div class="kg-card-title-row">
            <span class="${badgeClass}">${escapeHtml(ent.type)}</span>
            <span class="kg-card-name">${escapeHtml(ent.name)}</span>
          </div>
          ${descHtml}
        </div>
        <span class="kg-conn-pill">${connCount} ${connCount === 1 ? 'edge' : 'edges'}</span>
      </button>
    `;
  }).join('');
}

async function inspectEntity(entityId) {
  if (!entityId) return;
  kgState.selectedEntityId = Number(entityId);

  document.querySelectorAll('.kg-entity-card').forEach(card => {
    card.classList.toggle('selected', Number(card.dataset.entityId) === kgState.selectedEntityId);
  });

  const inspectorEl = $('kg-inspector');
  if (!inspectorEl) return;

  inspectorEl.innerHTML = `<div class="loading-mini">Loading entity connections...</div>`;

  try {
    const payload = await apiGet(`/knowledge/entity/${encodeURIComponent(entityId)}`);
    if (!payload || !payload.entity) {
      inspectorEl.innerHTML = `<div class="empty-state">Entity not found.</div>`;
      return;
    }

    const { entity, connections = [] } = payload;
    const badgeClass = getEntityBadgeClass(entity.type);

    const metadataEntries = entity.metadata && typeof entity.metadata === 'object'
      ? Object.entries(entity.metadata).filter(([_, v]) => v !== null && v !== undefined && v !== '')
      : [];

    const metadataHtml = metadataEntries.length > 0
      ? `<div class="kg-meta-grid">
          ${metadataEntries.map(([k, v]) => `
            <span class="kg-meta-tag">
              <span class="kg-meta-key">${escapeHtml(k)}:</span>${escapeHtml(String(v))}
            </span>
          `).join('')}
        </div>`
      : '';

    const descriptionHtml = entity.description
      ? `<p class="kg-inspector-desc">${escapeHtml(entity.description)}</p>`
      : '';

    const edgesHtml = connections.length === 0
      ? `<div class="empty-state">No recorded relationships for this entity.</div>`
      : `<div class="kg-edges-list">
          ${connections.map(conn => {
            const isOutgoing = conn.direction === 'OUTGOING';
            const arrow = isOutgoing ? '&rarr;' : '&larr;';
            const dirLabel = isOutgoing ? 'Outgoing to' : 'Incoming from';
            const target = conn.connected_entity || {};
            const targetBadge = getEntityBadgeClass(target.type);
            const evidenceText = conn.evidence
              ? `<blockquote class="kg-evidence-quote">“${escapeHtml(conn.evidence)}”</blockquote>`
              : `<blockquote class="kg-evidence-quote" style="opacity:0.6;">No verbatim quote recorded.</blockquote>`;
            const meetingFooter = conn.source_meeting_id
              ? `<div class="kg-evidence-meta">Source Meeting #${escapeHtml(String(conn.source_meeting_id))}</div>`
              : '';

            return `
              <div class="kg-edge-card">
                <div class="kg-edge-top">
                  <span class="kg-rel-pill">${escapeHtml(conn.relation_type)}</span>
                  <span class="kg-dir-arrow" title="${dirLabel}">${arrow}</span>
                  <button 
                    type="button" 
                    class="kg-target-link" 
                    onclick="inspectEntity(${Number(target.id)})"
                    title="Inspect ${escapeHtml(target.name || '')}"
                  >
                    <span class="${targetBadge}">${escapeHtml(target.type || '')}</span>
                    <span>${escapeHtml(target.name || 'Unknown')}</span>
                  </button>
                </div>
                ${evidenceText}
                ${meetingFooter}
              </div>
            `;
          }).join('')}
        </div>`;

    inspectorEl.innerHTML = `
      <div class="kg-inspector-header">
        <span class="${badgeClass}">${escapeHtml(entity.type)}</span>
        <h3 class="kg-inspector-title">${escapeHtml(entity.name)}</h3>
        ${descriptionHtml}
        ${metadataHtml}
      </div>
      <div>
        <div class="kg-panel-header" style="margin: 0 -20px 12px -20px; padding: 10px 20px;">
          <span>Connected Knowledge & Evidence</span>
          <span class="kg-count-badge">${connections.length}</span>
        </div>
        ${edgesHtml}
      </div>
    `;
  } catch (err) {
    inspectorEl.innerHTML = `<div class="empty-state">Error loading entity details.</div>`;
    toast('Failed to load entity details', 'error');
  }
}

// ─── Init ───
loadMeetings();
switchView('dashboard');
