declare module '@event-calendar/core' {
  import type { SvelteComponent } from 'svelte';

  export class Calendar extends SvelteComponent<{
    plugins: unknown[];
    options: Record<string, unknown>;
  }> {}

  export const TimeGrid: unknown;
  export const Interaction: unknown;
  export const DayGrid: unknown;
  export const List: unknown;
  export const ResourceTimeGrid: unknown;
  export const ResourceTimeline: unknown;
}
