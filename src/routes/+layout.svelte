<script lang="ts">
  let { children } = $props();
</script>

<main>
  {@render children?.()}
</main>

<style>
  :global(:root) {
    --bg: #0c0c0a;
    --ink: #deded9;
    --ink-soft: #555550;
    --line: #1e1e1c;
    --bg-surface: #151513;
    --accent: #5070a8;
    color-scheme: dark;
  }
  :global(*),
  :global(*::before),
  :global(*::after) {
    box-sizing: border-box;
  }
  :global(html, body) {
    margin: 0;
    padding: 0;
    height: 100%;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    font-size: 15px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  :global(button) {
    font-family: inherit;
  }
  :global(button:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  main {
    min-height: 100%;
  }
</style>
