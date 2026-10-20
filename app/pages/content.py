"""Registrering af de offentlige sider: titel, beskrivelse og senest ændret.

Registret bruges til metadata, sitemap.xml og sidefoden, så en ny offentlig side kun skal tilføjes ét sted.
Siderne må aldrig indeholde brugerdata, heller ikke optællinger eller anonymiserede profiler (specifikation 12.5).
"""
from dataclasses import dataclass
from datetime import date

from flask import current_app, url_for

from ..core.settings import get_setting


@dataclass(frozen=True)
class PublicPage:
    endpoint: str
    nav_label: str
    title: str
    description: str
    updated: date
    priority: str = "0.5"
    # Sider, der kun findes, mens en kampagne er aktiv (fx tidlige fugle). None = altid.
    only_if: str | None = None


PUBLIC_PAGES = [
    PublicPage(
        "core.home", "Forside", "{site} – moderne og diskret datingside for voksne",
        "{site} er en moderne og diskret datingside for voksne. Opret en gratis profil på få minutter, "
        "og mød nye mennesker i nærheden – du bestemmer selv, hvor meget du deler.",
        date(2026, 10, 17), "1.0",
    ),
    PublicPage(
        "pages.purpose", "Hvorfor {site}", "Hvorfor findes {site}? Ærlig dating uden fiduser",
        "{site} er et ærligt alternativ til dyre datingsider: rigtige mennesker, ingen falske profiler eller robotter, "
        "rimelige priser og en administrator, du kan skrive til.",
        date(2026, 10, 18), "0.9",
    ),
    PublicPage(
        "pages.early_bird", "Tidlige fugle", "Premium for livstid til tidlige fugle – {site}",
        "{site} er en ny dansk datingside. Opret en gratis profil nu, mens vi er få, og få Premium for livstid "
        "som tidlig fugl: skriv med dine matches, se hvem der har liket dig, og filtrér i Udforsk.",
        date(2026, 10, 17), "0.9", only_if="early_bird",
    ),
    PublicPage(
        "pages.how", "Sådan virker det", "Sådan virker {site} – profil, likes, matches og beskeder",
        "Se hvordan du kommer i gang på {site}: opret en profil på få minutter, find mennesker i nærheden, "
        "like hinanden og skriv sammen, når I har et match.",
        date(2026, 10, 17), "0.8",
    ),
    PublicPage(
        "pages.safety", "Tryghed og sikkerhed", "Tryghed og sikkerhed på {site}",
        "Sådan beskytter {site} dit privatliv: ingen profiler eller billeder for besøgende, omtrentlig afstand "
        "i stedet for adresse, blokering, anmeldelse og moderation.",
        date(2026, 10, 17), "0.8",
    ),
    PublicPage(
        "pages.premium", "Premium", "Free, Premium og Premium+ på {site}",
        "Se forskellen på Free, Premium og Premium+ på {site}. Nye profiler får Premium gratis i en periode, "
        "og sikkerhedsfunktioner er altid gratis.",
        date(2026, 10, 17), "0.7", only_if="premium",
    ),
    PublicPage(
        "pages.faq", "Ofte stillede spørgsmål", "Ofte stillede spørgsmål om {site}",
        "Svar på de mest stillede spørgsmål om {site}: pris, synlighed, afstand, matches, beskeder, "
        "blokering og sletning af profil.",
        date(2026, 10, 17), "0.7",
    ),
    PublicPage(
        "pages.about", "Om og kontakt", "Om {site} og kontakt",
        "{site} er en dansk datingside for voksne med fokus på enkelhed, diskretion og tryghed. "
        "Her kan du læse om siden og finde kontaktoplysninger.",
        date(2026, 10, 17), "0.5",
    ),
    PublicPage(
        "pages.terms", "Brugervilkår", "Brugervilkår for {site}",
        "Brugervilkårene for {site}: hvem kan bruge siden, regler for opførsel, moderation, abonnementer "
        "og sletning af profil.",
        date(2026, 10, 17), "0.3",
    ),
    PublicPage(
        "pages.privacy", "Privatlivspolitik", "Privatlivspolitik for {site}",
        "Hvilke oplysninger {site} behandler, hvorfor, hvor længe de gemmes, og hvordan du får indsigt, "
        "henter dine data eller sletter din profil.",
        date(2026, 10, 17), "0.3",
    ),
    PublicPage(
        "pages.cookies", "Cookies", "Cookiepolitik for {site}",
        "{site} bruger kun én nødvendig cookie til login og sikkerhed. Ingen sporing og ingen "
        "markedsføringscookies.",
        date(2026, 10, 17), "0.2",
    ),
]

PAGES_BY_ENDPOINT = {page.endpoint: page for page in PUBLIC_PAGES}

