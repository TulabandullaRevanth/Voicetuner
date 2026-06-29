#!/usr/bin/env python3
"""
Offline voice-cloning helper — called as a subprocess by offline-stub-server.py.

Must be run with the backend venv Python (which has qwen_tts, torch, etc.):
  backend/venv/bin/python3 scripts/qwen_clone_helper.py \
      --ref-audio path/to/sample.wav \
      --ref-text  "Hello my name is Revanth" \
      --text      "The text to synthesize" \
      --language  en \
      --output    /tmp/out.wav \
      --model-size 0.6B

Writes a WAV file to --output and exits 0 on success, non-zero on failure.
Prints progress lines to stderr so the parent can optionally forward them.
"""
import argparse
import sys
import warnings
warnings.filterwarnings("ignore")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-audio",   required=True)
    ap.add_argument("--ref-text",    default="")
    ap.add_argument("--text",        required=True)
    ap.add_argument("--language",    default="en")
    ap.add_argument("--output",      required=True)
    ap.add_argument("--model-size",  default="0.6B")
    args = ap.parse_args()

    HF_REPOS = {
        "0.6B": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "1.7B": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    }
    repo = HF_REPOS.get(args.model_size, HF_REPOS["0.6B"])

    print(f"[qwen-clone] loading {repo} …", file=sys.stderr)
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as e:
        print(f"[qwen-clone] import error: {e}", file=sys.stderr)
        sys.exit(1)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"[qwen-clone] device={device}", file=sys.stderr)

    try:
        import torch
        dtype = torch.float16 if device == "mps" else torch.float32
        model = Qwen3TTSModel.from_pretrained(
            repo,
            device_map=device,
            torch_dtype=dtype,
        )
    except Exception as e:
        print(f"[qwen-clone] model load failed: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"[qwen-clone] creating voice prompt from {args.ref_audio} …", file=sys.stderr)
    try:
        voice_prompt = model.create_voice_clone_prompt(
            ref_audio=args.ref_audio,
            ref_text=args.ref_text or None,
            x_vector_only_mode=False,
        )
    except Exception as e:
        print(f"[qwen-clone] voice prompt failed: {e}", file=sys.stderr)
        sys.exit(3)

    LANG_MAP = {
        "en": "English", "hi": "Hindi", "te": "Telugu", "zh": "Chinese",
        "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German",
        "es": "Spanish", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    }
    lang_name = LANG_MAP.get(args.language, "English")

    print(f"[qwen-clone] synthesising {len(args.text)} chars in {lang_name} …", file=sys.stderr)

    # Chunk text to stay within model context (≤600 chars per chunk)
    CHUNK = 600
    words = args.text.split()
    chunks, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > CHUNK and cur:
            chunks.append(cur.strip())
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        chunks.append(cur)

    import numpy as np
    import wave, struct

    all_audio = []
    sample_rate = 24000
    for i, chunk in enumerate(chunks):
        print(f"[qwen-clone] chunk {i+1}/{len(chunks)} ({len(chunk)} chars) …", file=sys.stderr)
        try:
            wavs, sr = model.generate_voice_clone(
                text=chunk,
                voice_clone_prompt=voice_prompt,
                language=lang_name,
                max_new_tokens=8192,
            )
            sample_rate = sr
            all_audio.append(wavs[0])
        except Exception as e:
            print(f"[qwen-clone] chunk {i+1} failed: {e}", file=sys.stderr)
            sys.exit(4)

    # Merge chunks and write WAV
    merged = np.concatenate(all_audio) if len(all_audio) > 1 else all_audio[0]
    # Normalise to int16
    if merged.dtype != np.int16:
        peak = np.abs(merged).max()
        if peak > 0:
            merged = (merged / peak * 32767 * 0.95).astype(np.int16)
        else:
            merged = merged.astype(np.int16)

    out_path = args.output
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(merged.tobytes())

    dur = len(merged) / sample_rate
    print(f"[qwen-clone] wrote {out_path}  ({dur:.1f}s)", file=sys.stderr)

if __name__ == "__main__":
    main()
