# cogs/langcodes.py
from __future__ import annotations
from typing import List, Tuple, Optional, Set

# Kuratierte, häufige Sprachen (BCP-47-artig) – nur als UX-Fallback,
# wenn keine Providerliste (DeepL) verfügbar ist.
COMMON_LANG_CHOICES: List[Tuple[str, str]] = [
    ("DE",       "Deutsch"),
    ("EN-GB",    "Englisch (UK)"),
    ("EN-US",    "Englisch (US)"),
    ("EN",       "Englisch (neutral)"),
    ("FR",       "Französisch"),
    ("ES",       "Spanisch"),
    ("PT-PT",    "Portugiesisch (EU)"),
    ("PT-BR",    "Portugiesisch (BR)"),
    ("IT",       "Italienisch"),
    ("NL",       "Niederländisch"),
    ("SV",       "Schwedisch"),
    ("NB",       "Norwegisch (Bokmål)"),
    ("DA",       "Dänisch"),
    ("FI",       "Finnisch"),
    ("PL",       "Polnisch"),
    ("CS",       "Tschechisch"),
    ("HU",       "Ungarisch"),
    ("RO",       "Rumänisch"),
    ("RU",       "Russisch"),
    ("UK",       "Ukrainisch"),
    ("TR",       "Türkisch"),
    ("EL",       "Griechisch"),
    ("BG",       "Bulgarisch"),
    ("ZH",       "Chinesisch (vereinfacht)"),
    ("ZH-HANT",  "Chinesisch (traditionell)"),
    ("JA",       "Japanisch"),
    ("KO",       "Koreanisch"),
    ("AR",       "Arabisch"),
]

# Anzeigenamen-Hints (für Provider-Listen/Anzeige)
NAME_HINTS = {
    "DE": "Deutsch",
    "EN": "Englisch (neutral)",
    "EN-GB": "Englisch (UK)",
    "EN-US": "Englisch (US)",
    "FR": "Französisch",
    "ES": "Spanisch",
    "PT": "Portugiesisch",
    "PT-PT": "Portugiesisch (EU)",
    "PT-BR": "Portugiesisch (BR)",
    "IT": "Italienisch",
    "NL": "Niederländisch",
    "SV": "Schwedisch",
    "NO": "Norwegisch",
    "NB": "Norwegisch (Bokmål)",
    "DA": "Dänisch",
    "FI": "Finnisch",
    "PL": "Polnisch",
    "CS": "Tschechisch",
    "HU": "Ungarisch",
    "RO": "Rumänisch",
    "RU": "Russisch",
    "UK": "Ukrainisch",
    "TR": "Türkisch",
    "EL": "Griechisch",
    "BG": "Bulgarisch",
    "ZH": "Chinesisch (vereinfacht)",
    "ZH-HANT": "Chinesisch (traditionell)",
    "JA": "Japanisch",
    "KO": "Koreanisch",
    "AR": "Arabisch",
}

# Alias-Heuristiken (nutzerfreundlich → provider-valide)
ALIAS_MAP = {
    "EN": "EN-GB",      # neutral → UK (DeepL erwartet EN-GB/EN-US)
    "PT": "PT-PT",      # neutral → EU
    "NO": "NB",         # Norwegisch → Bokmål
    "ZH-CN": "ZH",      # vereinfachtes Chinesisch → ZH
    "ZH-SG": "ZH",      # Singapur → ZH
    "ZH-TW": "ZH-HANT", # Taiwan → traditionell
    "ZH-HK": "ZH-HANT", # Hongkong → traditionell
}

def normalize_code(code: Optional[str]) -> Optional[str]:
    """Uppercase, '_'→'-', Trim."""
    if not code:
        return None
    c = code.strip().upper().replace("_", "-")
    return c or None

def alias_for_provider(code: str, provider_targets: Optional[Set[str]] = None) -> str:
    """Mappe ggf. auf passenderen Code (z. B. EN→EN-GB), falls Provider nur bestimmte Varianten akzeptiert."""
    c = normalize_code(code) or ""
    c2 = ALIAS_MAP.get(c, c)
    if provider_targets:
        if c2 in provider_targets:
            return c2
        trials = []
        if c2.startswith("EN-") and "EN" in provider_targets:
            trials.append("EN")
        if c2 == "EN" and "EN-GB" in provider_targets:
            trials.append("EN-GB")
        if c2 == "PT" and "PT-PT" in provider_targets:
            trials.append("PT-PT")
        if c2 == "NO" and "NB" in provider_targets:
            trials.append("NB")
        for t in trials:
            if t in provider_targets:
                return t
    return c2

def suggest_codes(query: str, provider_targets: Optional[Set[str]] = None) -> list[tuple[str, str]]:
    """Bis zu 20 Vorschläge (code,label). Bevorzugt Providerliste, sonst kuratierte Defaults. Filter per Query."""
    q = (query or "").strip().lower()
    suggestions: list[tuple[str, str]] = []

    if provider_targets:
        for code in sorted(provider_targets):
            label = NAME_HINTS.get(code, code)
            suggestions.append((code, label))
    else:
        suggestions.extend(COMMON_LANG_CHOICES)

    def match(item: tuple[str, str]) -> bool:
        code, label = item
        return (q in code.lower()) or (q in label.lower())

    filtered = [it for it in suggestions if match(it)] if q else suggestions

    seen = set()
    uniq: list[tuple[str, str]] = []
    for code, label in filtered:
        if code in seen:
            continue
        seen.add(code)
        uniq.append((code, label))

    return uniq[:20]

async def setup(bot):
    # Utility-Modul, kein Cog zu registrieren.
    pass
