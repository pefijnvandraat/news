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
| Tweakers | `tweakers.net/feeds/nieuws.xml` (alleen nieuws; `mixed.xml` bevat ook reviews) |
| Bright | `bright.nl/rss` |

Als een bron wegvalt, degradeert alleen die bron. De statusbalk bovenin en
**Bronnen beheren** tonen precies welke feed faalde en waarom.

### Over het consent-scherm

Tweakers en Bright tonen bij een gewoon bezoek een toestemmingsscherm, maar hun
**RSS-endpoints staan daarbuiten** en zijn direct bereikbaar. Dat geldt ook voor
RTL: `rtl.nl/rss/*` loopt door de gate, `rtlnieuws.nl/rss.xml` niet. Er wordt
dus niets omzeild — er is simpelweg een officiële, open feed naast de
afgeschermde website. Kom je ooit een bron tegen die wél alleen achter een gate
zit, dan rapporteert de adapter die als gedegradeerd in plaats van hem te
forceren.

### Standaardcategorie per uitgever

Een specialistische titel labelt zijn eigen feed vaak generiek: Bright zet
vrijwel alles onder "Nieuws", Tweakers gebruikt een eigen hiërarchie
(`Nieuws / IT Pro / Politiek en recht`). Een uitgever heeft daarom een
`default_category` die gebruikt wordt zodra de feed niets bruikbaars zegt —
`tech` voor Tweakers en Bright, `fryslan` voor Omrop Fryslân.

Twee bewuste keuzes in de Tweakers-mapping:

* **`Politiek en recht` → tech**, niet politiek. Het gaat over techwetgeving en
  arrestaties van hackers; wie op "Politiek" klikt verwacht het kabinet.
* **`Gaming` → tech**, niet entertainment. Het is hardware- en industrienieuws
  ("AMD-driver verwijst naar mogelijke gpu PS6"). Zo blijft Entertainment
  voorbehouden aan AD Show en NU Achterklap.

Let op: de categorie van een *verhaal* is een meerderheidsstem over zijn
artikelen. Een Tweakers-artikel is altijd tech, maar het verhaal waarin het
belandt kan anders worden gelabeld — de Paramount/Warner-fusie staat onder
entertainment omdat NU.nl en AD het zo brengen.

### Zelf een bron toevoegen: eerst controleren, dan bevestigen

Je hoeft geen feed-URL te kennen. Plak op **Bronnen** gewoon het adres van de
site (`tweakers.net`, `nrc.nl`) en druk op **Controleren**. `app/ingest/discover.py`
haalt die URL op en komt met één van vier uitkomsten:

| Uitkomst | Wat er gebeurt |
|----------|----------------|
| `feed` | De URL is zelf een geldige feed. Direct toe te voegen. |
| `gated` | De pagina zit achter een toestemmingsscherm. Dat wordt **niet** omzeild; in plaats daarvan worden open feeds van dezelfde uitgever gezocht en ter bevestiging getoond. |
| `html` | Een gewone webpagina. De `<link rel="alternate">`-tags en bekende feedpaden worden afgezocht. |
| `error` | Onbereikbaar of 404. Je krijgt de echte foutmelding, geen stille mislukking. |

Elke kandidaat wordt **eerst opgehaald en geparsed** voordat hij wordt
voorgesteld. Je ziet de feedtitel, het aantal artikelen, of er afbeeldingen in
zitten en de nieuwste kop — genoeg om te zien of het de juiste feed is. Pas als
je op **Toevoegen** klikt wordt hij opgeslagen.

Twee dingen waar de zoekroutine rekening mee houdt:

* **De gate verwijst door naar een ander domein.** `tweakers.net` stuurt je naar
  `myprivacy.dpgmedia.nl`. Er wordt daarom altijd doorgezocht op de
  *oorspronkelijke* host, niet op het doel van de redirect — anders zoek je
  feeds bij de consent-leverancier.
* **Feeds staan vaak op een zusterhost.** `nos.nl` → `feeds.nos.nl`,
  `rtl.nl` → `rtlnieuws.nl`. `_host_variants()` probeert `feeds.X`, `nieuws.X`,
  `<merk>nieuws.<tld>` en www-varianten.

