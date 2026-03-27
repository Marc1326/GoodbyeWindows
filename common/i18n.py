"""GoodbyeWindows — Internationalization (i18n) system."""

import json
import locale
from pathlib import Path

_current_locale: str = "en"
_translations: dict[str, str] = {}
_locales_dir = Path(__file__).parent / "locales"


def detect_locale() -> str:
    """Detect system locale and return 'de' or 'en'."""
    sys_locale = locale.getdefaultlocale()[0] or ""
    if sys_locale.startswith("de"):
        return "de"
    return "en"


def set_locale(loc: str) -> None:
    """Load translations for the given locale."""
    global _current_locale, _translations
    _current_locale = loc
    locale_file = _locales_dir / f"{loc}.json"
    if locale_file.exists():
        _translations = json.loads(locale_file.read_text(encoding="utf-8"))
    else:
        _translations = {}


def get_locale() -> str:
    """Return the current locale string."""
    return _current_locale


def tr(key: str, **kwargs) -> str:
    """Translate a key. Supports {placeholder} formatting."""
    text = _translations.get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def available_locales() -> list[str]:
    """Return list of available locale codes."""
    return sorted(p.stem for p in _locales_dir.glob("*.json"))


# Auto-detect on import
set_locale(detect_locale())
