/**
 * Supported languages for voice generation, per engine.
 *
 * VoiceTuner is English-only. All TTS/STT runs locally by default.
 */

/** All languages VoiceTuner supports. */
export const ALL_LANGUAGES = {
  en: 'English',
} as const;

export type LanguageCode = keyof typeof ALL_LANGUAGES;

/** Per-engine supported language codes. */
export const ENGINE_LANGUAGES: Record<string, readonly LanguageCode[]> = {
  qwen: ['en'],
  luxtts: ['en'],
  chatterbox: ['en'],
  chatterbox_turbo: ['en'],
  tada: ['en'],
  kokoro: ['en'],
  qwen_custom_voice: ['en'],
  sarvam: ['en'],
  elevenlabs: ['en'],
} as const;

/** Helper: get language options for a given engine. */
export function getLanguageOptionsForEngine(engine: string) {
  const codes = ENGINE_LANGUAGES[engine] ?? ['en'];
  return codes.map((code) => ({
    value: code,
    label: ALL_LANGUAGES[code],
  }));
}

// ── Backwards-compatible exports used elsewhere ──────────────────────
export const SUPPORTED_LANGUAGES = ALL_LANGUAGES;
export const LANGUAGE_CODES = Object.keys(ALL_LANGUAGES) as LanguageCode[];
export const LANGUAGE_OPTIONS = LANGUAGE_CODES.map((code) => ({
  value: code,
  label: ALL_LANGUAGES[code],
}));
