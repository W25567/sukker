"""Brands: samme kode, flere selvstændige sider (Sukker.nu, Nogen.nu).

Hvert brand kører som sin egen installation med egen database, egne filer og eget domæne (deploy.py). Koden er
fælles, og forskellene står ét sted, her. Koden spørger aldrig "er det Nogen.nu?", men "er modulet slået til?" eller
"hvad hedder siden?" via current_brand().

Hvad et brand bestemmer:
- navn, domæne, farve og logo (static/brands/<key>/) og egne tekster (templates/brands/<key>/ overstyrer de fælles
  skabeloner med samme navn)
- hvilke moduler der er slået til (Premium og tidlige fugle, privat album)
- standardværdier for indstillingerne (core/settings.py), som administrator stadig kan ændre

Brandet vælges med SUKKER_BRAND (config.BRAND). Standarden er "sukker".
"""
from dataclasses import dataclass, field

from flask import current_app, has_app_context


@dataclass(frozen=True)
class Brand:
    key: str
    name: str  # "Sukker.nu"
    name_main: str  # "Sukker" (logoteksten, før ".nu")
    name_tld: str  # ".nu"
    domain: str  # "sukker.nu" – links til siden selv er ikke "kontaktoplysninger" (core/contact_info.py)
    tagline: str
    theme_color: str  # mobilbrowserens farve og manifestet
    background_color: str
    og_alt: str  # alt-tekst til delingsbilledet
    logo_size: tuple = (38, 32)  # logo-64.webp vist i topbaren (bredde, højde)
    svg_favicon: bool = False  # favicon.svg findes
    stylesheet: bool = False  # brand.css med egne farver lægges oven i app.css
    # Moduler. En funktion, der er slået fra, er skjult i brugerfladen og afvist på serveren.
    premium: bool = True  # Free/Premium/Premium+, tidlige fugle og påmindelser om udløb
    private_album: bool = True
    # Standardværdier for indstillinger, der afviger fra core/settings.DEFAULTS.
    setting_defaults: dict = field(default_factory=dict)

    @property
    def compact(self):
        """Navnet uden tegn, fx "sukkernu". Bruges til at afvise viste navne, der ligner siden (core/name_rules.py)."""
        return "".join(ch for ch in self.name.lower() if ch.isalnum())

    def asset(self, filename):
        """Stien under static/ til brandets egne filer (logo, favicon, manifest, delingsbillede)."""
        return f"brands/{self.key}/{filename}"


BRANDS = {
    "sukker": Brand(
        key="sukker", name="Sukker.nu", name_main="Sukker", name_tld=".nu", domain="sukker.nu",
        tagline="Dating for voksne – diskret og enkelt", theme_color="#a8325a", background_color="#faf7f5",
        og_alt="Sukker.nu – dating for voksne", svg_favicon=True,
    ),
    "nogen": Brand(
        key="nogen", name="Nogen.nu", name_main="nogen", name_tld=".nu", domain="nogen.nu",
        tagline="Find nogen at gå tur, drikke kaffe eller snakke med", theme_color="#1a8a7f",
        background_color="#f6f8f8", og_alt="Nogen.nu – find nogen at mødes med", logo_size=(40, 32), stylesheet=True,
        # Ingen skal betale for at finde nogen: alle funktioner er gratis, og der er intet privat album.
        premium=False, private_album=False,
        setting_defaults={"signup_premium_months": 0, "early_bird_enabled": False, "free_match_limit_enabled": False},
    ),
}
DEFAULT_BRAND = "sukker"


def brand_for(key):
    if key not in BRANDS:
        raise KeyError(f"Ukendt brand: {key!r}. Findes: {', '.join(BRANDS)}.")
    return BRANDS[key]


def current_brand():
    """Brandet for den kørende app. Uden app-kontekst (fx i rene enhedstests) bruges standardbrandet."""
    if has_app_context():
        return brand_for(current_app.config.get("BRAND") or DEFAULT_BRAND)
    return BRANDS[DEFAULT_BRAND]
