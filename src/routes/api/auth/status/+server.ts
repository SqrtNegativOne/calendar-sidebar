import { json } from '@sveltejs/kit';
import { loadTokens } from '$lib/server/tokens.js';

export function GET() {
  const tokens = loadTokens();
  return json({ authenticated: !!(tokens?.access_token || tokens?.refresh_token) });
}
