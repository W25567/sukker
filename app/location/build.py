"""Bygger kommunedata ud fra DAGI-geometrier (Klimadatastyrelsen via Datafordeleren).

Trin:
1. Henter kommune- og postnummerpolygoner fra Datafordelerens DAGI WFS, eller læser dem fra GeoJSON-filer.
2. Finder kommunerne for hvert postnummer ud fra arealoverlap.
3. Beregner den korteste afstand mellem hver to kommuners grænser.
4. Skriver resultatet som CSV i data/geo/, som `load-geo` indlæser i databasen.

Kræver shapely (requirements-dev.txt). Koordinater forventes i EPSG:25832, så afstande er i meter.
"""
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

WFS_URL = "https://wfs.datafordeler.dk/DAGI/DAGI_WFS/1.0.0/WFS"
KOMMUNE_LAYER = "dagi_v001:kommuneinddeling_current"
POSTNUMMER_LAYER = "dagi_v001:postnummerinddeling_current"
PAGE_SIZE = 500

# Mindste andel af et postnummers landareal, før en kommune tæller med. Under 5 % spørges brugeren ikke,
# og den primære kommune bruges. Det koster højst lidt præcision, fordi nabokommuner har afstanden 0.
MIN_SHARE = 0.05
# Polygonerne forenkles før afstandsberegning. 100 m er langt under den afrunding, der vises.
SIMPLIFY_METERS = 100

KOMMUNE_CODE_KEYS = ("kommunekode", "kode", "KOMKODE")
KOMMUNE_NAME_KEYS = ("navn", "kommunenavn", "KOMNAVN")
POSTCODE_KEYS = ("postnummer", "nr", "postnr", "POSTNR_TXT")
POSTCODE_NAME_KEYS = ("navn", "postnummernavn", "POSTBYNAVN")


def _pick(properties, keys, what):
    for key in keys:
        if properties.get(key) not in (None, ""):
            return str(properties[key]).strip()
    raise ValueError(f"Fandt ikke {what} i egenskaberne: {sorted(properties)}")


RETRIES = 6
RETRY_SECONDS = 20


def _get_json(url):
    """Henter JSON og prøver igen ved 401 og 5xx. En ny nøgle kan være op til 15 minutter om at virke på alle
    Datafordelerens servere, så 401 kan forekomme på enkelte kald."""
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if attempt == RETRIES or not (error.code == 401 or error.code >= 500):
                raise
            time.sleep(RETRY_SECONDS)


def fetch_wfs_layer(layer, apikey, wfs_url=WFS_URL):
    """Henter alle features i et lag, side for side, som GeoJSON-features i EPSG:25832."""
    features, start = [], 0
    while True:
        query = urllib.parse.urlencode({
            "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer,
            "outputFormat": "application/json", "srsName": "EPSG:25832",
            "count": PAGE_SIZE, "startIndex": start, "apikey": apikey,
        })
        page = _get_json(f"{wfs_url}?{query}")
        batch = page.get("features", [])
        features.extend(batch)
        if len(batch) < PAGE_SIZE:
            return features
        start += PAGE_SIZE


def read_geojson(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["features"]


def build(kommune_features, postnummer_features):
    """Returnerer (kommuner, postnumre, postnummer_kommuner, afstande) som lister af tupler."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    kommune_parts, kommune_names = defaultdict(list), {}
    for feature in kommune_features:
        props = feature["properties"]
        if props.get("udenforKommuneinddeling"):
            continue  # områder uden for kommuneinddelingen, fx Christiansø
        code = _pick(props, KOMMUNE_CODE_KEYS, "kommunekode").zfill(4)
        kommune_names[code] = _pick(props, KOMMUNE_NAME_KEYS, "kommunenavn")
        kommune_parts[code].append(shape(feature["geometry"]))
    kommuner = {code: unary_union(parts) for code, parts in kommune_parts.items()}

    postcode_parts, postcode_names = defaultdict(list), {}
    for feature in postnummer_features:
        props = feature["properties"]
        postcode = _pick(props, POSTCODE_KEYS, "postnummer").zfill(4)
        postcode_names[postcode] = _pick(props, POSTCODE_NAME_KEYS, "postnummernavn")
        postcode_parts[postcode].append(shape(feature["geometry"]))

    mapping = []
    for postcode, parts in sorted(postcode_parts.items()):
        area_geom = unary_union(parts)
        # Postnumre dækker også havområder, mens kommuner kun dækker land. Andelen regnes derfor af det
        # landareal, postnummeret har i kommunerne, ikke af hele postnummerets areal.
        overlaps = [(code, area_geom.intersection(geom).area) for code, geom in kommuner.items()
                    if area_geom.intersects(geom)]
        land = sum(area for _, area in overlaps)
        if land <= 0:
            continue
        shares = sorted(((code, area / land) for code, area in overlaps if area > 0), key=lambda s: -s[1])
        primary = shares[0][0]
        for code, share in shares:
            if share >= MIN_SHARE or code == primary:
                mapping.append((postcode, code, round(share, 4), code == primary))

    simplified = {code: geom.simplify(SIMPLIFY_METERS) for code, geom in kommuner.items()}
    codes = sorted(simplified)
    distances = []
    for i, a in enumerate(codes):
        distances.append((a, a, 0.0))
        for b in codes[i + 1:]:
            km = round(simplified[a].distance(simplified[b]) / 1000, 1)
            distances.append((a, b, km))
            distances.append((b, a, km))

    used_postcodes = {m[0] for m in mapping}
    return (
        sorted(kommune_names.items()),
        sorted((p, n) for p, n in postcode_names.items() if p in used_postcodes),
        mapping,
        sorted(distances),
    )


def write_csv(directory, kommuner, postnumre, mapping, distances):
    os.makedirs(directory, exist_ok=True)
    files = {
        "kommuner.csv": (["code", "name"], kommuner),
        "postnumre.csv": (["postcode", "name"], postnumre),
        "postnummer_kommuner.csv": (["postcode", "kommune_code", "share", "is_primary"], mapping),
        "kommune_afstande.csv": (["from_code", "to_code", "km"], distances),
    }
    for name, (header, rows) in files.items():
        with open(os.path.join(directory, name), "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
