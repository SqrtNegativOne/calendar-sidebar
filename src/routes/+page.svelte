<script lang="ts">
  import { onMount } from 'svelte';
  import DayColumnWidget, { type ECEvent } from '$lib/components/DayColumnWidget.svelte';

  let open = $state(true);
  let authenticated = $state(false);
  let loading = $state(true);
  let error = $state(false);
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
      events = await resp.json() as ECEvent[];
    } catch (err) {
      addLog(`Sync error: ${err}`);
    }
  }

  onMount(() => {
    let interval: ReturnType<typeof setInterval>;

    (async () => {
      try {
        const resp = await fetch('/api/auth/status');
        const status = await resp.json() as { authenticated: boolean };
        authenticated = status.authenticated;
      } catch {
        error = true;
      } finally {
        loading = false;
      }

      if (!authenticated || error) return;

      await fetchEvents();
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
    events = events.map(e => e.id === ev.id ? { ...e, start, end } : e);
    addLog(`Moved "${ev.title}" to ${new Date(ev.start).toLocaleTimeString()}`);
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
    events = events.map(e => e.id === ev.id ? { ...e, start, end } : e);
    addLog(`Resized "${ev.title}" ends ${new Date(ev.end).toLocaleTimeString()}`);
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

<!-- .demo is hidden in widget mode via _INJECT_JS in widget.py -->
<div class="demo">
  <h1>Calendar Sidebar</h1>
  {#if loading}
    <p>Connecting...</p>
  {:else if error}
    <p>Could not reach the backend. Is the server running?</p>
  {:else if !authenticated}
    <p>Connect your Google Calendar to get started.</p>
    <a class="connect-btn" href="/api/auth/google">Connect Google Calendar</a>
  {:else}
    <p class="status">Synced &mdash; polls every 30 s</p>
    {#if log.length > 0}
      <div class="log">
        {#each log as entry}
          <div class="log-entry">{entry}</div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<DayColumnWidget
  {events}
  {open}
  {authenticated}
  {loading}
  {error}
  onToggle={handleToggle}
  onEventClick={handleEventClick}
  onEventDrop={handleEventDrop}
  onEventResize={handleEventResize}
  onDateClick={handleDateClick}
  onEventRemove={handleEventRemove}
/>

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
    font-size: 0.9rem;
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
  .connect-btn:hover { opacity: 0.85; }

  .log {
    border-top: 1px solid var(--line);
    padding-top: 0.75rem;
    margin-top: 1rem;
  }
  .log-entry {
    font-size: 0.8rem;
    color: var(--ink-soft);
    padding: 0.1rem 0;
    font-family: 'Consolas', monospace;
  }
</style>
