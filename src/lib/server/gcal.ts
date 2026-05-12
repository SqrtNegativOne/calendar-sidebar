import { google } from 'googleapis';
import { env } from '$env/dynamic/private';
import { loadTokens, saveTokens } from './tokens.js';

const SCOPES = ['https://www.googleapis.com/auth/calendar.events'];

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
}

function buildClient() {
  const client = new google.auth.OAuth2(
    env.GOOGLE_CLIENT_ID,
    env.GOOGLE_CLIENT_SECRET,
    env.GOOGLE_REDIRECT_URI ?? 'http://localhost:5173/api/auth/callback',
  );

  const tokens = loadTokens();
  if (tokens) {
    client.setCredentials(tokens);
    client.on('tokens', (refreshed) => saveTokens(refreshed));
  }

  return client;
}

export function getAuthUrl(): string {
  const client = buildClient();
  return client.generateAuthUrl({
    access_type: 'offline',
    scope: SCOPES,
    prompt: 'consent',
  });
}

export async function exchangeCode(code: string): Promise<void> {
  const client = buildClient();
  const { tokens } = await client.getToken(code);
  saveTokens(tokens);
}

function calendarId(): string {
  return env.GOOGLE_CALENDAR_ID ?? 'primary';
}

function localTz(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function mapEvent(ev: { id?: string | null; summary?: string | null; start?: { dateTime?: string | null; date?: string | null } | null; end?: { dateTime?: string | null; date?: string | null } | null }): CalendarEvent {
  return {
    id: ev.id!,
    title: ev.summary ?? '(no title)',
    start: ev.start!.dateTime ?? `${ev.start!.date}T00:00:00`,
    end: ev.end!.dateTime ?? `${ev.end!.date}T00:00:00`,
  };
}

export async function listTodayEvents(): Promise<CalendarEvent[]> {
  const client = buildClient();
  const cal = google.calendar({ version: 'v3', auth: client });

  const now = new Date();
  const timeMin = new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
  const timeMax = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).toISOString();

  const resp = await cal.events.list({
    calendarId: calendarId(),
    timeMin,
    timeMax,
    singleEvents: true,
    orderBy: 'startTime',
  });

  return (resp.data.items ?? [])
    .filter(ev => ev.id && ev.start && (ev.start.dateTime || ev.start.date))
    .map(mapEvent);
}

export async function createEvent(event: Omit<CalendarEvent, 'id'>): Promise<CalendarEvent> {
  const client = buildClient();
  const cal = google.calendar({ version: 'v3', auth: client });
  const tz = localTz();

  const resp = await cal.events.insert({
    calendarId: calendarId(),
    requestBody: {
      summary: event.title,
      start: { dateTime: event.start, timeZone: tz },
      end: { dateTime: event.end, timeZone: tz },
    },
  });

  return mapEvent(resp.data);
}

export async function updateEvent(gcalId: string, patch: Partial<Omit<CalendarEvent, 'id'>>): Promise<CalendarEvent> {
  const client = buildClient();
  const cal = google.calendar({ version: 'v3', auth: client });
  const tz = localTz();

  const resp = await cal.events.patch({
    calendarId: calendarId(),
    eventId: gcalId,
    requestBody: {
      ...(patch.title !== undefined && { summary: patch.title }),
      ...(patch.start !== undefined && { start: { dateTime: patch.start, timeZone: tz } }),
      ...(patch.end !== undefined && { end: { dateTime: patch.end, timeZone: tz } }),
    },
  });

  return mapEvent(resp.data);
}

export async function deleteEvent(gcalId: string): Promise<void> {
  const client = buildClient();
  const cal = google.calendar({ version: 'v3', auth: client });
  await cal.events.delete({ calendarId: calendarId(), eventId: gcalId });
}
