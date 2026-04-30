import type { Task, TranscriptionResult } from '../types';

export const API_BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`API error ${response.status}: ${error}`);
  }
  return response.json();
}

export async function uploadAudio(file: File): Promise<Task> {
  const formData = new FormData();
  formData.append('file', file);

  return request<Task>(`${API_BASE}/tasks`, {
    method: 'POST',
    body: formData,
  });
}

export async function getTasks(): Promise<Task[]> {
  return request<Task[]>(`${API_BASE}/tasks`);
}

export async function getTask(taskId: string): Promise<Task> {
  return request<Task>(`${API_BASE}/tasks/${taskId}`);
}

export async function getTaskResult(taskId: string): Promise<TranscriptionResult> {
  return request<TranscriptionResult>(`${API_BASE}/tasks/${taskId}/result`);
}