`POST /api/sources` weigert een URL die niet geverifieerd is (HTTP 422, mét de
gevonden alternatieven). Dat voorkomt dat een gate-pagina of een 404 in de
bronnenlijst belandt en bij elke verversing een fout meldt. De controle duurt
ongeveer 10 seconden omdat er tot 44 kandidaat-URL's parallel worden getest.

Verwijder je de laatste feed van een zelf toegevoegde uitgever, dan verdwijnt
die uitgever mee — anders blijft er een lege uitgever in de lijst staan.
`tools_orphans.py` spoort zulke wezen op (`--purge` ruimt ze op).

---

## Datamodel (SQLite, `data/nieuws.db`)

```
publishers ──< sources ──< articles >── stories ──< story_topics >── topics
                                                        │
user_preferences      user_interactions      recommendations
```

| Tabel | Kern |
|-------|------|
| `publishers` | id, naam, homepage, regio, gewicht, kleur, `default_category`, user_added |
| `sources` | feed-url, kind (`rss`/`html`), categorie-hint, gezondheidsstatus |
| `articles` | kop, uitgever, originele URL, canonical URL + hash (dedupe), publicatie-/updatetijd, categorie, afbeelding, beschrijving, taal, geo-scope + plaatsen, topics, entiteiten, tokens, revisie, story_id |
| `stories` | neutrale kop + bronuitgever, gegenereerde samenvatting, categorie, geo-scope, aantal artikelen/uitgevers, eerste/laatste tijd, centroid, entiteiten, tegenstrijdigheden, is_updating, importance/frontpage/trending-scores |
| `topics` | slug, label, soort (topic/category/person/org/place) |
| `story_topics` | story ↔ topic met gewicht |
| `user_preferences` | expliciete favorieten + instellingen |
| `user_interactions` | open, open_article, save, hide, unread, follow, more, less, feedback_clear (met tijdstempel) |
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

## Weergave: kaarten of lijst

Elke nieuwspagina (Voorpagina, Mijn nieuws, zoeken, onderwerp, categorie,
bewaard) heeft linksboven een schakelaar:

* **Kaarten** — de standaard: grote kaarten mét afbeelding, samenvatting en
  bronbadges.
* **Lijst** — een compacte tabel **zonder afbeeldingen**, waarin veel meer
  verhalen tegelijk op het scherm passen.

De lijstweergave heeft de kolommen **Sectie** (alleen op de voorpagina),
**Titel**, **Uitgevers**, **Status**, **Categorie**, **Bijgewerkt** en
**Waarom** (alleen bij Mijn nieuws), plus dezelfde actieknoppen als op de
kaarten: bewaren, meer zo, minder zo, verbergen. Klik op een kolomkop om te
sorteren; de tweede koprij bevat per kolom een filter.

Twee bewuste keuzes:

* **Standaardvolgorde = de rangschikking van de pagina zelf** — nieuwswaarde op
  de voorpagina, relevantie bij Mijn nieuws. Pas als je een kolomkop aanklikt
  neemt jouw sortering het over. De voettekst onder de tabel vermeldt altijd
  welke volgorde actief is.
* **Sortering en filters resetten bij paginawissel.** Anders zou een filter van
  de ene pagina stilzwijgend rijen verbergen op de volgende, en zou een oude
  sortering de ranking van de nieuwe pagina overschrijven.

De keuze kaarten/lijst wordt onthouden in `localStorage`. Op smalle schermen
verbergt de tabel achtereenvolgens de minst essentiële kolommen, zodat titel,
tijd en acties altijd zichtbaar blijven.

### Breedte: schaalt mee, behalve de lopende tekst

De schil schaalt met het venster (`min(2100px, 95vw)`) in plaats van te stoppen
bij een vaste kolombreedte, dus een breed scherm levert méér kolommen op en
geen lege marges. Kaarten blijven daarbij ~300px breed; alleen hun aantal
groeit (1 → 3 → 4 → 5 → 6 kolommen).

