import { json, error } from '@sveltejs/kit';
import { updateEvent, deleteEvent } from '$lib/server/gcal.js';
import { loadTokens } from '$lib/server/tokens.js';
import type { RequestHandler } from './$types.js';

function requireAuth() {
  const tokens = loadTokens();
  if (!tokens?.access_token && !tokens?.refresh_token) error(401, 'Not authenticated');
}

export const PATCH: RequestHandler = async ({ params, request }) => {
  requireAuth();
  const patch = await request.json() as { title?: string; start?: string; end?: string };
  const ev = await updateEvent(params.id, patch);
  return json(ev);
};

export const DELETE: RequestHandler = async ({ params }) => {
  requireAuth();
  await deleteEvent(params.id);
  return new Response(null, { status: 204 });
};
