import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Mic, Square, Trash2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { apiClient } from '@/lib/api/client';
import { useAudioRecording } from '@/lib/hooks/useAudioRecording';
import { useTranscription } from '@/lib/hooks/useTranscription';
import { useServerStore } from '@/stores/serverStore';

interface SavedTranscript {
  id: string;
  text: string;
  language: string;
  duration: number;
  created_at: string;
}

/**
 * Dictation — a standalone Speech-to-Text page. Record from the mic or upload
 * an audio file, see the transcription, and keep a saved history of every
 * transcript. Runs against the local Whisper backend (/transcribe), which
 * persists each result and serves it back from /transcriptions.
 */
export function DictationTab() {
  const [transcript, setTranscript] = useState('');
  const [status, setStatus] = useState('');
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  // Background-job state for long uploads (null = no job running).
  const [jobProgress, setJobProgress] = useState<number | null>(null);
  const [jobBusy, setJobBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const transcribe = useTranscription();
  const queryClient = useQueryClient();
  const serverUrl = useServerStore((s) => s.serverUrl);

  const { data: saved } = useQuery({
    queryKey: ['transcriptions'],
    queryFn: async (): Promise<SavedTranscript[]> => {
      const res = await fetch(`${serverUrl}/transcriptions`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      return json.items ?? [];
    },
  });

  async function runTranscribe(file: File) {
    setTranscript('');
    setStatus('Transcribing locally with Whisper…');
    try {
      const result = await transcribe.mutateAsync({ file, language: 'en' });
      setTranscript(result.text?.trim() || '(no speech detected)');
      setStatus(`Done · ${result.duration ?? 0}s of audio · saved`);
      queryClient.invalidateQueries({ queryKey: ['transcriptions'] });
    } catch (e) {
      setStatus(`Failed: ${e instanceof Error ? e.message : 'transcription error'}`);
    }
  }

  /**
   * Transcribe an uploaded file via the background, chunked job — supports
   * multi-hour audio. Polls progress and streams partial text into the view.
   */
  async function runTranscribeJob(file: File) {
    setTranscript('');
    setJobBusy(true);
    setJobProgress(0);
    setStatus('Transcribing long audio in chunks…');
    try {
      const { job_id } = await apiClient.startTranscriptionJob(file, 'en');
      // Poll until the job reaches a terminal state.
      while (true) {
        await new Promise((r) => setTimeout(r, 1500));
        const s = await apiClient.getTranscriptionJob(job_id);
        setJobProgress(s.progress);
        if (s.text) setTranscript(s.text);
        if (s.status === 'completed') {
          setTranscript(s.text?.trim() || '(no speech detected)');
          setStatus(`Done · ${Math.round(s.duration)}s of audio · saved`);
          queryClient.invalidateQueries({ queryKey: ['transcriptions'] });
          break;
        }
        if (s.status === 'error') {
          setStatus(`Failed: ${s.error ?? 'transcription error'}`);
          break;
        }
      }
    } catch (e) {
      setStatus(`Failed: ${e instanceof Error ? e.message : 'transcription error'}`);
    } finally {
      setJobBusy(false);
      setJobProgress(null);
    }
  }

  async function deleteSaved(id: string) {
    await fetch(`${serverUrl}/transcriptions/${id}`, { method: 'DELETE' });
    queryClient.invalidateQueries({ queryKey: ['transcriptions'] });
  }

  const recording = useAudioRecording({
    onRecordingComplete: (blob) => {
      const file = new File([blob], 'recording.webm', { type: blob.type || 'audio/webm' });
      setAudioUrl(URL.createObjectURL(blob));
      runTranscribe(file);
    },
  });

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setAudioUrl(URL.createObjectURL(file));
    // Uploads can be hours long → background chunked job with progress.
    runTranscribeJob(file);
    e.target.value = '';
  }

  const isBusy = transcribe.isPending || jobBusy;
  const progressPct =
    jobProgress !== null ? Math.round(jobProgress * 100) : null;

  return (
    <div className="h-full overflow-y-auto py-8">
      <div className="max-w-3xl">
        <h1 className="text-2xl font-bold">Dictation</h1>
        <p className="text-muted-foreground mt-1 mb-6">
          Speech-to-text, running locally and offline. Record your voice or upload an audio file —
          every transcript is saved below.
        </p>

        <div className="grid gap-4 sm:grid-cols-2">
          {/* Record */}
          <div className="rounded-xl border border-border bg-card p-5 flex flex-col gap-3">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Record your voice
            </span>
            {recording.isRecording ? (
              <Button variant="destructive" onClick={recording.stopRecording} className="gap-2">
                <Square className="h-4 w-4" />
                Stop ({recording.duration}s)
              </Button>
            ) : (
              <Button onClick={recording.startRecording} disabled={isBusy} className="gap-2">
                <Mic className="h-4 w-4" />
                Start recording
              </Button>
            )}
            {recording.error && <p className="text-xs text-destructive">{recording.error}</p>}
          </div>

          {/* Upload */}
          <div className="rounded-xl border border-border bg-card p-5 flex flex-col gap-3">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Upload an audio file
            </span>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={handleFile}
            />
            <Button
              variant="outline"
              onClick={() => fileRef.current?.click()}
              disabled={isBusy || recording.isRecording}
              className="gap-2"
            >
              <Upload className="h-4 w-4" />
              Choose file
            </Button>
          </div>
        </div>

        {audioUrl && (
          // biome-ignore lint/a11y/useMediaCaption: user-provided audio has no captions
          <audio src={audioUrl} controls className="w-full mt-4" />
        )}

        <div className="mt-6">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
            Transcript
          </div>
          <div className="min-h-[96px] rounded-xl border border-border bg-background/60 p-5 text-lg leading-relaxed whitespace-pre-wrap">
            {isBusy && !transcript ? (
              <span className="inline-flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Transcribing…{progressPct !== null ? ` ${progressPct}%` : ''}
              </span>
            ) : (
              transcript || <span className="text-muted-foreground">—</span>
            )}
          </div>
          {jobBusy && progressPct !== null && (
            <div className="mt-3">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-primary transition-[width] duration-500"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Transcribing long audio · {progressPct}%
              </p>
            </div>
          )}
          {status && !isBusy && <p className="text-xs text-muted-foreground mt-2">{status}</p>}
        </div>

        {/* Saved transcripts */}
        <div className="mt-8">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-3">
            Saved transcripts {saved?.length ? `(${saved.length})` : ''}
          </div>
          {saved && saved.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {saved.map((item) => (
                <li
                  key={item.id}
                  className="group flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm leading-relaxed">{item.text}</p>
                    <p className="text-[11px] text-muted-foreground mt-1">
                      {new Date(item.created_at).toLocaleString()} · {item.duration}s ·{' '}
                      {item.language}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => deleteSaved(item.id)}
                    aria-label="Delete transcript"
                    className="text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No transcripts yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}
