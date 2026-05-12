import { json, error } from '@sveltejs/kit';
import { listTodayEvents, createEvent } from '$lib/server/gcal.js';
import { loadTokens } from '$lib/server/tokens.js';
import type { RequestHandler } from './$types.js';

function requireAuth() {
  const tokens = loadTokens();
  if (!tokens?.access_token && !tokens?.refresh_token) error(401, 'Not authenticated');
}

export const GET: RequestHandler = async () => {
  requireAuth();
  const events = await listTodayEvents();
  return json(events);
};

export const POST: RequestHandler = async ({ request }) => {
  requireAuth();
  const { title, start, end } = await request.json() as { title: string; start: string; end: string };
  const ev = await createEvent({ title, start, end });
  return json(ev, { status: 201 });
};
