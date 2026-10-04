# Nieuws — persoonlijke Nederlandse nieuwsaggregator

> Your own locally hosted (Dutch) news website

Een complete nieuwservaring die artikelen van vijf Nederlandse uitgevers ophaalt,
samenvoegt tot **verhalen** (stories), rangschikt en personaliseert.

```
http://127.0.0.1:8500
```

---

## Architectuur

Elke laag is een apart, vervangbaar onderdeel. Niets downstream weet iets van de
HTML of feed-eigenaardigheden van een individuele uitgever.

| # | Laag | Bestand | Verantwoordelijkheid |
|---|------|---------|----------------------|
| 1 | Bron-ingestie | `app/ingest/` | Adapters per mechanisme (`rss.py`, `html.py`) achter één contract (`base.py`); `pipeline.py` draait alle bronnen parallel |
| 2 | Normalisatie | `app/normalise.py` | `RawItem` → gemeenschappelijk Article-model + idempotente upsert |
| 3 | Story-clustering | `app/cluster.py` | TF-IDF-cosinus + entiteit-overlap + categorie + tijd → stabiele story-ids |
| 4 | Topic/entiteit-extractie | `app/topics.py` | Nederlandse tokenisatie, entiteiten, curated topics, geo-scope, categorie-mapping |
| 5 | Story-synthese | `app/summarise.py` | Neutrale kop, extractieve samenvatting, tegenstrijdigheden |
| 6 | Ranking | `app/ranking.py` | Twee gescheiden modellen: voorpagina vs. Mijn nieuws |
| 7 | Voorkeuren | `app/personalise.py` | Favorieten, instellingen |
| 8 | Gedragssignalen | `app/personalise.py` | Interacties met tijdsverval |
| 9 | Personalisatie | `app/personalise.py` | Score, uitleg, diversiteit, serendipiteit |
| 10 | Presentatie | `app/api.py` + `web/` | JSON-API en responsieve SPA |

### Een uitgever toevoegen

Via de UI (**Favorieten → Bronnen beheren**) of `POST /api/sources`:

```json
{ "publisher_name": "Trouw", "url": "https://www.trouw.nl/rss.xml" }
```

In code: voeg een regel toe aan `PUBLISHERS`/`FEEDS` in `app/config.py`.
Een nieuw *mechanisme* (JSON-API, GraphQL) = een nieuwe adapterklasse met een
`fetch(source) -> FetchResult`-methode, geregistreerd in `ingest/pipeline.ADAPTERS`.

---

## Databronnen

Alleen **officiële, openbaar toegankelijke feeds**. Er wordt niets omzeild:
geen paywalls, geen logins, geen consent-schermen, geen robots.txt-overtredingen.

| Uitgever | Feed(s) |
|----------|---------|
| NU.nl | `nu.nl/rss/{Algemeen,Binnenland,Buitenland,Politiek,Economie,Tech,Sport,Achterklap}` |
| NOS | `feeds.nos.nl/nosnieuws{algemeen,binnenland,buitenland,politiek,economie,tech,cultuurenmedia}`, `nossportalgemeen` |
| RTL Nieuws | `rtlnieuws.nl/rss.xml` — de officiële open feed. `rtl.nl/rss/*` is slechts een redirect door een consent-gate en wordt **niet** omzeild |
| Omrop Fryslân | `omropfryslan.nl/rss/nieuws` (hun `/rss/<pad>`-varianten geven allemaal dezelfde feed terug) |
| AD | `ad.nl/{nieuws,binnenland,buitenland,politiek,economie,tech,sport,show}/rss.xml` |

Als een bron wegvalt, degradeert alleen die bron. De statusbalk bovenin en
**Bronnen beheren** tonen precies welke feed faalde en waarom.

---

## Datamodel (SQLite, `data/nieuws.db`)

```
publishers ──< sources ──< articles >── stories ──< story_topics >── topics
                                                        │
user_preferences      user_interactions      recommendations
```

| Tabel | Kern |
|-------|------|
| `publishers` | id, naam, homepage, regio, gewicht, kleur, user_added |
| `sources` | feed-url, kind (`rss`/`html`), categorie-hint, gezondheidsstatus |
| `articles` | kop, uitgever, originele URL, canonical URL + hash (dedupe), publicatie-/updatetijd, categorie, afbeelding, beschrijving, taal, geo-scope + plaatsen, topics, entiteiten, tokens, revisie, story_id |
| `stories` | neutrale kop + bronuitgever, gegenereerde samenvatting, categorie, geo-scope, aantal artikelen/uitgevers, eerste/laatste tijd, centroid, entiteiten, tegenstrijdigheden, is_updating, importance/frontpage/trending-scores |
| `topics` | slug, label, soort (topic/category/person/org/place) |
| `story_topics` | story ↔ topic met gewicht |
| `user_preferences` | expliciete favorieten + instellingen |
| `user_interactions` | open, open_article, save, hide, more, less (met tijdstempel) |
| `recommendations` | laatst geserveerde Mijn-nieuws-ranglijst met redenen |

---

## Story-clustering

Een artikel sluit aan bij een bestaand verhaal als de gecombineerde score boven
`ASSIGN_THRESHOLD` (0,42) ligt:

```
0,44 · TF-IDF-cosinus(titel×2 + beschrijving)
0,40 · entiteit-overlap (Jaccard + containment, inclusief achternamen)
0,04 · gelijke categorie
0,12 · publicatietijd-nabijheid (e^-Δuur/30)
```

Met een harde randvoorwaarde: `tokencosinus ≥ 0,16`. Gedeelde namen zonder
gedeelde woordkeuze kunnen dus nooit twee gebeurtenissen samenvoegen.
Een tweede, strengere pass (0,56) hersmelt verhalen die naar elkaar toe groeien
als late berichtgeving binnenkomt; het oudste/grootste story-id blijft behouden
zodat opgeslagen interacties blijven kloppen.

