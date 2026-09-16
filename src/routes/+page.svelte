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
  let authenticated = $state(false);
  let providers = $state<ProviderStatus[]>([]);
  let loading = $state(true);
  let error = $state(false);
  let syncError = $state(false);
  let fetching = $state(false);
  let lastError = $state<string | null>(null);
  let lastSync = $state<Date | null>(null);
  let connectHint = $state<string | null>(null);
  let events: ECEvent[] = $state([]);
  let log: string[] = $state([]);

  function addLog(msg: string) {
    log = [msg, ...log].slice(0, 20);
  }

  function activeProvider(): string | undefined {
    return providers.find((p) => p.authenticated)?.id;
  }

  // Events are merged from every connected provider, so edits/deletes must be
  // routed back to the provider and calendar the event actually came from.
  function eventQuery(id: string): string {
    const ev = events.find((e) => e.id === id);
    const params = new URLSearchParams();
    if (ev?.provider) params.set('provider', ev.provider);
    if (ev?.calendarId) params.set('calendar_id', ev.calendarId);
    const qs = params.toString();
    return qs ? `?${qs}` : '';
  }

  async function fetchEvents() {
    fetching = true;
    try {
      const resp = await fetch('/api/events');
      if (!resp.ok) {
        if (resp.status === 401) {
          authenticated = false;
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
  let statusInterval: ReturnType<typeof setInterval> | undefined;

  async function checkStatus(): Promise<boolean> {
    try {
      const resp = await fetch('/api/auth/status');
      if (!resp.ok) throw new Error(`${resp.status}`);
      const status = (await resp.json()) as {
        authenticated: boolean;
        providers?: ProviderStatus[];
      };
      providers = status.providers ?? [];
      authenticated = status.authenticated;
      error = false;
      return true;
    } catch {
      return false;
    }
  }

  // Keep probing while the backend is down *or* the user hasn't connected yet,
  // so the panel recovers on its own — including after a system-browser login.
  function watchStatus() {
    if (statusInterval) return;
    statusInterval = setInterval(async () => {
      if (!(await checkStatus())) return;
      if (!authenticated) return;
      stopStatusWatch();
      connectHint = null;
      await fetchEvents();
      startPolling();
    }, RETRY_MS);
  }

  function stopStatusWatch() {
    clearInterval(statusInterval);
    statusInterval = undefined;
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
    if (!document.hidden && open && authenticated) fetchEvents();
  }

  // Token expired mid-session — drop back to the reconnect prompt
  $effect(() => {
    if (!authenticated) stopPolling();
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
        watchStatus();
        return;
      }
      if (!authenticated) {
        watchStatus();
        return;
      }

      await fetchEvents();
      startPolling();
    })();

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      stopPolling();
      stopStatusWatch();
    };
  });

  function handleToggle() {
    open = !open;
    // Catch up on anything missed while the sidebar was closed
    if (open && authenticated) fetchEvents();
  }
  function handleEventClick(_info: any) {}

  // Google/Microsoft reject OAuth inside the widget's embedded WebEngine (and
  // WebAuthn passkey prompts don't work there), so the backend hands us a
  // consent URL, we copy it to the clipboard for the user to open in any
  // browser, then poll until the callback stores the token.
  async function copyToClipboard(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Async clipboard API unavailable/blocked inside WebEngine — fall back
      // to the legacy execCommand path below.
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
  }

  async function handleConnect(provider: string) {
    try {
      const resp = await fetch(`/api/auth/${encodeURIComponent(provider)}/url`);
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = (await resp.json()) as { url?: string };
      if (!data.url) throw new Error('No sign-in URL returned');
      await copyToClipboard(data.url);
      connectHint = 'Sign-in link copied — open it in any browser, then come back. This panel updates on its own.';
      addLog(`Sign-in link copied to clipboard (${provider})`);
      watchStatus();
    } catch (err) {
      connectHint = null;
      addLog(`Connect failed: ${err}`);
    }
  }

  async function createEvent(title: string, start: Date, end: Date) {
    const provider = activeProvider();
    try {
      const resp = await fetch('/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          start: start.toISOString(),
          end: end.toISOString(),
          provider
        }),
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      const newEv = (await resp.json()) as ECEvent;
      events = [...events, newEv];
      addLog(`Created task "${title}"`);
    } catch (err) {
      addLog(`Create failed: ${err}`);
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
    } catch {
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
    // Resolve the provider before dropping the event from state, otherwise the
    // lookup finds nothing and the delete falls back to the wrong provider.
    const query = eventQuery(id);
    events = events.filter((e) => e.id !== id);
    addLog(`Removed "${ev?.title ?? id}"`);
    try {
      const resp = await fetch(`/api/events/${id}${query}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`${resp.status}`);
    } catch {
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
  {:else if !authenticated}
    <p>Connect a calendar to get started.</p>
    {#each configuredProviders as provider (provider.id)}
      <a class="connect-btn" href={`/api/auth/${provider.id}`}>
        Connect {provider.displayName}
      </a>
    {/each}
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

<SidebarWidget
  {events}
  {open}
  {authenticated}
  {providers}
  {loading}
  {error}
  {syncError}
  {fetching}
  {lastError}
  {lastSync}
  onToggle={handleToggle}
  onRefresh={fetchEvents}
  onConnect={handleConnect}
  connectHint={connectHint}
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

  .connect-btn {
    display: inline-block;
    padding: 0.5rem 1.2rem;
    margin: 0 0.5rem 0.5rem 0;
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
