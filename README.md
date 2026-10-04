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
| 1 | Bron-ingestie | `app/ingest/` | Adapters per mechanisme (`rss.py`, `html.py`) achter één contract (`base.py`); `pipeline.py` draait alle bronnen parallel en kan een deelverzameling verversen |
| 2 | Normalisatie | `app/normalise.py` | `RawItem` → gemeenschappelijk Article-model + idempotente upsert |
| 3 | Story-clustering | `app/cluster.py` | TF-IDF-cosinus + entiteit-overlap + categorie + tijd → stabiele story-ids |
| 4 | Topic/entiteit-extractie | `app/topics.py` | Nederlandse tokenisatie, entiteiten, curated topics, geo-scope, categorie-mapping |
| 5 | Story-synthese | `app/summarise.py` | Neutrale kop, extractieve samenvatting, tegenstrijdigheden |
| 6 | Ranking | `app/ranking.py` | Twee gescheiden modellen: voorpagina vs. Mijn nieuws |
| 7 | Voorkeuren | `app/personalise.py` | Favorieten, instellingen |
| 8 | Gedragssignalen | `app/personalise.py` | Interacties met tijdsverval |
| 9 | Personalisatie | `app/personalise.py` | Score, uitleg, diversiteit, serendipiteit |
| 10 | Presentatie | `app/api.py` + `web/` | JSON-API en responsieve SPA |

### Verversstrategie — topverhalen eerst

Niet elke feed is even urgent. Een verhaal dat nú op de voorpagina staat is het
meest waarschijnlijk het verhaal dat wordt gecorrigeerd, uitgebreid of ingehaald.
De app gebruikt daarom twee verversrondes:

| Ronde | Interval | Bereik |
|-------|----------|--------|
| **Prioriteit** | elke 2 min (`NIEUWS_PRIORITY_SECONDS`) | alleen de feeds achter de huidige topverhalen — doorgaans 12 van de 26 |
| **Volledig** | elke 10 min (`NIEUWS_REFRESH_SECONDS`) | alle ingeschakelde feeds, zodat ook rustige secties bijblijven |

`priority_source_ids()` bepaalt de selectie: het neemt de top‑N verhalen op
`frontpage_score`, zoekt op welke feeds die artikelen leverden, en voegt per
betrokken uitgever ook de algemene feed toe — vervolgberichtgeving over een
lopend verhaal verschijnt daar meestal eerder dan in de smalle sectiefeed.

Clustering en scoring draaien **altijd** over het volledige venster van 60 uur,
ook na een gedeeltelijke fetch. Een prioriteitsronde kan de voorpagina dus nooit
inconsistent achterlaten.

De ronde is handmatig te forceren:

```
POST /api/refresh?scope=priority     # alleen topverhaal-feeds
POST /api/refresh?scope=full         # alles
```

In de UI toont de kop rechtsboven **"Top bijgewerkt …"**; de tooltip vermeldt
beide intervallen en hoeveel prioriteitsbronnen er nu zijn. Zolang de voorpagina
open staat vraagt de client zelf elke 2 minuten een prioriteitsronde aan en
hertekent alleen wanneer er daadwerkelijk iets veranderd is.

**Herziene artikelen.** Veel uitgevers hergebruiken de oorspronkelijke `pubDate`
ook als de tekst wijzigt. Bij een gewijzigde content-hash gebruikt `upsert()`
daarom het detectiemoment als `updated_at`, anders zou een echte herziening nooit
in *Laatste nieuws* belanden of het verhaal als "ontwikkelt zich" markeren.

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
| `GET /api/meta` | Categorieën, uitgevers, bronnen, prioriteitsvlag per bron, gedegradeerde feeds |
| `POST /api/refresh?scope=full\|priority` | Handmatig ophalen + herclusteren |
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
py -3.12 -m pip install -r requirements.txt
cd nieuws
py -3.12 run.py          # http://127.0.0.1:8500
```

Als achtergronddienst via pm2 — `pythonw.exe` zorgt dat er **geen consolevenster**
verschijnt:

```powershell
pm2 start ecosystem.config.js
pm2 save
```

Omdat er onder `pythonw.exe` geen console is, schrijft `run.py` altijd een
roterend logbestand naar `data/nieuws.log` (plus `data/pm2-*.log` van pm2 zelf).

### Instellingen

| Variabele | Standaard | Betekenis |
|-----------|-----------|-----------|
| `NIEUWS_PORT` | `8500` | HTTP-poort |
| `NIEUWS_DB` | `data/nieuws.db` | Pad naar de database |
| `NIEUWS_REFRESH_SECONDS` | `600` | Interval volledige ronde |
| `NIEUWS_PRIORITY_SECONDS` | `120` | Interval prioriteitsronde (topverhalen) |
| `NIEUWS_PRIORITY_TOP_N` | `24` | Hoeveel topverhalen de prioriteitsselectie bepalen |
| `NIEUWS_PRIORITY_MAX_SOURCES` | `12` | Maximum aantal feeds per prioriteitsronde |

### Onderhoudsscripts

```powershell
py -3.12 tools_rebuild.py              # taxonomie opnieuw toepassen + verhalen herbouwen
py -3.12 tools_profile.py priority     # tijdsverdeling van een ronde meten
py -3.12 tools_remove_publisher.py id  # uitgever + artikelen verwijderen
```

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
