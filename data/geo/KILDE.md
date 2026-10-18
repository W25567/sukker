# Kommunedata

Filerne i denne mappe er afledt af DAGI (Danmarks Administrative Geografiske Inddeling) og indeholder
data fra Klimadatastyrelsen, hentet via Datafordeleren (lagene `kommuneinddeling_current` og
`postnummerinddeling_current`).

- `kommuner.csv`: kommunekode og navn
- `postnumre.csv`: postnummer og navn
- `postnummer_kommuner.csv`: kommunerne for hvert postnummer med andel af landarealet (mindst 5 %)
- `kommune_afstande.csv`: afstand i km mellem kommunernes midtpunkter (tyngdepunktet af landarealet).
  Samme kommune har 0. Tidligere blev den korteste afstand mellem grænserne brugt, men så fik alle
  nabokommuner 0, fx Odense og Middelfart.

Opdatering: `flask sukker build-geo` (kræver `SUKKER_DATAFORDELER_APIKEY` og shapely) og derefter
`flask sukker load-geo`.
