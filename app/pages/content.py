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
        "core.home", "Forside", "Sukker.nu – moderne og diskret datingside for voksne",
        "Sukker.nu er en moderne og diskret datingside for voksne. Opret en gratis profil på få minutter, "
        "og mød nye mennesker i nærheden – du bestemmer selv, hvor meget du deler.",
        date(2026, 10, 17), "1.0",
    ),
    PublicPage(
        "pages.early_bird", "Tidlige fugle", "Premium for livstid til tidlige fugle – Sukker.nu",
        "Sukker.nu er en ny dansk datingside. Opret en gratis profil nu, mens vi er få, og få Premium for livstid "
        "som tidlig fugl: skriv med dine matches, se hvem der har liket dig, og filtrér i Udforsk.",
        date(2026, 10, 17), "0.9", only_if="early_bird",
    ),
    PublicPage(
        "pages.how", "Sådan virker det", "Sådan virker Sukker.nu – profil, likes, matches og beskeder",
        "Se hvordan du kommer i gang på Sukker.nu: opret en profil på få minutter, find mennesker i nærheden, "
        "like hinanden og skriv sammen, når I har et match.",
        date(2026, 10, 17), "0.8",
    ),
    PublicPage(
        "pages.safety", "Tryghed og sikkerhed", "Tryghed og sikkerhed på Sukker.nu",
        "Sådan beskytter Sukker.nu dit privatliv: ingen profiler eller billeder for besøgende, omtrentlig afstand "
        "i stedet for adresse, blokering, anmeldelse og moderation.",
        date(2026, 10, 17), "0.8",
    ),
    PublicPage(
        "pages.premium", "Premium", "Free, Premium og Premium+ på Sukker.nu",
        "Se forskellen på Free, Premium og Premium+ på Sukker.nu. Nye profiler får Premium gratis i en periode, "
        "og sikkerhedsfunktioner er altid gratis.",
        date(2026, 10, 17), "0.7",
    ),
    PublicPage(
        "pages.faq", "Ofte stillede spørgsmål", "Ofte stillede spørgsmål om Sukker.nu",
        "Svar på de mest stillede spørgsmål om Sukker.nu: pris, synlighed, afstand, matches, beskeder, "
        "blokering og sletning af profil.",
        date(2026, 10, 17), "0.7",
    ),
    PublicPage(
        "pages.about", "Om og kontakt", "Om Sukker.nu og kontakt",
        "Sukker.nu er en dansk datingside for voksne med fokus på enkelhed, diskretion og tryghed. "
        "Her kan du læse om siden og finde kontaktoplysninger.",
        date(2026, 10, 17), "0.5",
    ),
    PublicPage(
        "pages.terms", "Brugervilkår", "Brugervilkår for Sukker.nu",
        "Brugervilkårene for Sukker.nu: hvem kan bruge siden, regler for opførsel, moderation, abonnementer "
        "og sletning af profil.",
        date(2026, 10, 17), "0.3",
    ),
    PublicPage(
        "pages.privacy", "Privatlivspolitik", "Privatlivspolitik for Sukker.nu",
        "Hvilke oplysninger Sukker.nu behandler, hvorfor, hvor længe de gemmes, og hvordan du får indsigt, "
        "henter dine data eller sletter din profil.",
        date(2026, 10, 17), "0.3",
    ),
    PublicPage(
        "pages.cookies", "Cookies", "Cookiepolitik for Sukker.nu",
        "Sukker.nu bruger kun én nødvendig cookie til login og sikkerhed. Ingen sporing og ingen "
        "markedsføringscookies.",
        date(2026, 10, 17), "0.2",
    ),
]

PAGES_BY_ENDPOINT = {page.endpoint: page for page in PUBLIC_PAGES}


def page_active(page):
    if page.only_if == "early_bird":
        from ..subscriptions.services import early_bird_open

        return early_bird_open()
    return True


def visible_pages():
    """De offentlige sider, der findes lige nu. Bruges af sitemap, sidefod og hjælpesiden."""
    return [page for page in PUBLIC_PAGES if page_active(page)]


def absolute_url(endpoint, **values):
    return current_app.config["BASE_URL"].rstrip("/") + url_for(endpoint, **values)


def seo_for(endpoint):
    """Metadata til base.html: unik titel og beskrivelse samt canonical-URL."""
    page = PAGES_BY_ENDPOINT[endpoint]
    return {
        "title": page.title,
        "description": page.description,
        "canonical": absolute_url(endpoint),
        "image": absolute_url("static", filename="images/og.jpg"),
    }


def faq_items():
    """(spørgsmål, svar) – bruges både på siden og i FAQPage-data, så de altid er ens."""
    from ..subscriptions.services import early_bird_open

    months = get_setting("signup_premium_months")
    early_bird = early_bird_open()
    if early_bird:
        bonus = "Lige nu får nye profiler Premium for livstid som tidlige fugle, når e-mailen er bekræftet."
    elif months:
        bonus = f"Nye profiler får {months} måneders Premium gratis, så snart e-mailen er bekræftet."
    else:
        bonus = "Premium tildeles i øjeblikket ikke automatisk ved oprettelse."
    early_bird_items = [
        ("Hvad er en tidlig fugl?",
         "Sukker.nu er ny, og der er endnu ikke så mange profiler. Opretter du en profil nu og bekræfter din e-mail, "
         "får du Premium for livstid som tak, fordi du er med fra starten. Det kræver ingen betaling og intet abonnement."),
    ] if early_bird else []
    return early_bird_items + [
        ("Hvad koster Sukker.nu?",
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
         "Sukker.nu er kun for personer på 18 år eller derover."),
    ]