**Lopende tekst doet daar bewust niet aan mee.** Een regel van 1700px is
ongeveer 250 tekens; het oog verliest dan het begin van de volgende regel.
Daarom zijn de tekstblokken apart begrensd op `--prose` (70ch ≈ 77 tekens) en
de brontekst op 68ch.

De verhaalpagina splitst op ≥1200px in twee kolommen: onderwerpen en de
publicatietijdlijn links, de artikelen van de uitgevers rechts (meescrollend).
Dat is precies de vergelijking waarvoor die pagina bestaat, en het is wat de
extra breedte daar verdient in plaats van een half leeg scherm.

In de lijstweergave verschijnt vanaf 1500px een samenvattingsregel onder de
titel: anders slokt de titelkolom alle overgebleven ruimte op en ontstaat er
een gat vóór de volgende kolom.

## Gelezen nieuwsartikelen

De Voorpagina en Mijn nieuws tonen standaard alleen wat **nieuw voor jou** is.
Zodra je een verhaal opent (of doorklikt naar het artikel bij de uitgever),
verhuist het naar de pagina **Gelezen** — met een melding op de voorpagina
hoeveel verhalen er verborgen zijn.

* **↩ Ongelezen** per verhaal, of **Alles als ongelezen markeren**, zet ze terug.
* De schakelaar **"Verberg gelezen verhalen"** op de Gelezen-pagina zet het
  hele gedrag uit; dan werkt de voorpagina weer als vanouds.
* De lijstweergave krijgt een extra kolom **Gelezen** (wanneer je het las),
  sorteerbaar net als de andere kolommen.

**Bijgewerkte verhalen komen terug.** Een verhaal blijft alleen verborgen
zolang het hetzelfde verhaal is dat je gelezen hebt. Komt er daarna nieuwe
berichtgeving bij, dan is het weer nieuws voor jou en verschijnt het opnieuw op
de voorpagina met het label **Nieuw sinds gelezen**. Zonder die regel zou een
lopend dossier na één klik voorgoed onzichtbaar blijven, juist op het moment
dat er iets gebeurt.

Zoeken, categorie- en onderwerppagina's filteren **niet** op gelezen: daar zoek
je bewust iets op en wil je alles zien.

### Dit volgen

De knop **🔔 Dit volgen** houdt een verhaal op de Voorpagina en in Mijn nieuws
staan, óók nadat je het gelezen hebt. Handig voor een lopend dossier dat je wilt
blijven volgen. Gevolgde verhalen krijgen een oranje 🔔-badge; de privacypagina
toont een overzicht met per verhaal een knop om te stoppen.

### Toekomstige publicatiedata

Uitgevers dateren een artikel soms in de toekomst — AD doet dat bij
liveblog-items. Onbehandeld zou zo'n artikel *Laatste nieuws* permanent
aanvoeren, nooit als gelezen kunnen gelden (de updatetijd is altijd nieuwer dan
het moment waarop je het las) en onzinnige "x min geleden" tonen. Bij ingest
wordt een datum die meer dan 10 minuten in de toekomst ligt daarom afgeklemd op
het moment waarop we het artikel voor het eerst zagen.

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

### Duim omhoog/omlaag is één toestand, geen optelsom

*Meer zo* en *Minder zo* vormen samen één drieweg-toestand per verhaal:
omhoog, omlaag, of geen van beide.

* Klik je op een actieve knop, dan **trek je je oordeel in**.
* Klik je op de andere knop, dan **vervangt** die het eerdere oordeel.
* Beide tegelijk actief is onmogelijk.

Het gebeurtenislog blijft append-only (`more`, `less`, `feedback_clear`), maar
de *huidige* toestand is het laatste oordeel per verhaal. Dat is essentieel:
zou de ranking alle gebeurtenissen optellen, dan zouden een `more` (+3) en een
latere `less` (−3) elkaar opheffen tot neutraal — precies het tegenovergestelde
van wat die tweede klik vroeg.

