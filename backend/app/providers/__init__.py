"""External-service provider abstractions (embeddings, speech-to-text, text-to-speech).

Each module exposes a ``Protocol``, a real implementation, a deterministic fake
and a ``get_*_provider()`` factory selected by an environment variable read
lazily at call time (no settings import):

* ``EMBEDDING_PROVIDER`` = ``fake`` (default) | ``local`` (fastembed, no key) | ``voyage`` — key ``VOYAGE_API_KEY``
* ``STT_PROVIDER``       = ``fake`` (default) | ``elevenlabs`` | ``openai``  — keys ``ELEVENLABS_API_KEY`` / ``OPENAI_API_KEY``
* ``TTS_PROVIDER``       = ``fake`` (default) | ``elevenlabs`` | ``openai``  — keys ``ELEVENLABS_API_KEY`` / ``OPENAI_API_KEY``
"""


from app.providers.embeddings import EmbeddingProvider, ProviderConfigError, FakeEmbeddings, LocalEmbeddings, VoyageEmbeddings, cosine, get_embedding_provider
from app.providers.stt import ElevenLabsStt, FakeStt, OpenAIWhisperStt, SttProvider, Transcript, get_stt_provider
from app.providers.tts import ElevenLabsTts, FakeTts, OpenAITts, TtsProvider, get_tts_provider

__all__ = [
    "ProviderConfigError",
    "EmbeddingProvider", "FakeEmbeddings", "LocalEmbeddings", "VoyageEmbeddings", "cosine", "get_embedding_provider",
    "SttProvider", "Transcript", "FakeStt", "OpenAIWhisperStt", "ElevenLabsStt", "get_stt_provider",
    "TtsProvider", "FakeTts", "OpenAITts", "ElevenLabsTts", "get_tts_provider",
]