# Brands med egne titler og beskrivelser (app/brands). Sider, der ikke står her, bruger de fælles tekster ovenfor.
BRAND_PAGES = {
    "nogen": {
        "core.home": ("{site} – find nogen at gå tur, drikke kaffe eller snakke med",
                      "{site} er et sted at møde nye mennesker til venskab og fællesskab: en gåtur, en kop kaffe "
                      "eller en god snak. Gratis for alle, uden algoritmer."),
        "pages.purpose": ("Hvorfor findes {site}?",
                          "{site} skal gøre det lettere at finde nogen at være sammen med – gratis, trygt og uden "
                          "algoritmer, der bestemmer, hvem du ser."),
        "pages.how": ("Sådan virker {site}",
                      "Se hvordan du kommer i gang på {site}: opret en profil, vælg dine interesser, og find "
                      "nogen at mødes med."),
        "pages.about": ("Om {site} og kontakt",
                        "{site} er en dansk side for venskab og fællesskab. Her kan du læse om siden og finde "
                        "kontaktoplysninger."),
        "pages.faq": ("Ofte stillede spørgsmål om {site}",
                      "Svar på de mest stillede spørgsmål om {site}: synlighed, afstand, forbindelser, beskeder, "
                      "blokering og sletning af profil."),
        "pages.terms": ("Brugervilkår for {site}",
                        "Brugervilkårene for {site}: hvem kan bruge siden, regler for opførsel, moderation og "
                        "sletning af profil."),
    },
}


# Brands med egne spørgsmål øverst i FAQ (app/brands). Resten er de fælles spørgsmål i _faq_items.
BRAND_FAQ = {
    "nogen": [
        ("Er {site} en datingside?",
         "Nej. {site} er til venskab og fællesskab: at finde nogen at gå en tur med, drikke en kop kaffe med, tale "
         "med eller lave noget sammen med. Vil du på date, er det ikke her, du skal lede."),
        ("Hvem er {site} for?",
         "For alle over 18, der savner nogen at være sammen med – fx fordi du er flyttet, har mistet nogen, er gået "
         "på pension eller bare gerne vil møde nye mennesker. På din profil kan du fortælle, hvad du har lyst til, "
         "og under \"Jeg vil helst møde\" kan du vælge, hvem du helst vil møde. Lader du feltet stå tomt, møder du "
         "alle."),
    ],
}


def _site(text):
    from ..brands import current_brand

    return text.replace("{site}", current_brand().name)


def page_active(page):
    if page.only_if == "early_bird":
        from ..subscriptions.services import early_bird_open

        return early_bird_open()
    if page.only_if == "premium":
        from ..brands import current_brand

        return current_brand().premium
    return True


def visible_pages():
    """De offentlige sider, der findes lige nu. Bruges af sitemap, sidefod og hjælpesiden."""
    return [_for_brand(page) for page in PUBLIC_PAGES if page_active(page)]


def _for_brand(page):
    """Siden med brandets egne tekster og navn indsat."""
    from dataclasses import replace

    from ..brands import current_brand

    title, description = BRAND_PAGES.get(current_brand().key, {}).get(page.endpoint, (page.title, page.description))
    return replace(page, nav_label=_site(page.nav_label), title=_site(title), description=_site(description))


def absolute_url(endpoint, **values):
    return current_app.config["BASE_URL"].rstrip("/") + url_for(endpoint, **values)


def seo_for(endpoint):
    """Metadata til base.html: unik titel og beskrivelse samt canonical-URL."""
    from ..brands import current_brand

    page = _for_brand(PAGES_BY_ENDPOINT[endpoint])
    return {
        "title": page.title,
        "description": page.description,
        "canonical": absolute_url(endpoint),
        "image": absolute_url("static", filename=current_brand().asset("og.jpg")),
    }


def faq_items():
    """(spørgsmål, svar) – bruges både på siden og i FAQPage-data, så de altid er ens."""
    from ..subscriptions.services import early_bird_open

    months = get_setting("signup_premium_months")
    early_bird = early_bird_open()
    if early_bird:
        bonus = ("Lige nu får nye profiler Premium for livstid som tidlige fugle, når e-mailen er bekræftet, "
                 "og der er et billede på profilen.")
    elif months:
        bonus = f"Nye profiler får {months} måneders Premium gratis, så snart e-mailen er bekræftet."
    else:
        bonus = "Premium tildeles i øjeblikket ikke automatisk ved oprettelse."
    return [(_site(question), _site(answer)) for question, answer in _faq_items(months, early_bird, bonus)]


