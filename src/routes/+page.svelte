<script lang="ts">
  import { onMount } from 'svelte';
  import DayColumnWidget, { type ECEvent } from '$lib/components/DayColumnWidget.svelte';

  let open = $state(true);
  let authenticated = $state(false);
  let loading = $state(true);
  let syncing = $state(false);
  let events: ECEvent[] = $state([]);
  let log: string[] = $state([]);

  function addLog(msg: string) {
    log = [msg, ...log].slice(0, 20);
  }

  async function fetchEvents() {
    try {
      const resp = await fetch('/api/events');
      if (!resp.ok) {
        if (resp.status === 401) { authenticated = false; return; }
        throw new Error(`${resp.status}`);
      }
      const data = await resp.json() as ECEvent[];
      events = data;
    } catch (err) {
      addLog(`Sync error: ${err}`);
    }
  }

  onMount(() => {
    let interval: ReturnType<typeof setInterval>;

    (async () => {
      const resp = await fetch('/api/auth/status');
      const status = await resp.json() as { authenticated: boolean };
      authenticated = status.authenticated;
      loading = false;

      if (!authenticated) return;

      syncing = true;
      await fetchEvents();
      syncing = false;

      interval = setInterval(fetchEvents, 30_000);
    })();

    return () => clearInterval(interval);
  });

  function handleToggle() { open = !open; }
  function handleEventClick(_info: any) {}

  async function handleEventDrop(info: any) {
    const ev = info.event;
    const start = new Date(ev.start).toISOString();
    const end = new Date(ev.end).toISOString();
    addLog(`Moved "${ev.title}" to ${new Date(ev.start).toLocaleTimeString()}`);
    events = events.map(e => e.id === ev.id ? { ...e, start, end } : e);
    try {
      await fetch(`/api/events/${ev.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start, end }),
      });
    } catch {
      await fetchEvents();
    }
  }

  async function handleEventResize(info: any) {
    const ev = info.event;
    const start = new Date(ev.start).toISOString();
    const end = new Date(ev.end).toISOString();
    addLog(`Resized "${ev.title}" ends ${new Date(ev.end).toLocaleTimeString()}`);
    events = events.map(e => e.id === ev.id ? { ...e, start, end } : e);
    try {
      await fetch(`/api/events/${ev.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start, end }),
      });
    } catch {
      await fetchEvents();
    }
  }

  async function handleDateClick(info: any) {
    const start = new Date(info.dateStr ?? info.date?.toISOString());
    const end = new Date(start.getTime() + 30 * 60_000);
    try {
      const resp = await fetch('/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'New task', start: start.toISOString(), end: end.toISOString() }),
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      const newEv = await resp.json() as ECEvent;
      events = [...events, newEv];
      addLog(`Created task at ${start.toLocaleTimeString()}`);
    } catch (err) {
      addLog(`Create failed: ${err}`);
    }
  }

  async function handleEventRemove(id: string) {
    const ev = events.find(e => e.id === id);
    events = events.filter(e => e.id !== id);
    addLog(`Removed "${ev?.title ?? id}"`);
    try {
      const resp = await fetch(`/api/events/${id}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`${resp.status}`);
    } catch {
      await fetchEvents();
    }
  }
</script>

<div class="demo">
  <h1>Day Column Widget</h1>

  {#if loading}
    <p class="status">Checking auth...</p>
  {:else if !authenticated}
    <p>Connect your Google Calendar to get started.</p>
    <a class="connect-btn" href="/api/auth/google">Connect Google Calendar</a>
  {:else}
    <p class="status">{syncing ? 'Syncing...' : 'Synced with Google Calendar'} &mdash; polls every 30 s</p>
    <ul>
      <li><strong>Drag</strong> an event to move it</li>
      <li><strong>Drag the bottom edge</strong> to resize</li>
      <li><strong>Click empty space</strong> to create a 30-min task</li>
      <li><strong>Hover + ×</strong> to remove</li>
    </ul>
    {#if log.length > 0}
      <div class="log">
        {#each log as entry}
          <div class="log-entry">{entry}</div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

{#if authenticated}
  <DayColumnWidget
    {events}
    {open}
    onToggle={handleToggle}
    onEventClick={handleEventClick}
    onEventDrop={handleEventDrop}
    onEventResize={handleEventResize}
    onDateClick={handleDateClick}
    onEventRemove={handleEventRemove}
  />
{/if}

<style>
  .demo {
    padding: 3rem;
    max-width: 38rem;
  }

  h1 {
    font-size: 1.4rem;
    font-weight: 600;
    margin: 0 0 0.4rem;
  }

  p {
    color: var(--ink-soft);
    margin: 0 0 1.5rem;
  }

  .status {
    font-size: 0.85rem;
  }

  .connect-btn {
    display: inline-block;
    padding: 0.5rem 1.2rem;
    background: var(--accent);
    color: var(--ink);
    border-radius: 6px;
    text-decoration: none;
    font-size: 0.9rem;
    font-weight: 500;
    transition: opacity 150ms;
  }
  .connect-btn:hover {
    opacity: 0.85;
  }

  ul {
    list-style: none;
    padding: 0;
    margin: 0 0 2rem;
  }
  ul li {
    padding: 0.3rem 0;
    font-size: 0.9rem;
    border-bottom: 1px solid var(--line);
  }

  .log {
    border-top: 1px solid var(--line);
    padding-top: 0.75rem;
  }
  .log-entry {
    font-size: 0.8rem;
    color: var(--ink-soft);
    padding: 0.1rem 0;
    font-family: 'Consolas', monospace;
  }
</style>
