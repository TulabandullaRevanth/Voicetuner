"""Database package — ORM models, session management, and migrations.

Re-exports all public symbols so that ``from .database import get_db``
and ``from .database import Generation as DBGeneration`` continue to work
without changing any importers.
"""

from .models import (
    Base,
    AudioChannel,
    ChannelDeviceMapping,
    EffectPreset,
    Generation,
    GenerationSettings,
    GenerationVersion,
    MCPClientBinding,
    ProfileChannelMapping,
    ProfileSample,
    Project,
    Story,
    StoryItem,
    Transcription,
    VoiceProfile,
)
from .session import engine, SessionLocal, init_db, get_db

__all__ = [
    # Models
    "Base",
    "AudioChannel",
    "ChannelDeviceMapping",
    "EffectPreset",
    "Generation",
    "GenerationSettings",
    "GenerationVersion",
    "MCPClientBinding",
    "ProfileChannelMapping",
    "ProfileSample",
    "Project",
    "Story",
    "StoryItem",
    "Transcription",
    "VoiceProfile",
    # Session
    "engine",
    "SessionLocal",
    "init_db",
    "get_db",
]
