"""
F5-TTS backend implementation.

Wraps the F5-TTS model (SWivid/F5-TTS) for zero-shot voice cloning.
~800MB, 24kHz output. Supports English and Chinese; strong cross-lingual
cloning quality. Reference audio is passed directly at generation time
(lazy voice prompt — no pre-encoding step needed).
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from . import TTSBackend
from .base import (
    is_model_cached,
    get_torch_device,
    empty_device_cache,
    manual_seed,
    combine_voice_prompts as _combine_voice_prompts,
    model_load_progress,
)
from ..utils.cache import get_cache_key, get_cached_voice_prompt, cache_voice_prompt

logger = logging.getLogger(__name__)

F5TTS_HF_REPO = "SWivid/F5-TTS"
SAMPLE_RATE = 24000


class F5TTSBackend:
    """F5-TTS backend for zero-shot voice cloning."""

    def __init__(self):
        self.model = None
        self.vocoder = None
        self._device = None

    def _get_device(self) -> str:
        return get_torch_device(allow_mps=True, allow_xpu=False)

    def is_loaded(self) -> bool:
        return self.model is not None

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = self._get_device()
        return self._device

    def _get_model_path(self, model_size: str = "default") -> str:
        return F5TTS_HF_REPO

    def _is_model_cached(self, model_size: str = "default") -> bool:
        return is_model_cached(
            F5TTS_HF_REPO,
            weight_extensions=(".pt", ".safetensors", ".bin"),
        )

    async def load_model(self, model_size: str = "default") -> None:
        if self.model is not None:
            return
        await asyncio.to_thread(self._load_model_sync)

    def _load_model_sync(self):
        is_cached = self._is_model_cached()
        with model_load_progress("f5tts", is_cached):
            from f5_tts.api import F5TTS

            device = self.device
            logger.info("Loading F5-TTS on %s...", device)
            self.model = F5TTS(device=device)
            self._device = device
        logger.info("F5-TTS loaded successfully")

    def unload_model(self) -> None:
        if self.model is not None:
            device = self.device
            del self.model
            self.model = None
            self._device = None
            empty_device_cache(device)
            logger.info("F5-TTS unloaded")

    async def create_voice_prompt(
        self,
        audio_path: str,
        reference_text: str,
        use_cache: bool = True,
    ) -> Tuple[dict, bool]:
        """Store reference audio path + text as the voice prompt.

        F5-TTS conditions on the raw reference waveform at inference time,
        so there is no separate encoding step.
        """
        prompt = {"ref_audio": str(audio_path), "ref_text": reference_text}
        return prompt, False

    async def combine_voice_prompts(self, audio_paths, reference_texts):
        """Concatenate multiple reference clips into one combined clip."""
        return await _combine_voice_prompts(audio_paths, reference_texts, sample_rate=SAMPLE_RATE)

    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> Tuple[np.ndarray, int]:
        """Generate audio using F5-TTS zero-shot voice cloning."""
        await self.load_model()

        ref_audio = voice_prompt.get("ref_audio", "")
        ref_text = voice_prompt.get("ref_text", "")

        def _generate_sync():
            import torch
            if seed is not None:
                manual_seed(seed, self.device)

            wav, sr, _ = self.model.infer(
                ref_file=ref_audio,
                ref_text=ref_text,
                gen_text=text,
                nfe_step=16,
                target_rms=0.1,
                cross_fade_duration=0.15,
                speed=1.0,
                show_info=logger.debug,
            )

            if isinstance(wav, np.ndarray):
                audio = wav.astype(np.float32)
            else:
                audio = wav.detach().cpu().numpy().squeeze().astype(np.float32)

            return audio, int(sr)

        return await asyncio.to_thread(_generate_sync)