def _faq_items(months, early_bird, bonus):
    from ..brands import current_brand

    early_bird_items = [
        ("Hvad er en tidlig fugl?",
         "{site} er ny, og der er endnu ikke så mange profiler. Opretter du en profil nu, bekræfter din e-mail og "
         "uploader et billede, får du Premium for livstid som tak, fordi du er med fra starten. Det kræver ingen "
         "betaling og intet abonnement."),
    ] if early_bird else []
    brand = current_brand()
    if not brand.premium:
        # Uden Premium er alt gratis for alle (app/brands). Ordene er brandets, fx "hilse på" og "forbindelse".
        w = brand.words
        return BRAND_FAQ.get(brand.key, []) + [
            ("Hvad koster {site}?", "Ingenting. Alle funktioner er gratis for alle, og det bliver de ved med at være."),
            ("Hvem kan se min profil?",
             "Kun brugere, der er logget ind. Besøgende uden login kan hverken se profiler eller billeder, og profiler "
             "vises aldrig i søgemaskiner. Du kan skjule enkelte felter, holde pause eller kun være synlig for dem, "
             f"du har {w['a_match']} med."),
            ("Kan andre se, hvor jeg bor?",
             "Nej. Dit postnummer bruges kun til at finde din kommune og vises aldrig. Andre ser en omtrentlig afstand, "
             "fx \"I nærheden\" eller \"ca. 40 km\". Kommunens navn vises kun, hvis du selv tillader det."),
            ("Hvordan kommer jeg i kontakt med nogen?",
             f"Når I begge har trykket \"{w['like']}\", har I {w['a_match']}, og så kan I skrive sammen. Ingen kan "
             f"skrive til dig, før I begge har sagt ja. {w['the_match'].capitalize()} kan altid fjernes igen af begge."),
            ("Hvad er forskellen på at skjule og blokere?",
             "Skjul fjerner en profil fra det, du ser i Udforsk. Blokér forhindrer al kontakt i begge retninger og "
             f"fjerner {w['any_match']}. Ingen af dem giver den anden besked."),
            ("Hvordan anmelder jeg en profil eller en besked?",
             "Brug Anmeld på profilen, i samtalen eller ved den enkelte besked. Anmeldelsen behandles af en moderator, "
             "og den anmeldte får ikke at vide, hvem der har anmeldt."),
            ("Hvordan sletter jeg min profil?",
             f"Under Konto kan du slette din profil. Profil, billeder, {w['likes']} og {w['matches']} slettes. "
             "Beskeder, du har sendt, bliver stående hos modtageren med afsenderen vist som \"Slettet bruger\"."),
            ("Hvor gammel skal man være?", "{site} er kun for personer på 18 år eller derover."),
        ]
    return early_bird_items + [
        ("Hvad koster {site}?",
         f"Det er gratis at oprette en profil, like andre og få matches. {bonus} "
         "Der er endnu ikke indført betaling. Kommer der betaling, får du besked, før noget koster penge."),
        ("Hvem kan se min profil?",
         "Kun brugere, der er logget ind. Besøgende uden login kan hverken se profiler eller billeder, og profiler "
         "vises aldrig i søgemaskiner. Du kan skjule enkelte felter, holde pause eller kun være synlig for dine matches."),
        ("Kan andre se, hvor jeg bor?",
         "Nej. Dit postnummer bruges kun til at finde din kommune og vises aldrig. Andre ser en omtrentlig afstand, "
         "fx \"I nærheden\" eller \"ca. 40 km\". Kommunens navn vises kun, hvis du selv tillader det."),
        ("Hvordan får jeg et match?",
         "Når to personer har liket hinanden, er der et match. Et match kan altid fjernes igen af begge."),
        ("Hvem kan skrive sammen?",
         "Matches kan skrive sammen, når mindst én af dem har Premium eller Premium+. Alle kan altid læse de beskeder, "
         "de modtager."),
        ("Hvad sker der, når min Premium-periode udløber?",
         "Du bliver Free og beholder din profil, dine matches og dine samtaler. Du kan stadig læse beskeder og skrive "
         "med matches, der har Premium."),
        ("Hvad er forskellen på at skjule og blokere?",
         "Skjul fjerner en profil fra det, du ser i Udforsk. Eksisterende matches og samtaler fortsætter. Blokér forhindrer al "
         "kontakt i begge retninger og fjerner et eventuelt match. Ingen af dem giver den anden besked."),
        ("Hvordan anmelder jeg en profil eller en besked?",
         "Brug Anmeld på profilen, i samtalen eller ved den enkelte besked. Anmeldelsen behandles af en moderator, "
         "og den anmeldte får ikke at vide, hvem der har anmeldt."),
        ("Hvordan sletter jeg min profil?",
         "Under Konto kan du slette din profil. Profil, billeder, likes og matches slettes. Beskeder, du har sendt, "
         "bliver stående hos modtageren med afsenderen vist som \"Slettet bruger\"."),
        ("Hvor gammel skal man være?",
         "{site} er kun for personer på 18 år eller derover."),
    ]
