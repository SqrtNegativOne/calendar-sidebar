import { redirect, error } from '@sveltejs/kit';
import { exchangeCode } from '$lib/server/gcal.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ url }) => {
  const code = url.searchParams.get('code');
  if (!code) error(400, 'Missing OAuth code');

  await exchangeCode(code);
  redirect(302, '/');
};
