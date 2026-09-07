export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  status?: 'completed' | 'working' | 'error';
}

export type SessionStatus = 'idle' | 'working' | 'completed' | 'error';

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: Message[];
  status: SessionStatus;
}

export type SessionsData = Record<string, Session>;