Het log wordt op `rowid` afgespeeld, niet op `created_at`: tijdstempels hebben
secondenresolutie en id's zijn willekeurig, dus twee klikken binnen dezelfde
seconde zouden anders in willekeurige volgorde worden herspeeld — juist het
geval dat ontstaat wanneer je jezelf meteen corrigeert.

De knoppen worden gerenderd vanuit het serverantwoord (veld `feedback` per
verhaal), dus na herladen zie je nog steeds wat je gekozen hebt, en elke kopie
van een verhaal op het scherm (kaart, tabelrij, Waarom-paneel) toont dezelfde
toestand.

### Verborgen verhalen terugzetten

*Verberg* filtert een verhaal uit al je feeds. Omdat de rij daarmee verdwijnt,
is er geen knop meer om op te klikken — daarom staat op de privacypagina een
sectie **Verborgen verhalen** met per verhaal een *Weer tonen*-knop en een
*Alles weer tonen*-knop.

Een hide-record kan het verhaal overleven: als de artikelen te oud worden,
verdwijnt het verhaal uit het corpus. Die worden geteld maar niet getoond,
want er valt niets meer terug te zetten.

### Aan/uit-toestanden worden afgespeeld, niet afgetrokken

`hide`/`unhide` en `save`/`unsave` gebruiken dezelfde laatste-wint-logica als
de duimen. Een setverschil (`hide - unhide`) leek simpeler maar is fout: na
*verbergen → terugzetten → verbergen* staat het id in beide verzamelingen en
leest het resultaat als "niet verborgen". `tools_test_toggles.py` dekt dat
geval expliciet af.

Er worden uitsluitend topic-, uitgever- en verhaal-id's opgeslagen. Er worden
geen gevoelige persoonskenmerken afgeleid en niets verlaat de machine.

---

## API

| Endpoint | Beschrijving |
|----------|--------------|
| `GET /api/health` | Status, aantallen, laatste run |
| `GET /api/meta` | Categorieën, uitgevers, bronnen, prioriteitsvlag per bron, gedegradeerde feeds |
| `POST /api/refresh?scope=full\|priority` | Handmatig ophalen + herclusteren |
| `GET /api/frontpage` | Top / Laatste / Trending / Fryslân / per categorie (`include_read=true` toont ook gelezen verhalen) |
| `GET /api/read`, `POST /api/read/unread` | Gelezen artikelen en ze terugzetten |
| `GET /api/followed` | Verhalen die je volgt |
| `POST /api/settings/hide-read` | Gelezen verhalen wel/niet verbergen |
| `GET /api/stories` | Zoeken + filteren op `q, topic, publisher, category, location, hours, saved` |
| `GET /api/stories/{id}` | Verhaaldetail met tijdlijn, bronartikelen, gerelateerd |
| `GET /api/topics` | Topics met zoekopdracht en verhaaltelling |
| `GET POST DELETE /api/favourites` | Favoriete onderwerpen |
| `GET /api/mynews` | Gepersonaliseerde feed met redenen |
| `POST /api/interactions` | Gedragssignaal vastleggen |
| `GET /api/privacy`, `POST /api/privacy/{reset,forget,unhide}` | Inzage, wissen en verborgen verhalen terugzetten |
| `GET POST PATCH DELETE /api/sources` | Eigen bronnen beheren |
| `POST /api/sources/probe` | Een URL controleren: feed, consent-gate, gewone pagina of fout — met geverifieerde feed-kandidaten |

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
py -3.12 tools_badge_audit.py          # welke verhalen 'Ontwikkelt zich' tonen en waarom
py -3.12 tools_test_feedback.py        # duim omhoog/omlaag-toestandsmachine testen
py -3.12 tools_test_toggles.py         # verbergen/bewaren-toggles testen
py -3.12 tools_test_read.py            # gelezen/ongelezen-logica testen
py -3.12 tools_test_follow.py          # volgen-logica testen
py -3.12 tools_future_dates.py         # artikelen met toekomstige datum tonen
py -3.12 tools_check_publisher.py id   # categorisering van één uitgever bekijken
py -3.12 tools_profile_inspect.py      # favorieten, feedback en signalen bekijken
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
