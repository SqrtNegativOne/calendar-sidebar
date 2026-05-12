import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const TOKEN_FILE = join(process.cwd(), '.tokens.json');

export interface StoredTokens {
  access_token?: string | null;
  refresh_token?: string | null;
  expiry_date?: number | null;
  token_type?: string | null;
  scope?: string;
}

export function loadTokens(): StoredTokens | null {
  if (!existsSync(TOKEN_FILE)) return null;
  try {
    const raw = readFileSync(TOKEN_FILE, 'utf-8');
    const parsed = JSON.parse(raw) as StoredTokens;
    return Object.keys(parsed).length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

export function saveTokens(tokens: StoredTokens): void {
  const existing = loadTokens() ?? {};
  writeFileSync(TOKEN_FILE, JSON.stringify({ ...existing, ...tokens }, null, 2));
}
