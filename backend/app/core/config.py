from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from env vars / .env. See .env.example."""

    app_name: str = "HackTheValley API"
    frontend_origin: str = "http://localhost:5173"
    base_url: str = "http://localhost:8000"

    # Database: SQLite locally, Postgres (+pgvector) in the cloud.
    database_url: str = "sqlite:///./data/dev.db"

    # Auth / security
    jwt_secret: str = "dev-insecure-jwt-secret-change-me"
    jwt_expire_days: int = 7
    # HMAC secret for clinic confirm links; empty = derived from JWT_SECRET with a fixed label
    appointment_token_secret: str = ""
    admin_secret: str = "dev-admin-secret"
    field_encryption_key: str = ""  # Fernet key; generated per-process in dev_mode if empty
    # dev_mode enables the X-User-Id header shortcut. MUST be false in production.
    dev_mode: bool = True

    storage_dir: str = "./data/uploads"
    # Where uploaded files live: "local" = storage_dir on disk; "database" = the stored_files table,
    # for hosts whose disk is wiped on redeploy (Render free). Big streamed imports stay on disk.
    storage_backend: Literal["local", "database"] = "local"
    log_level: str = "INFO"
    log_json: bool = False

    # Third-party APIs
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    openai_api_key: str = ""
    hf_token: str = ""
    # PD vowel model on the HF Hub: used only when the local models/ folder is missing.
    # Pin ``pd_vowel_hub_revision`` to a commit sha so a Hub push can never change what is served.
    pd_vowel_hub_repo: str = ""
    pd_vowel_hub_revision: str = "main"
    # PD-03 may only escalate on the voice score once a model validated on app-extracted audio is served.
    # v1 (UCI/MDVP features) scores AUC 0.31 on real audio through the app's extractor (2026-10-17 audit),
    # so the score is display-only until this is switched on deliberately.
    pd_vowel_rule_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_maps_api_key: str = ""
    resend_api_key: str = ""
    resend_from_email: str = ""
    clinic_fallback_email: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_cheap_model: str = "gemini-3.5-flash-lite"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"   # premade "Sarah" (free plan can use premade voices only)
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    elevenlabs_stt_model: str = "scribe_v1"

    # RAG (English corpus, local models via fastembed)
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5-Q"  # quantized, ~130 MB; same space
    reranker: Literal["cross_encoder", "identity"] = "cross_encoder"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rag_corpus_version: str = "v3"
    # Cross-encoder logit below which a passage is treated as irrelevant (scripts/eval_rag.py calibrates it).
    rag_min_rerank_score: float = -7.5

    # Region (Canada-only for now)
    default_country: str = "CA"
    emergency_number: str = "911"
    health_line_number: str = "811"

    # Booking agent v2 (docs/specs/booking-v2.md)
    # sandbox = every outbound booking (email, call, website) goes to the SANDBOX_* targets the
    # team controls, never to real clinics. live = real clinic contact details. Default sandbox.
    booking_target_mode: Literal["sandbox", "live"] = "sandbox"
    booking_require_approval: bool = True
    sandbox_clinic_email: str = ""
    sandbox_clinic_phone: str = ""        # E.164, e.g. +14165550123 (your own phone)
    sandbox_clinic_website: str = ""      # empty = the built-in mock clinic site at BASE_URL/mock-clinic
    booking_reply_domain: str = ""        # inbound domain on Resend, e.g. book.example.com
    resend_webhook_secret: str = ""       # Svix signing secret for the Resend inbound webhook
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""         # E.164
    elevenlabs_agent_id: str = ""
    elevenlabs_agent_phone_number_id: str = ""
    elevenlabs_webhook_secret: str = ""   # post-call webhook HMAC secret
    web_booking_enabled: bool = True
    # Single-process Chromium for the web channel (~340 MB instead of ~480 MB peak per run).
    web_booking_low_memory: bool = True
    # Watch the agent fill the form: a visible Chromium window (needs a display + the full Chromium
    # build, not only the headless shell). Low-memory flags are dropped in headed mode.
    web_booking_headed: bool = False
    # Delay (ms) after every browser action so people can follow along; 0 = full speed. The hard
    # timeout grows with it automatically (60 s + fields x slow_mo x 4, capped at 5 min).
    web_booking_slow_mo_ms: int = 0
    # Canadian Suicide Crisis Helpline (call or text, 24/7), used in crisis guidance text.
    crisis_line_number: str = "9-8-8"

    # Provider switches ("fake" = deterministic offline implementations for tests/dev)
    embedding_provider: Literal["local", "voyage", "fake"] = "fake"
    llm_provider: Literal["gemini", "anthropic", "fake"] = "fake"
    stt_provider: Literal["elevenlabs", "openai", "fake"] = "fake"
    tts_provider: Literal["elevenlabs", "openai", "fake"] = "fake"

    # Rate limits on chat / uploads / tasks (app/core/ratelimit.py); tests disable them.
    rate_limit_enabled: bool = True
    rate_limit_default: str = "30/minute"

    scheduler_enabled: bool = False
    scheduler_timezone: str = "America/Toronto"
    email_provider: Literal["auto", "resend", "fake"] = "auto"  # auto = resend when RESEND_API_KEY is set

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgres")


def check_production_settings(s: Settings) -> None:
    """Fail fast when deployed (DEV_MODE=false) with dev defaults."""
    if s.dev_mode:
        return
    problems = []
    if s.jwt_secret.startswith("dev-") or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET")
    if s.admin_secret.startswith("dev-"):
        problems.append("ADMIN_SECRET")
    if not s.field_encryption_key:
        problems.append("FIELD_ENCRYPTION_KEY")
    if s.email_provider != "fake" and not (s.resend_api_key and s.resend_from_email):
        # otherwise get_email_provider() would silently fall back to FakeEmail in production
        problems.append("RESEND_API_KEY and RESEND_FROM_EMAIL (or EMAIL_PROVIDER=fake to disable e-mail)")
    if problems:
        raise RuntimeError(f"Set production values for: {', '.join(problems)}")


settings = Settings()
