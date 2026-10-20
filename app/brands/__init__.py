"""Brands: samme kode, flere selvstændige sider (Sukker.nu, Nogen.nu).

Hvert brand kører som sin egen installation med egen database, egne filer og eget domæne (deploy.py). Koden er
fælles, og forskellene står ét sted, her. Koden spørger aldrig "er det Nogen.nu?", men "er modulet slået til?" eller
"hvad hedder siden?" via current_brand().

Hvad et brand bestemmer:
- navn, domæne, farve og logo (static/brands/<key>/) og egne tekster (templates/brands/<key>/ overstyrer de fælles
  skabeloner med samme navn)
- ordene for likes og matches (words), så de fælles skabeloner kan sige "Like" eller "Hils på"
- hvilke moduler der er slået til (Premium og tidlige fugle, privat album, datingfelterne på profilen)
- hvad "Søger" på profilen spørger om (profiles/choices.LOOKING_FOR) og standardkataloget over interesser
  (profiles/interests.DEFAULT_CATALOGS)
- standardværdier for indstillingerne (core/settings.py), som administrator stadig kan ændre

Brandet vælges med SUKKER_BRAND (config.BRAND). Standarden er "sukker".
"""
from dataclasses import dataclass, field

from flask import current_app, has_app_context

# Ordene for likes og matches. Skabeloner bruger dem som {{ brand.words.match }}, Python som
# current_brand().words["match"]. Står ordet først i en sætning, bruges | capitalize ("matchet" → "Matchet").
# Alle brands skal have de samme nøgler (tests/test_brands.py). Sukker.nu's ord er dem, siden altid har brugt.
DATING_WORDS = {
    # Handlingen (knappen i _like.html)
    "like": "Like",  # knappen
    "liked": "liket",  # "Du har liket", "profiler, jeg har liket"
    "to_like": "like",  # "Du kan ikke like dig selv"
    "give_likes": "give likes",  # "før du kan give likes"
    "likes": "likes",  # "Ingen nye likes lige nu"
    "liked_you": "har liket dig",
    "like_each_other": "liker hinanden",
    "like_back": "liker tilbage",  # "indtil de liker tilbage"
    "likes_you_back": "liker dig tilbage",  # "Når en, du har liket, liker dig tilbage"
    # Resultatet, når begge har sagt ja
    "matched": "Match",  # mærket i stedet for knappen
    "match": "match",
    "a_match": "et match",
    "the_match": "matchet",
    "matches": "matches",
    "new_match": "nyt match",  # "Nyt match med Anna"
    "new_badge": "Nyt",  # mærket ved et nyt match på listen
    "a_new_match": "et nyt match",
    "any_match": "et eventuelt match",
    "ended_match": "et ophørt match",
    "to_match": "matche",  # "For at matche igen"
    "matched_again": "Matcher I igen",  # står først i sætningen (| capitalize ville gøre "I" til "i")
    "matched_flash": "Det er et match! Du og {name} kan lide hinanden.",
    "nav": "Likes",  # menupunktet og overskriften på siden med matches
    # "Interesseret i" (køn) og gruppen med "Søger"
    "seeking_genders": "Interesseret i",
    "seeking_hint": "Du ser kun de køn, du vælger, og kun de køn kan se og like dig. Vælger du ingen, er alle velkomne.",
    "seeking_edit_hint": "",  # under feltet, når profilen redigeres (tom = ingen hjælpetekst)
    "not_interested": "Profilen søger ikke kontakt med dit køn",
    "relation_group": "Relation",
}

FRIENDSHIP_WORDS = {
    "like": "Hils på",
    "liked": "hilst på",
    "to_like": "hilse på",
    "give_likes": "hilse på andre",
    "likes": "hilsner",
    "liked_you": "har hilst på dig",
    "like_each_other": "hilser på hinanden",
    "like_back": "hilser igen",
    "likes_you_back": "hilser igen",
    "matched": "Forbundet",
    "match": "forbindelse",
    "a_match": "en forbindelse",
    "the_match": "forbindelsen",
    "matches": "forbindelser",
    "new_match": "ny forbindelse",
    "new_badge": "Ny",
    "a_new_match": "en ny forbindelse",
    "any_match": "en eventuel forbindelse",
    "ended_match": "en ophørt forbindelse",
    "to_match": "blive forbundet",
    "matched_again": "Bliver I forbundet igen",
    "matched_flash": "I er forbundet! Du og {name} har hilst på hinanden.",
    "nav": "Forbindelser",
    "seeking_genders": "Jeg vil helst møde",
    "seeking_hint": "Du ser kun de køn, du vælger, og kun de køn kan se og hilse på dig. Lader du feltet stå tomt, "
                    "møder du alle.",
    "seeking_edit_hint": "Lader du feltet stå tomt, møder du alle.",
    "not_interested": "Profilen vil helst møde andre køn end dit",
    "relation_group": "Samvær",
}


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
    # Ønsker børn, aldersinterval, afstand, langdistance og forventninger på profilen (profiles/fields.py).
    dating_fields: bool = True
    # Hvad feltet relationship_types spørger om: "Søger" kæreste, dating … eller "Hvad har du lyst til" (kaffe,
    # gåture …). Nøgle i profiles/choices.LOOKING_FOR.
    looking_for: str = "relationship"
    # Standardkataloget over interesser, når administrator ikke har gemt sit eget (profiles/interests.py).
    interest_catalog: str = "dating"
    # Adressen på siden om, hvorfor siden findes (pages.purpose). Andre brands sendes videre fra den fælles adresse.
    purpose_path: str = "/hvorfor-sukker"
    words: dict = field(default_factory=lambda: dict(DATING_WORDS))
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
        # Venskab, ikke dating: man hilser på hinanden og bliver forbundet, og profilen spørger ikke om kærester og børn.
        dating_fields=False, looking_for="activities", interest_catalog="venskab", words=FRIENDSHIP_WORDS,
        purpose_path="/hvorfor",
        setting_defaults={"signup_premium_months": 0, "early_bird_enabled": False, "free_match_limit_enabled": False},
    ),
}
DEFAULT_BRAND = "sukker"
DEFAULT_PURPOSE_PATH = Brand.purpose_path  # "/hvorfor-sukker", formålssidens oprindelige adresse


def brand_for(key):
    if key not in BRANDS:
        raise KeyError(f"Ukendt brand: {key!r}. Findes: {', '.join(BRANDS)}.")
    return BRANDS[key]


def current_brand():
    """Brandet for den kørende app. Uden app-kontekst (fx i rene enhedstests) bruges standardbrandet."""
    if has_app_context():
        return brand_for(current_app.config.get("BRAND") or DEFAULT_BRAND)
    return BRANDS[DEFAULT_BRAND]
