/**
 * Supported languages for voice generation, per engine.
 *
 * VoiceTuner restricts the platform to English, Hindi, and Telugu.
 *
 * Local engines cannot serve Telugu (and only Kokoro/Chatterbox serve Hindi),
 * so Telugu — and high-quality Hindi — route to the cloud tier:
 *   - sarvam:     en/hi/te (primary, purpose-built for Indic)
 *   - elevenlabs: en/hi/te (voice cloning + premium English)
 * Local engines remain as the offline en/hi fallback.
 */

/** All languages VoiceTuner supports. */
export const ALL_LANGUAGES = {
  en: 'English',
  hi: 'हिन्दी',
  te: 'తెలుగు',
} as const;

export type LanguageCode = keyof typeof ALL_LANGUAGES;

/** Per-engine supported language codes (intersection with {en,hi,te}). */
export const ENGINE_LANGUAGES: Record<string, readonly LanguageCode[]> = {
  // Local engines — English-capable; Hindi only on kokoro/chatterbox; no Telugu.
  qwen: ['en'],
  luxtts: ['en'],
  chatterbox: ['en', 'hi'],
  chatterbox_turbo: ['en'],
  tada: ['en'],
  kokoro: ['en', 'hi'],
  qwen_custom_voice: ['en'],
  // Cloud engines — full trilingual coverage, including Telugu.
  sarvam: ['en', 'hi', 'te'],
  elevenlabs: ['en', 'hi', 'te'],
} as const;

/** Helper: get language options for a given engine. */
export function getLanguageOptionsForEngine(engine: string) {
  const codes = ENGINE_LANGUAGES[engine] ?? ENGINE_LANGUAGES.sarvam;
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
