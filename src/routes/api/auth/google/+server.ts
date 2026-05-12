import { redirect } from '@sveltejs/kit';
import { getAuthUrl } from '$lib/server/gcal.js';

export function GET(): never {
  redirect(302, getAuthUrl());
}
