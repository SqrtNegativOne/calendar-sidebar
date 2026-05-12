<!--
  DayColumnWidget — floating day-column calendar sidebar.

  A narrow slide-out panel anchored to the right edge of the viewport.
  Shows a single-day time grid with two visually distinct item types:
  calendar events (solid blue) and tasks (dashed green). Supports
  drag-move, drag-resize, and click-to-create via @event-calendar/core.

  Props (data in):
    events        ECEvent[]       items to display (use extendedProps.kind
                                  to distinguish 'task' vs 'event')
    open          boolean         whether the panel is expanded

  Callbacks (events out):
    onToggle()                    user clicked the tab
    onEventClick(info)            user clicked an event
    onEventDrop(info)             user dragged an event to a new time
    onEventResize(info)           user resized an event's duration
    onDateClick(info)             user clicked an empty time slot
    onEventRemove(id)             user requested removal of an event

  This component is pure — it does not fetch data or call any backend.
-->
<script lang="ts">
  import { Calendar, TimeGrid, Interaction } from '@event-calendar/core';
  import '@event-calendar/core/index.css';

  export interface ECEvent {
    id: string;
    title: string;
    start: string;
    end: string;
    color?: string;
    extendedProps?: {
      kind: 'task' | 'event';
      [key: string]: unknown;
    };
  }

  type Props = {
    events: ECEvent[];
    open: boolean;
    onToggle: () => void;
    onEventClick: (info: any) => void;
    onEventDrop: (info: any) => void;
    onEventResize: (info: any) => void;
    onDateClick: (info: any) => void;
    onEventRemove: (id: string) => void;
  };

  let {
    events,
    open,
    onToggle,
    onEventClick,
    onEventDrop,
    onEventResize,
    onDateClick,
    onEventRemove
  }: Props = $props();

  const now = new Date();
  const dayNames = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];

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

  const headerStr = `${ordinal(now.getDate())} ${monthNames[now.getMonth()]} ${now.getFullYear()} ⋅ ${dayNames[now.getDay()].slice(0, 3)} ⋅ Week #${isoWeek(now)}`;

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

  // Stable callback refs — created once so the library's diff() never
  // sees them as changed. They close over $props via the `on*` bindings
  // which Svelte keeps current.
  function handleEventClassNames(info: any) {
    const kind = info.event.extendedProps?.kind ?? 'event';
    const dur = getDurationMinutes(info.event);
    const classes = [`ec-kind-${kind}`];
    if (dur <= 30) classes.push('ec-short');
    return classes;
  }

  function handleEventContent(info: any) {
    const kind = info.event.extendedProps?.kind ?? 'event';
    const start = toDate(info.event.start);
    const end = toDate(info.event.end);
    const timeStr = `${formatTime(start)} – ${formatTime(end)}`;
    const prefix = kind === 'task' ? '◻ ' : '';
    const removeBtn = `<button class="ec-remove-btn" data-event-id="${info.event.id}" title="Remove">×</button>`;
    return {html: `<div class="ec-custom-event"><div class="ec-custom-title">${prefix}${info.event.title} ${removeBtn}</div><div class="ec-custom-time">${timeStr}</div></div>`};
  }

  function handleEventClick(info: any) {
    const target = info.jsEvent?.target as HTMLElement | undefined;
    if (target?.closest('.ec-remove-btn')) {
      const id = target.closest('.ec-remove-btn')!.getAttribute('data-event-id');
      if (id) onEventRemove(id);
      return;
    }
    onEventClick(info);
  }

  function handleEventDrop(info: any) { onEventDrop(info); }
  function handleEventResize(info: any) { onEventResize(info); }
  function handleDateClick(info: any) { onDateClick(info); }
  function handleSlotLabel(date: Date) {
    return String(date.getHours());
  }

  const calPlugins = [TimeGrid, Interaction];
  const scrollH = Math.max(6, now.getHours() - 2);

  // Options created once as $state — only `events` is patched reactively.
  // svelte-ignore state_referenced_locally
  let options = $state({
    view: 'timeGridDay',
    height: '100%',
    events: events,
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
    headerToolbar: {start: '', center: '', end: ''},
    dayHeaderFormat: () => '',
    slotLabelFormat: handleSlotLabel,
    eventClassNames: handleEventClassNames,
    eventContent: handleEventContent,
    eventClick: handleEventClick,
    eventDrop: handleEventDrop,
    eventResize: handleEventResize,
    dateClick: handleDateClick,
    theme: (theme: any) => ({
      ...theme,
      calendar: `${theme.calendar} ec-dark`
    }),
  });

  $effect(() => {
    options.events = events;
  });
</script>

<div class="widget" class:open>
  <button class="tab" onclick={onToggle} aria-label="Toggle day column">
    <span class="arrow">{open ? '›' : '‹'}</span>
  </button>
  <div class="panel">
    <div class="panel-header">
      <div class="header-line">{headerStr}</div>
    </div>
    <div class="calendar-wrap">
      <Calendar plugins={calPlugins} {options} />
    </div>
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
    width: 28px;
    height: 80px;
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
  }
  .panel-header .header-line {
    font-size: 0.72rem;
    color: var(--ink-soft);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    letter-spacing: 0.03em;
  }

  .calendar-wrap {
    flex: 1;
    overflow: hidden;
    min-height: 0;
  }

  /* ─── Event Calendar dark-theme overrides ─── */
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

  .calendar-wrap :global(.ec-toolbar) {
    display: none !important;
  }
  .calendar-wrap :global(.ec-day-head),
  .calendar-wrap :global(.ec-col-head) {
    display: none !important;
  }

  .calendar-wrap :global(.ec-sidebar) {
    width: 32px !important;
    font-size: 0.65rem;
    color: var(--ink-soft);
  }

  /* ─── Calendar events (kind=event): solid blue ─── */
  .calendar-wrap :global(.ec-kind-event) {
    background: #1e3a5f !important;
    border-left: 3px solid #5090d0 !important;
    color: #a8cef0 !important;
    border-radius: 3px;
  }

  /* ─── Tasks (kind=task): dashed green border ─── */
  .calendar-wrap :global(.ec-kind-task) {
    background: rgba(26, 58, 42, 0.8) !important;
    border-left: 3px dashed #50a070 !important;
    color: #a0d8b0 !important;
    border-radius: 3px;
  }

  .calendar-wrap :global(.ec-short .ec-event-body) {
    overflow: hidden;
  }

  /* ─── Custom event content ─── */
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

  /* ─── Remove button ─── */
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
</style>
