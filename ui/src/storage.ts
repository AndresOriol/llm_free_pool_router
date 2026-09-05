import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import { Session, SessionsData, Message, SessionStatus } from './types';

const DATA_DIR = path.join(process.cwd(), '.ui_data');
const SESSIONS_FILE = path.join(DATA_DIR, 'sessions.json');

function ensureStorage(): void {
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
  if (!fs.existsSync(SESSIONS_FILE)) {
    fs.writeFileSync(SESSIONS_FILE, JSON.stringify({}, null, 2), 'utf-8');
  }
}

function loadAll(): SessionsData {
  ensureStorage();
  try {
    const content = fs.readFileSync(SESSIONS_FILE, 'utf-8');
    return JSON.parse(content) as SessionsData;
  } catch (error) {
    return {};
  }
}

function saveAll(data: SessionsData): void {
  ensureStorage();
  fs.writeFileSync(SESSIONS_FILE, JSON.stringify(data, null, 2), 'utf-8');
}

export function getAllSessions(): Session[] {
  const data = loadAll();
  const sessions = Object.values(data);
  sessions.sort((a, b) => {
    const dateA = a.updated_at || a.created_at || '';
    const dateB = b.updated_at || b.created_at || '';
    return dateB.localeCompare(dateA);
  });
  return sessions;
}

export function getSession(sessionId: string): Session | undefined {
  const data = loadAll();
  return data[sessionId];
}

export function createSession(title: string = 'New Task'): string {
  const data = loadAll();
  const sessionId = crypto.randomUUID().slice(0, 8);
  const now = new Date().toISOString();
  data[sessionId] = {
    id: sessionId,
    title,
    created_at: now,
    updated_at: now,
    messages: [],
    status: 'idle',
  };
  saveAll(data);
  return sessionId;
}

export function updateSession(
  sessionId: string,
  messages: Message[],
  status: SessionStatus = 'idle',
  title?: string
): void {
  const data = loadAll();
  if (data[sessionId]) {
    data[sessionId].messages = messages;
    data[sessionId].status = status;
    data[sessionId].updated_at = new Date().toISOString();
    if (title) {
      data[sessionId].title = title;
    }
    saveAll(data);
  }
}

export function deleteSession(sessionId: string): void {
  const data = loadAll();
  if (data[sessionId]) {
    delete data[sessionId];
    saveAll(data);
  }
}
