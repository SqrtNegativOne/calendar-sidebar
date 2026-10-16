<script lang="ts">
  import { onMount } from 'svelte';
  import SidebarWidget, {
    type ECEvent,
    type ProviderStatus
  } from '$lib/components/SidebarWidget.svelte';

  const POLL_MS = 30_000;
  const RETRY_MS = 3_000;
  const MAX_STARTUP_RETRIES = 10;

  let open = $state(true);
  let configured = $state(false);
  let providers = $state<ProviderStatus[]>([]);
  let loading = $state(true);
  let error = $state(false);
  let syncError = $state(false);
  let fetching = $state(false);
  let lastError = $state<string | null>(null);
  let lastSync = $state<Date | null>(null);
  let events: ECEvent[] = $state([]);
  let log: string[] = $state([]);

  function addLog(msg: string) {
    log = [msg, ...log].slice(0, 20);
  }

  // Events are merged from every calendar of every provider, so edits and
  // deletes must be routed back to the provider and calendar the event came
  // from — and, for a recurring event, to the occurrence the user touched.
  function eventQuery(id: string): string {
    const ev = events.find((e) => e.id === id);
    const params = new URLSearchParams();
    if (ev?.provider) params.set('provider', ev.provider);
    if (ev?.calendarId) params.set('calendar_id', ev.calendarId);
    const occurrence = ev?.recurrenceId ?? ev?.start;
    if (occurrence) params.set('original_start', occurrence);
    return `?${params.toString()}`;
  }

  // The API only exposes today, so anything the user sees is in the local day.

  async function fetchEvents() {
    fetching = true;
    try {
      const resp = await fetch('/api/events');
      if (!resp.ok) {
        if (resp.status === 401) {
          configured = false;
          lastError = null;
          return;
        }
        // Prefer the backend's error detail over a bare status code so the
        // failure is legible in the UI instead of silent.
        let detail = `${resp.status} ${resp.statusText}`.trim();
        try {
          const body = await resp.json();
          if (body?.detail) detail = `${resp.status}: ${body.detail}`;
        } catch {
          /* non-JSON error body — keep the status */
        }
        throw new Error(detail);
      }
      const data = await resp.json();
      if (!Array.isArray(data)) throw new Error('Malformed response: expected an event list');
      events = data as ECEvent[];
      syncError = false;
      lastError = null;
      lastSync = new Date();
    } catch (err) {
      syncError = true;
      lastError = err instanceof Error ? err.message : String(err);
      addLog(`Sync failed: ${lastError}`);
    } finally {
      fetching = false;
    }
  }

  let interval: ReturnType<typeof setInterval> | undefined;

  async function checkStatus(): Promise<boolean> {
    try {
      const resp = await fetch('/api/status');
      if (!resp.ok) throw new Error(`${resp.status}`);
      const status = (await resp.json()) as {
        configured: boolean;
        providers?: ProviderStatus[];
      };
      providers = status.providers ?? [];
      configured = status.configured;
      error = false;
      return true;
    } catch {
      return false;
    }
  }

  function startPolling() {
    if (interval) return;
    interval = setInterval(pollTick, POLL_MS);
  }

  function stopPolling() {
    clearInterval(interval);
    interval = undefined;
  }

  // Only hit the API while the calendar panel is actually on screen
  function pollTick() {
    if (!open || document.hidden) return;
    fetchEvents();
  }

  function handleVisibilityChange() {
    if (!document.hidden && open && configured) fetchEvents();
  }

  // Nothing left to poll once the backend reports no configured calendar
  $effect(() => {
    if (!configured) stopPolling();
  });

  onMount(() => {
    document.addEventListener('visibilitychange', handleVisibilityChange);

    (async () => {
      let reachable = false;
      for (let attempt = 0; attempt < MAX_STARTUP_RETRIES && !reachable; attempt++) {
        reachable = await checkStatus();
        if (!reachable) await new Promise((r) => setTimeout(r, RETRY_MS));
      }
      loading = false;

      if (!reachable) {
        error = true;
        return;
      }
      if (!configured) return;

      await fetchEvents();
      startPolling();
    })();

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      stopPolling();
    };
  });

  function handleToggle() {
    open = !open;
    // Catch up on anything missed while the sidebar was closed
    if (open && configured) fetchEvents();
  }
  function handleEventClick(_info: any) {}

  async function createEvent(title: string, start: Date, end: Date) {
    try {
      const resp = await fetch('/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          start: start.toISOString(),
          end: end.toISOString()
        }),
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      const newEv = (await resp.json()) as ECEvent;
      events = [...events, newEv];
      addLog(`Created task "${title}"`);
    } catch (err) {
      addLog(`Create failed: ${err}`);
      await fetchEvents();
    }
  }

  async function handleSelect(info: any) {
    const title = window.prompt('Task name:', 'New task');
    if (!title) {
      if (info.view?.calendar) info.view.calendar.unselect();
      return;
    }
    const startStr = info.startStr ?? info.start?.toISOString();
    const endStr = info.endStr ?? info.end?.toISOString();
    await createEvent(title, new Date(startStr), new Date(endStr));
  }

  async function patchEvent(id: string, body: Record<string, string>) {
    try {
      const resp = await fetch(`/api/events/${id}${eventQuery(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      // Take the server's version: it may have shifted the event into another
      // timezone or attached a recurrence id the optimistic copy lacks.
      const saved = (await resp.json()) as ECEvent;
      events = events.map((e) => (e.id === id ? { ...e, ...saved } : e));
    } catch (err) {
      addLog(`Update failed: ${err}`);
      await fetchEvents();
    }
  }

  async function handleEventDrop(info: any) {
    const ev = info.event;
    const start = new Date(ev.start).toISOString();
    const end = new Date(ev.end).toISOString();
    events = events.map((e) => (e.id === ev.id ? { ...e, start, end } : e));
    addLog(`Moved "${ev.title}" to ${new Date(ev.start).toLocaleTimeString()}`);
    await patchEvent(ev.id, { start, end });
  }

  async function handleEventResize(info: any) {
    const ev = info.event;
    const start = new Date(ev.start).toISOString();
    const end = new Date(ev.end).toISOString();
    events = events.map((e) => (e.id === ev.id ? { ...e, start, end } : e));
    addLog(`Resized "${ev.title}" ends ${new Date(ev.end).toLocaleTimeString()}`);
    await patchEvent(ev.id, { start, end });
  }

  async function handleDateClick(info: any) {
    const start = new Date(info.dateStr ?? info.date?.toISOString());
    const end = new Date(start.getTime() + 30 * 60_000);
    await createEvent('New task', start, end);
  }

  async function handleEventRename(id: string, title: string) {
    const trimmed = title.trim();
    const ev = events.find((e) => e.id === id);
    if (!ev || !trimmed || ev.title === trimmed) return;
    events = events.map((e) => (e.id === id ? { ...e, title: trimmed } : e));
    addLog(`Renamed "${ev.title}" to "${trimmed}"`);
    await patchEvent(id, { title: trimmed });
  }

  async function handleEventRemove(id: string) {
    const ev = events.find((e) => e.id === id);
    // Resolve the provider/occurrence before dropping the event from state,
    // otherwise the lookup finds nothing and the delete targets the wrong one.
    const query = eventQuery(id);
    events = events.filter((e) => e.id !== id);
    addLog(`Removed "${ev?.title ?? id}"`);
    try {
      const resp = await fetch(`/api/events/${id}${query}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`${resp.status}`);
    } catch (err) {
      addLog(`Delete failed: ${err}`);
      await fetchEvents();
    }
  }

  let configuredProviders = $derived(providers.filter((p) => p.configured));
</script>

<!-- .demo is hidden in widget mode via _INJECT_JS in widget.py -->
<div class="demo">
  <h1>Calendar Sidebar</h1>
  {#if loading}
    <p>Connecting...</p>
  {:else if error}
    <p>Could not reach the backend. Is the server running?</p>
  {:else if !configured}
    <p>
      No calendar configured. Set RADICALE_URL, RADICALE_USERNAME and
      RADICALE_PASSWORD in .env, then restart the app.
    </p>
  {:else}
    <p class="status">
      Synced &mdash; polls every 30 s
      {#each configuredProviders as provider (provider.id)}
        &middot; {provider.displayName}
      {/each}
    </p>
    {#if log.length > 0}
      <div class="log">
        {#each log as entry}
          <div class="log-entry">{entry}</div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<SidebarWidget
  {events}
  {open}
  {configured}
  {loading}
  {error}
  {syncError}
  {fetching}
  {lastError}
  {lastSync}
  onToggle={handleToggle}
  onRefresh={fetchEvents}
  onEventClick={handleEventClick}
  onEventDrop={handleEventDrop}
  onEventResize={handleEventResize}
  onDateClick={handleDateClick}
  onSelect={handleSelect}
  onEventRename={handleEventRename}
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
