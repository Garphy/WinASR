export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed';

export interface Task {
  id: string;
  filename: string;
  status: TaskStatus;
  progress: number;
  createdAt: string;
  completedAt: string | null;
}

export interface Segment {
  id: string;
  speaker: string;
  start: number;
  end: number;
  text: string;
  emotion: string;
  language: string;
  isModified: boolean;
}

export type SpeakerMap = Record<string, string>;

export interface AudioMetadata {
  duration: number;
  sampleRate: number;
}

export interface TranscriptionResult {
  taskId: string;
  metadata: AudioMetadata;
  speakerMap: SpeakerMap;
  segments: Segment[];
  peaks: number[];
}
