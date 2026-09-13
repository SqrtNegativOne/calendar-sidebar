<!--
  SidebarWidget — floating day-column calendar and notes sidebar.

  Props (data in):
    events        ECEvent[]       items to display
    open          boolean         whether the panel is expanded
    syncError     boolean         last sync attempt failed

  Callbacks (events out):
    onToggle()
    onEventClick(info)
    onEventDrop(info)
    onEventResize(info)
    onDateClick(info)
    onSelect(info)
    onEventRename(id, title)
    onEventRemove(id)
-->
<script lang="ts">
  import { Calendar, TimeGrid, Interaction } from '@event-calendar/core';
  import '@event-calendar/core/index.css';
  import { onMount } from 'svelte';

  export interface ECEvent {
    id: string;
    title: string;
    start: string;
    end: string;
    allDay?: boolean;
    color?: string;
  }

  type Props = {
    events: ECEvent[];
    open: boolean;
    authenticated?: boolean;
    loading?: boolean;
    error?: boolean;
    syncError?: boolean;
    fetching?: boolean;
    lastError?: string | null;
    lastSync?: Date | null;
    onToggle: () => void;
    onRefresh: () => void;
    onEventClick: (info: any) => void;
    onEventDrop: (info: any) => void;
    onEventResize: (info: any) => void;
    onDateClick: (info: any) => void;
    onSelect: (info: any) => void;
    onEventRename: (id: string, title: string) => void;
    onEventRemove: (id: string) => void;
  };

  let {
    events,
    open,
    authenticated = true,
    loading = false,
    error = false,
    syncError = false,
    fetching = false,
    lastError = null,
    lastSync = null,
    onToggle,
    onRefresh,
    onEventClick,
    onEventDrop,
    onEventResize,
    onDateClick,
    onSelect,
    onEventRename,
    onEventRemove
  }: Props = $props();

  let now = $state(new Date());
  const dayNames = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];

  // State for Notes
  let showNotes = $state(false);
  let notesText = $state('');

  onMount(() => {
    notesText = localStorage.getItem('notes_text') || '';
    showNotes = localStorage.getItem('show_notes') === 'true';

    const onVisChange = () => {
      if (!document.hidden) now = new Date();
    };
    document.addEventListener('visibilitychange', onVisChange);

    // The backend always fetches "today", so the view must roll over with the
    // wall clock too — otherwise after midnight the calendar keeps rendering
    // yesterday while the API returns today's events, and nothing lines up.
    const dayTick = setInterval(() => {
      const d = new Date();
      if (d.toDateString() !== now.toDateString()) now = d;
    }, 30_000);

    return () => {
      document.removeEventListener('visibilitychange', onVisChange);
      clearInterval(dayTick);
    };
  });

  $effect(() => {
    if (open) now = new Date();
  });

  $effect(() => {
    localStorage.setItem('notes_text', notesText);
  });

  $effect(() => {
    localStorage.setItem('show_notes', String(showNotes));
  });

  function toggleNotes() {
    showNotes = !showNotes;
  }

  function ordinal(n: number): string {
    const v = n % 100;
    const s = ['th','st','nd','rd'];
    return n + (s[(v - 20) % 10] ?? s[v] ?? s[0]);
  }

  function isoWeek(d: Date): number {
    const utc = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    utc.setUTCDate(utc.getUTCDate() + 4 - (utc.getUTCDay() || 7));
    const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
    return Math.ceil(((utc.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7);
  }

  let headerStr = $derived(`${ordinal(now.getDate())} ${monthNames[now.getMonth()]} ${now.getFullYear()} ⋅ ${dayNames[now.getDay()].slice(0, 3)} ⋅ Week #${isoWeek(now)}`);

  function getDurationMinutes(event: any): number {
    const s = event.start instanceof Date ? event.start : new Date(event.start);
    const e = event.end instanceof Date ? event.end : new Date(event.end);
    return (e.getTime() - s.getTime()) / 60000;
  }

  function formatTime(date: Date): string {
    const h = date.getHours();
    const m = date.getMinutes();
    const suffix = h < 12 ? 'AM' : 'PM';
    const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${String(m).padStart(2, '0')} ${suffix}`;
  }

  function toDate(v: string | Date): Date {
    return v instanceof Date ? v : new Date(v);
  }

  function handleEventClassNames(info: any) {
    const dur = getDurationMinutes(info.event);
    return dur <= 30 ? ['ec-short'] : [];
  }

  // event-calendar injects this html via innerHTML, so every interpolated
  // value must be escaped — event titles and ids come from Google and are
  // not trusted.
  function escapeHtml(value: unknown): string {
    return String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string
    );
  }

  function handleEventContent(info: any) {
    // `calEvents` strips the allDay flag so event-calendar renders all-day
    // items as timed blocks (the all-day slot is disabled), so recover the
    // label from the source events instead.
    const isAllDay = allDayIds.has(String(info.event.id));
    const timeStr = isAllDay
      ? 'All day'
      : `${formatTime(toDate(info.event.start))} – ${formatTime(toDate(info.event.end))}`;
    const id = escapeHtml(info.event.id);
    const removeBtn = `<button class="ec-remove-btn" data-event-id="${id}" title="Remove">×</button>`;
    const title = `<span class="ec-title-text">${escapeHtml(info.event.title)}</span>`;
    return { html: `<div class="ec-custom-event"><div class="ec-custom-title" data-event-id="${id}">${title} ${removeBtn}</div><div class="ec-custom-time">${escapeHtml(timeStr)}</div></div>` };
  }

  function handleEventClick(info: any) {
    const target = info.jsEvent?.target as HTMLElement | undefined;
    if (target?.closest('.ec-remove-btn')) {
      const id = (target.closest('.ec-remove-btn') as HTMLElement).getAttribute('data-event-id');
      if (id) onEventRemove(id);
      return;
    }
    onEventClick(info);
  }

  function handleEventDrop(info: any) { onEventDrop(info); }
  function handleEventResize(info: any) { onEventResize(info); }
  function handleDateClick(info: any) { onDateClick(info); }
  function handleSelect(info: any) { onSelect(info); }

  function handleSlotLabel(date: Date) {
    return String(date.getHours());
  }

  const calPlugins = [TimeGrid, Interaction];
  const scrollH = Math.max(6, new Date().getHours() - 2);

  // Identifiers of all-day source events — used to keep the "All day" label
  // after the flag is stripped for rendering.
  let allDayIds = $derived(new Set(events.filter((e) => e.allDay).map((e) => e.id)));

  // The all-day slot is disabled in this layout, so all-day events must be
  // fed to the calendar without the allDay flag — they render as timed
  // events instead. `events` keeps the flag for the "All day" label in
  // handleEventContent.
  //
  // Google's all-day `end.date` is exclusive (the next midnight), and with
  // slotEventOverlap off a 24 h block would fill the column and steal half
  // the width from every timed event — so clamp the display end to the end
  // of the event's own day (falling back to today). Basing it on the event
  // start rather than `now` avoids an end-before-start block that vanishes
  // when the two race across a day boundary.
  //
  // Both bounds become local Date objects: the backend sends all-day dates
  // as naive `YYYY-MM-DDT00:00:00`, which event-calendar would otherwise
  // reparse as UTC and shift a day for timezones west of UTC.
  let calEvents = $derived(
    events.map(({ allDay, ...rest }) => {
      if (!allDay) return rest;
      const startDay = new Date(rest.start);
      startDay.setHours(0, 0, 0, 0);
      const today = new Date(now);
      today.setHours(0, 0, 0, 0);
      const displayDay = startDay > today ? startDay : today;
      const dayEnd = new Date(displayDay);
      dayEnd.setHours(23, 59, 0, 0);
      return { ...rest, start: startDay, end: dayEnd };
    })
  );

  // svelte-ignore state_referenced_locally
  let options = $state({
    view: 'timeGridDay',
    date: now,
    height: '100%',
    events: calEvents,
    editable: true,
    selectable: true,
    nowIndicator: true,
    allDaySlot: false,
    slotEventOverlap: false,
    slotHeight: 24,
    slotDuration: '00:15:00',
    slotLabelInterval: '01:00:00',
    slotMinTime: '06:00:00',
    slotMaxTime: '22:00:00',
    scrollTime: `${String(scrollH).padStart(2, '0')}:00:00`,
    headerToolbar: { start: '', center: '', end: '' },
    dayHeaderFormat: () => '',
    slotLabelFormat: handleSlotLabel,
    eventClassNames: handleEventClassNames,
    eventContent: handleEventContent,
    eventClick: handleEventClick,
    eventDrop: handleEventDrop,
    eventResize: handleEventResize,
    dateClick: handleDateClick,
    select: handleSelect,
    theme: (theme: any) => ({ ...theme, calendar: `${theme.calendar} ec-dark` }),
  });

  $effect(() => {
    options.events = calEvents;
    options.date = now;
  });

  // Inline title editing — double-click a task name to edit it in place
  let panelEl: HTMLElement | undefined = $state();
  let inputEl: HTMLInputElement | undefined = $state();
  let editor = $state<{ id: string; title: string; x: number; y: number; w: number } | null>(null);
  let editorCancelled = false;

  $effect(() => {
    if (editor && inputEl) {
      inputEl.focus();
      inputEl.select();
    }
  });

  function handlePanelDblClick(e: MouseEvent) {
    const target = e.target as HTMLElement;
    const titleEl = target.closest('.ec-custom-title') as HTMLElement | null;
    if (!titleEl || !panelEl) return;
    const id = titleEl.getAttribute('data-event-id');
    const ev = events.find((e2) => e2.id === id);
    if (!id || !ev) return;

    const panelRect = panelEl.getBoundingClientRect();
    const rect = titleEl.getBoundingClientRect();
    // Both rects are viewport-relative, so the difference positions the
    // input exactly over the title even when the grid is scrolled
    editorCancelled = false;
    editor = {
      id,
      title: ev.title,
      x: rect.left - panelRect.left,
      y: rect.top - panelRect.top,
      w: Math.max(80, rect.width)
    };
  }

  function commitEditor() {
    if (!editor) return;
    if (!editorCancelled) onEventRename(editor.id, editor.title);
    editor = null;
  }

  function handleEditorKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      (e.target as HTMLInputElement).blur();
    } else if (e.key === 'Escape') {
      editorCancelled = true;
      (e.target as HTMLInputElement).blur();
    }
  }
</script>

<div class="widget" class:open>
  <button class="tab" onclick={onToggle} aria-label="Toggle day column">
    <span class="arrow">{open ? '›' : '‹'}</span>
  </button>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="panel" bind:this={panelEl} ondblclick={handlePanelDblClick}>
    <div class="panel-header">
      <div class="header-line">{headerStr}</div>
      {#if fetching}
        <span
          class="spinner"
          role="status"
          aria-label="Syncing events"
          title={lastSync ? `Syncing… last synced ${lastSync.toLocaleTimeString()}` : 'Syncing events…'}
        ></span>
      {/if}
    </div>
    {#if loading}
      <div class="panel-status">Connecting...</div>
    {:else if error}
      <div class="panel-status">Backend unreachable</div>
    {:else if !authenticated}
      <div class="panel-status">
        <p>Google Calendar not connected.</p>
        <a class="connect-link" href="/api/auth/google">Connect</a>
      </div>
    {:else}
      {#if syncError}
        <div class="sync-error" role="alert">
          <span class="sync-error-msg">{lastError ?? 'Sync failed'}</span>
          <button class="retry-btn" onclick={onRefresh}>Retry</button>
        </div>
      {/if}
      <div class="calendar-wrap" class:half={showNotes}>
        <Calendar plugins={calPlugins} {options} />
        {#if events.length === 0 && !fetching}
          <div class="empty-state">No events today</div>
        {/if}
      </div>
      {#if showNotes}
        <div class="notes-wrap">
          <textarea
            bind:value={notesText}
            placeholder="Type notes here..."
            class="notes-textarea"
          ></textarea>
        </div>
      {/if}
      {#if editor}
        <input
          class="title-editor"
          style="left:{editor.x}px; top:{editor.y}px; width:{editor.w}px"
          bind:value={editor.title}
          bind:this={inputEl}
          onkeydown={handleEditorKey}
          onblur={commitEditor}
          onclick={(e) => e.stopPropagation()}
          ondblclick={(e) => e.stopPropagation()}
        />
      {/if}
    {/if}
    <button class="notes-toggle-btn" onclick={toggleNotes} aria-label="Toggle notes" title="Toggle notes">
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-square-pen"><path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.875.958.958-2.875a2 2 0 0 1 .506-.854z"/></svg>
    </button>
  </div>
</div>

<style>
  .widget {
    position: fixed;
    top: 0;
    right: 0;
    height: 100vh;
    display: flex;
    z-index: 1000;
    pointer-events: none;
    overflow: hidden;
  }

  .tab {
    align-self: center;
    width: 22px;
    height: 64px;
    background: var(--bg-surface);
    border: 1px solid var(--line);
    border-right: none;
    border-radius: 6px 0 0 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    pointer-events: auto;
    color: var(--ink-soft);
    font-size: 14px;
    transition: color 150ms, background 150ms;
    flex-shrink: 0;
  }
  .tab:hover {
    color: var(--ink);
    background: var(--bg);
  }
  .tab .arrow {
    transition: transform 200ms ease;
  }

  .panel {
    width: 0;
    min-width: 0;
    overflow: hidden;
    background: rgba(12, 12, 10, 0.88);
    border-left: none;
    transition: width 250ms cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
    display: flex;
    flex-direction: column;
    position: relative;
  }
  .open .panel {
    width: 280px;
    border-left: 1px solid var(--line);
    pointer-events: auto;
  }

  .panel-header {
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .panel-header .header-line {
    font-size: 0.72rem;
    color: var(--ink-soft);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    letter-spacing: 0.03em;
  }

  .spinner {
    flex-shrink: 0;
    width: 11px;
    height: 11px;
    border: 2px solid var(--line);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 700ms linear infinite;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner { animation-duration: 2s; }
  }

  .panel-status {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    padding: 1.5rem;
    text-align: center;
    font-size: 0.8rem;
    color: var(--ink-soft);
  }
  .panel-status p {
    margin: 0;
  }
  .connect-link {
    display: inline-block;
    padding: 0.4rem 1rem;
    background: var(--accent);
    color: var(--ink);
    border-radius: 5px;
    text-decoration: none;
    font-size: 0.8rem;
    font-weight: 500;
    transition: opacity 150ms;
  }
  .connect-link:hover {
    opacity: 0.8;
  }

  .sync-error {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 12px;
    font-size: 0.7rem;
    color: #e8a09a;
    background: rgba(204, 95, 84, 0.15);
    border-bottom: 1px solid var(--line);
    flex-shrink: 0;
  }
  .sync-error-msg {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .retry-btn {
    flex-shrink: 0;
    padding: 1px 8px;
    font-size: 0.68rem;
    color: var(--ink);
    background: rgba(204, 95, 84, 0.35);
    border: 1px solid rgba(232, 160, 154, 0.5);
    border-radius: 4px;
    cursor: pointer;
  }
  .retry-btn:hover {
    background: rgba(204, 95, 84, 0.55);
  }

  .title-editor {
    position: absolute;
    z-index: 200;
    padding: 0 2px;
    background: var(--bg-surface);
    color: var(--ink);
    border: 1px solid var(--accent);
    border-radius: 3px;
    font-size: 0.75rem;
    font-weight: 500;
    font-family: inherit;
    line-height: 1.3;
    outline: none;
  }

  .calendar-wrap {
    flex: 1;
    overflow: hidden;
    min-height: 0;
    position: relative;
    transition: height 250ms cubic-bezier(0.4, 0, 0.2, 1);
  }
  .empty-state {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    color: var(--ink-soft);
    pointer-events: none;
  }
  .calendar-wrap.half {
    flex: none;
    height: 50%;
  }

  .notes-wrap {
    height: 50%;
    border-top: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    background: rgba(18, 18, 16, 0.95);
    min-height: 0;
  }

  .notes-textarea {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--ink);
    padding: 12px;
    padding-bottom: 38px;
    padding-right: 38px;
    resize: none;
    font-size: 0.85rem;
    font-family: inherit;
    line-height: 1.5;
    outline: none;
    scrollbar-width: thin;
    scrollbar-color: var(--line) transparent;
  }

  .notes-toggle-btn {
    position: absolute;
    bottom: 0;
    right: 0;
    width: 32px;
    height: 32px;
    background: var(--bg-surface);
    border-top: 1px solid var(--line);
    border-left: 1px solid var(--line);
    border-bottom: none;
    border-right: none;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: var(--ink-soft);
    z-index: 100;
    transition: color 150ms, background 150ms;
    pointer-events: auto;
  }
  .notes-toggle-btn:hover {
    color: var(--ink);
    background: var(--bg);
  }

  .calendar-wrap :global(.ec) {
    --ec-bg-color: var(--bg);
    --ec-text-color: var(--ink);
    --ec-border-color: var(--line);
    --ec-now-indicator-color: #cc5f54;
    --ec-event-col-gap: 2px;
    --ec-color-400: var(--ink-soft);
    --ec-color-300: var(--line);
    --ec-color-200: var(--bg-surface);
    --ec-color-100: var(--bg);
    --ec-color-50: var(--bg);
    --ec-today-bg-color: transparent;
    --ec-highlight-color: rgba(80, 112, 168, 0.15);
    font-size: 12px;
  }

  .calendar-wrap :global(.ec-toolbar) { display: none !important; }
  .calendar-wrap :global(.ec-day-head),
  .calendar-wrap :global(.ec-col-head) { display: none !important; }

  .calendar-wrap :global(.ec-sidebar) {
    width: 32px !important;
    font-size: 0.65rem;
    color: var(--ink-soft);
  }

  .calendar-wrap :global(.ec-event) {
    background: #1e3a5f !important;
    border-left: 3px solid #5090d0 !important;
    color: #a8cef0 !important;
    border-radius: 3px;
  }

  .calendar-wrap :global(.ec-short .ec-event-body) {
    overflow: hidden;
  }

  .calendar-wrap :global(.ec-custom-event) {
    padding: 1px 4px;
    overflow: hidden;
    height: 100%;
    position: relative;
  }
  .calendar-wrap :global(.ec-custom-title) {
    font-weight: 500;
    font-size: 0.75rem;
    line-height: 1.3;
    overflow-wrap: break-word;
    word-break: break-word;
    overflow: hidden;
    padding-right: 16px;
  }
  .calendar-wrap :global(.ec-custom-time) {
    font-size: 0.6rem;
    opacity: 0.7;
    white-space: nowrap;
  }

  .calendar-wrap :global(.ec-remove-btn) {
    position: absolute;
    top: 1px;
    right: 2px;
    background: none;
    border: none;
    color: inherit;
    opacity: 0;
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    padding: 0 2px;
    transition: opacity 100ms;
  }
  .calendar-wrap :global(.ec-event:hover .ec-remove-btn) {
    opacity: 0.6;
  }
  .calendar-wrap :global(.ec-remove-btn:hover) {
    opacity: 1 !important;
  }

  .calendar-wrap :global(.ec-main) {
    scrollbar-width: none;
    -ms-overflow-style: none;
  }
  .calendar-wrap :global(.ec-main)::-webkit-scrollbar {
    display: none;
  }
</style>