### Kop en samenvatting

* **Kop** — de meest *centrale* en minst sensationele uitgeverskop wordt
  geselecteerd (clickbait-, vraag- en citaatpatronen krijgen een penalty) en
  genormaliseerd (`LIVE:`, `UPDATE:`, `VIDEO |` worden gestript). De bron wordt
  altijd vermeld; er wordt niets geparafraseerd.
* **Samenvatting** — extractief: 1-3 zinnen uit de beschrijvingen van
  *verschillende* uitgevers, gekozen op termdichtheid en ontdubbeld. De UI labelt
  dit expliciet als **✨ Automatisch samengevat uit de bronteksten**.
* **Tegenstrijdigheden** — getallen met dezelfde eenheid worden vergeleken;
  bij >12 % spreiding tussen uitgevers verschijnt een waarschuwingsblok in plaats
  van een schijnzekerheid.

---

## Ranking

**Voorpagina** (identiek voor iedereen):

```
0,34 recentheid  0,26 breedte(¹)  0,10 nog in beweging
0,14 prominentie 0,16 nieuwswaarde-categorie
```

¹ `log(1+uitgevers)/log(5)` — bij vier onafhankelijke uitgevers is het signaal
maximaal. Veel artikelen alleen maken een verhaal dus **niet** belangrijk.

**Mijn nieuws** (per gebruiker):

```
0,40 expliciete favoriet   0,18 geleerde interesse   0,16 recentheid
0,14 importance            0,07 regionale relevantie 0,05 uitgeversvoorkeur
× (1 − negatieve feedback)
```

Daarna diversificatie (max 3 verhalen per topic, 4 per uitgever) en per 7
gepersonaliseerde verhalen één **ontdekking**: belangrijk nieuws buiten je
interesses, zodat er geen filterbubbel ontstaat.

Gedrag vervalt met een halfwaardetijd van 10 dagen; expliciete favorieten wegen
per definitie zwaarder dan afgeleide interesses.

---

## Uitlegbare personalisatie

Elk aanbevolen verhaal draagt redenen mee ("Omdat je 'Friesland' volgt",
"Omdat je vaker nieuws over 'Technologie' leest", "Groot nieuws op dit moment",
"Buiten je interesses, maar belangrijk nieuws"). De knop **✨ Waarom zie ik dit?**
opent een paneel met alle redenen plus directe controles:
*Meer zoals dit*, *Minder zoals dit*, *Verberg*, en een link naar het
privacyscherm waar je afzonderlijke geleerde interesses kunt laten vergeten of
alles kunt wissen.

Er worden uitsluitend topic-, uitgever- en verhaal-id's opgeslagen. Er worden
geen gevoelige persoonskenmerken afgeleid en niets verlaat de machine.

---

## API

| Endpoint | Beschrijving |
|----------|--------------|
| `GET /api/health` | Status, aantallen, laatste run |
| `GET /api/meta` | Categorieën, uitgevers, bronnen, gedegradeerde feeds |
| `POST /api/refresh` | Handmatig ophalen + herclusteren |
| `GET /api/frontpage` | Top / Laatste / Trending / Fryslân / per categorie |
| `GET /api/stories` | Zoeken + filteren op `q, topic, publisher, category, location, hours, saved` |
| `GET /api/stories/{id}` | Verhaaldetail met tijdlijn, bronartikelen, gerelateerd |
| `GET /api/topics` | Topics met zoekopdracht en verhaaltelling |
| `GET POST DELETE /api/favourites` | Favoriete onderwerpen |
| `GET /api/mynews` | Gepersonaliseerde feed met redenen |
| `POST /api/interactions` | Gedragssignaal vastleggen |
| `GET /api/privacy`, `POST /api/privacy/{reset,forget}` | Inzage en wissen |
| `GET POST PATCH DELETE /api/sources` | Eigen bronnen beheren |

---

## Draaien

```powershell
py -3.12 -m pip install fastapi uvicorn feedparser httpx
cd nieuws
py -3.12 run.py          # http://127.0.0.1:8500
```

Via pm2 (zoals in de App Hub):

```powershell
pm2 start run.py --name nieuws --interpreter py --interpreter-args "-3.12"
```

Het nieuws wordt bij de start opgehaald en daarna elke 10 minuten ververst
(`NIEUWS_REFRESH_SECONDS`). Poort via `NIEUWS_PORT`, database via `NIEUWS_DB`.

---

## Gemaakte aannames

1. **Eén lokale gebruiker** (`user_id='local'`). Het schema ondersteunt meer
   gebruikers; er is bewust geen login toegevoegd omdat de app lokaal draait.
2. **Alleen feed-metadata, geen volledige artikelteksten.** Samenvattingen komen
   uit de door uitgevers zelf gepubliceerde beschrijvingen — dat respecteert hun
   voorwaarden en is genoeg voor clustering en vergelijking.
3. **Extractieve samenvattingen**, geen LLM. Daardoor is de app volledig offline
   reproduceerbaar en kan er geen feit worden gehallucineerd.
4. **RTL via `rtlnieuws.nl/rss.xml`.** De gevraagde `rtl.nl`-ingang loopt door een
   consent-gate; die wordt niet omzeild maar vervangen door de officiële feed van
   dezelfde redactie.
5. **Clustervenster van 60 uur.** Lange lopende dossiers vormen een reeks
   dagverhalen in plaats van één onhandelbaar megaverhaal.
6. **Drempels zijn conservatief afgesteld**: een onterechte samenvoeging schaadt
   het vertrouwen meer dan een gemiste.
