"""T-1.1: Settings — префиксы под-секций и опциональные секции (P1-1, P1-2).

Приёмка:
- ``Settings()`` в пустом env конструируется (все секции по умолчанию);
- чужие env-переменные (USER, PORT, LANGUAGE, MODEL) не «протекают» в секции
  (нужен префикс ``SECTION__``);
- ``SECTION__FIELD`` переменные по-прежнему применяются;
- под-секции работают standalone со своим ``env_prefix``.
"""

import pathlib

import pytest
from swot_contracts import Settings
from swot_contracts.config import BrokerSettings, LangfuseSettings

# Переменные окружения, которые обязаны игнорироваться без префикса
# (регрессия P1-1: ранее Settings читал «чужие» переменные процесса).
_NOISY_ENV = {
    "USER": "macuser",
    "PORT": "9999",
    "LANGUAGE": "en_US:en",
    "MODEL": "x",
}
# Все SECTION__* и скалярные переменные Settings, которые могли остаться
# в окружении разработчика.
_SWOT_ENV = [
    "MEDIA_DIR",
    "ARTIFACTS_DIR",
    "HEALTH_PORT",
    "BROKER__HOST",
    "BROKER__PORT",
    "BROKER__USER",
    "BROKER__PASSWORD",
    "DOWNLOADER__ALLOWED_SOURCES",
    "DOWNLOADER__MAX_VIDEO_DURATION_SEC",
    "DOWNLOADER__RETENTION_HOURS",
    "LANGFUSE__HOST",
    "LANGFUSE__PORT",
    "LANGFUSE__PUBLIC_KEY",
    "LANGFUSE__SECRET_KEY",
    "LLM__API_URL",
    "LLM__API_KEY",
    "LLM__MODEL_ID",
    "LLM__SAMPLING_PARAMETERS",
    "LLM__SUMMARIZATION_PROMPT_NAME",
    "TELEGRAM__TOKEN",
    "TELEGRAM__ADMIN_ID",
    "TELEGRAM__TARGET_CHAT_ID",
    "TELEGRAM__API_BASE_URL",
    "TRANSCRIBER__API_URL",
    "TRANSCRIBER__API_KEY",
    "TRANSCRIBER__MODEL",
    "TRANSCRIBER__LANGUAGE",
]


@pytest.fixture
def clean_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустое env для Settings: переменные очищены, рядом нет .env файла."""
    for key in [*_NOISY_ENV, *_SWOT_ENV]:
        monkeypatch.delenv(key, raising=False)
    # chdir в tmp: env_file=".env" не находит файл проекта
    monkeypatch.chdir(tmp_path)


def test_settings_empty_env_uses_defaults(clean_env: None) -> None:
    """Settings() в пустом env конструируется — все секции по умолчанию."""
    settings = Settings()

    # скаляры
    assert settings.media_dir == "/data/media"
    assert settings.artifacts_dir == "/data/artifacts"
    assert settings.health_port == 8080
    # broker — дефолты (не USER/PORT из env)
    assert settings.broker.user == "guest"
    assert settings.broker.port == 5672
    assert settings.broker.host == "rabbitmq"
    # langfuse — пустые ключи (аналитик без Langfuse, T-0.4 fallback)
    assert isinstance(settings.langfuse, LangfuseSettings)
    assert settings.langfuse.public_key == ""
    assert settings.langfuse.secret_key == ""
    # transcriber — дефолты (не из LANGUAGE/MODEL)
    assert settings.transcriber.language == ""
    assert settings.transcriber.model == "whisper-1"
    assert settings.transcriber.api_url == "asr-service"


def test_noisy_env_does_not_leak_into_sections(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-1: USER/PORT/LANGUAGE/MODEL в env не меняют значения секций."""
    for key, value in _NOISY_ENV.items():
        monkeypatch.setenv(key, value)

    settings = Settings()

    assert settings.broker.user == "guest"
    assert settings.broker.port == 5672
    assert settings.transcriber.language == ""
    assert settings.transcriber.model == "whisper-1"


def test_section_override_via_prefixed_env(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SECTION__FIELD по-прежнему применяется (docker-compose scheme)."""
    monkeypatch.setenv("BROKER__HOST", "rabbit-x")
    monkeypatch.setenv("BROKER__PORT", "5673")
    monkeypatch.setenv("LLM__MODEL_ID", "test-model")
    monkeypatch.setenv("LANGFUSE__PUBLIC_KEY", "pk-1")

    settings = Settings()

    assert settings.broker.host == "rabbit-x"
    assert settings.broker.port == 5673
    assert settings.llm.model_id == "test-model"
    assert settings.langfuse.public_key == "pk-1"
    # остальные поля той же секции — по умолчанию
    assert settings.broker.user == "guest"


def test_standalone_subsettings_use_env_prefix(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-2: BrokerSettings() standalone читает свои BROKER__* переменные."""
    monkeypatch.setenv("BROKER__HOST", "standalone-rabbit")

    broker = BrokerSettings()

    assert broker.host == "standalone-rabbit"
    assert broker.user == "guest"  # USER из env не читается


def test_scalar_fields_still_read_from_env(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Скалярные поля Settings читаются из своих переменных без префикса."""
    monkeypatch.setenv("MEDIA_DIR", "/tmp/media-x")
    monkeypatch.setenv("HEALTH_PORT", "9000")

    settings = Settings()

    assert settings.media_dir == "/tmp/media-x"
    assert settings.health_port == 9000
