"""Topic & entity extraction, tokenisation and geographic scoping (Dutch).

Pure functions over text; no database or network access, so clustering and
ranking can rely on it deterministically.
"""
from __future__ import annotations

import re

from .util import deaccent, slugify

# ---------------------------------------------------------------------------
# Dutch stopwords (plus the news-boilerplate words that pollute similarity)
# ---------------------------------------------------------------------------
STOPWORDS: set[str] = set("""
de het een en van in op te dat die is was zijn voor met als aan er maar om door
over ze niet naar bij ook tot je uit of hij heeft heeft nog dan zou wat mijn men
dit zo door moet ben hem kan haar nu wel geen hun deze hebben werd wordt worden
men onder tegen na reeds zich alle veel meer dus toch want zal moeten kunnen kon
ik we wij jij u jullie hen hen hen daar hier waar wie welke doch omdat terwijl
zonder tussen sinds volgens vanaf binnen buiten tijdens ondanks via per vanwege
al zelfs weer nooit altijd soms vaak misschien echter bovendien verder daarna
jaar jaren dag dagen week weken maand maanden uur uren keer miljoen miljard
procent euro nieuw nieuwe nieuwste grote groot klein kleine goed goede beter
beste eerste tweede derde laatste volgende afgelopen komende vorige andere
mensen man vrouw kind kinderen maken maakt gemaakt doen doet gedaan gaan gaat
komen komt kwam zeggen zegt zei zien ziet gezien krijgen krijgt kreeg staan
staat stond willen wil wilde laten laat liet geven geeft gaf vinden vindt vond
nederland nederlandse nederlander volgens bron foto video update live lees
artikel nieuws bekijk waarom hoe wat wanneer welk welke dit die deze zulke
the of and to in a for on is with that it as at by be from are this an
""".split())

MONTHS = {"januari", "februari", "maart", "april", "mei", "juni", "juli",
          "augustus", "september", "oktober", "november", "december",
          "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag",
          "zaterdag", "zondag", "vandaag", "gisteren", "morgen", "vanavond",
          "vanochtend", "vanmiddag", "vannacht"}

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:[-'][A-Za-zÀ-ÿ0-9]+)*")

# Capitalised demonyms and nationality adjectives are not entities; without this
# filter "Amerikaanse", "Russische" etc. pollute the topic list.
DEMONYMS = {
    "amerikaans", "amerikaanse", "russisch", "russische", "duits", "duitse",
    "frans", "franse", "spaans", "spaanse", "italiaans", "italiaanse",
    "belgisch", "belgische", "britse", "brits", "engelse", "engels",
    "chinees", "chinese", "japanse", "japans", "israelisch", "israelische",
    "palestijnse", "oekraiens", "oekraiense", "turkse", "turks", "poolse",
    "zweedse", "deense", "noorse", "griekse", "friese", "fryske", "hollandse",
    "europees", "europese", "afrikaanse", "aziatische", "westerse", "oosterse",
    "nederlands", "nederlandse", "belg", "duitser", "fries", "friezen",
}
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Þ0-9'\"\u201c])")
_PROPER_SEQ = re.compile(
    r"\b([A-ZÀ-Þ][\wÀ-ÿ'’-]+(?:\s+(?:van|van der|van den|de|der|den|op|'t|te)\s+)?"
    r"(?:\s*[A-ZÀ-Þ][\wÀ-ÿ'’-]+){0,3})"
)

# ---------------------------------------------------------------------------
# Gazetteers
# ---------------------------------------------------------------------------
FRISIAN_PLACES = {
    "friesland", "fryslan", "fryslân", "leeuwarden", "ljouwert", "sneek", "snits",
    "drachten", "heerenveen", "it hearrenfean", "harlingen", "harns", "makkum",
    "bolsward", "boalsert", "dokkum", "franeker", "frjentsjer", "joure", "joure",
    "lemmer", "wolvega", "burgum", "buitenpost", "gorredijk", "surhuisterveen",
    "stiens", "grou", "workum", "warkum", "hindeloopen", "sloten", "stavoren",
    "ijlst", "terschelling", "vlieland", "ameland", "schiermonnikoog",
    "waddeneilanden", "afsluitdijk", "sc heerenveen", "sc cambuur", "cambuur",
    "elfstedentocht", "noardeast-fryslan", "sudwest-fryslan", "súdwest-fryslân",
    "waadhoeke", "tytsjerksteradiel", "smallingerland", "weststellingwerf",
    "oostellingwerf", "achtkarspelen", "dantumadiel", "opsterland",
}

NL_PLACES = {
    "amsterdam", "rotterdam", "den haag", "utrecht", "eindhoven", "groningen",
    "tilburg", "almere", "breda", "nijmegen", "arnhem", "haarlem", "enschede",
    "apeldoorn", "zwolle", "maastricht", "leiden", "dordrecht", "zoetermeer",
    "venlo", "assen", "emmen", "middelburg", "lelystad", "schiphol", "zeeland",
    "limburg", "brabant", "gelderland", "overijssel", "drenthe", "flevoland",
    "groninger", "randstad", "wadden",
}

# Curated topics that users commonly want to follow. Dynamic entities are added
# on top of this, so the Favorites page is never limited to a fixed list.
CURATED_TOPICS: list[tuple[str, list[str], str]] = [
    ("AI", ["ai", "kunstmatige intelligentie", "artificial intelligence", "chatgpt",
            "openai", "copilot", "llm", "chatbot", "generatieve ai"], "topic"),
    ("Microsoft", ["microsoft", "azure", "windows", "xbox", "copilot"], "org"),
    ("Technologie", ["tech", "technologie", "software", "hardware", "internet",
                     "apps", "smartphone", "cybersecurity", "hack", "chip"], "topic"),
    ("Nederlandse politiek", ["kabinet", "tweede kamer", "eerste kamer", "kamerlid",
                              "minister", "staatssecretaris", "coalitie", "formatie",
                              "pvv", "vvd", "groenlinks-pvda", "d66", "cda", "bbb",
                              "sp", "denk", "forum voor democratie", "nsc"], "topic"),
    ("Economie", ["economie", "inflatie", "beurs", "aex", "rente", "koopkracht",
                  "werkloosheid", "cbs", "dnb", "huizenmarkt", "hypotheek"], "topic"),
    ("Voetbal", ["voetbal", "eredivisie", "knvb", "champions league", "oranje",
                 "ajax", "feyenoord", "psv", "az", "nations league"], "topic"),
    ("SC Heerenveen", ["sc heerenveen", "heerenveen", "abe lenstra"], "org"),
    ("Friesland", sorted(FRISIAN_PLACES), "place"),
    ("Makkum", ["makkum"], "place"),
    ("Europa", ["europa", "europese unie", "eu", "brussel", "europees parlement",
                "europese commissie", "navo"], "place"),
    ("Formule 1", ["formule 1", "f1", "verstappen", "grand prix", "red bull racing",
                   "ferrari", "mclaren"], "topic"),
    ("Klimaat", ["klimaat", "co2", "stikstof", "duurzaam", "energie", "windmolen",
                 "zonnepanelen", "opwarming"], "topic"),
    ("Gezondheid", ["zorg", "ziekenhuis", "huisarts", "gezondheid", "virus",
                    "medicijn", "rivm", "ggd"], "topic"),
    ("Oekra\u00efne", ["oekraine", "oekraïne", "zelensky", "kyiv", "kiev", "rusland",
                       "poetin"], "place"),
    ("Midden-Oosten", ["israel", "israël", "gaza", "hamas", "libanon", "iran",
                       "palestijn"], "place"),
    ("Verenigde Staten", ["verenigde staten", "vs", "amerika", "trump", "washington",
                          "witte huis", "biden"], "place"),
    ("Onderwijs", ["onderwijs", "school", "leraar", "universiteit", "student",
                   "hogeschool"], "topic"),
    ("Justitie", ["rechtbank", "openbaar ministerie", "officier van justitie",
                  "verdachte", "politie", "hoger beroep", "veroordeeld",
                  "celstraf", "aangifte", "aangehouden"], "topic"),
    ("Verkeer", ["verkeer", "file", "a7", "snelweg", "ns", "trein", "prorail",
                 "ongeluk"], "topic"),
    ("Wonen", ["woningmarkt", "huurwoning", "woningnood", "huur", "sociale huur"], "topic"),
]

_CURATED_INDEX: list[tuple[str, str, str, list[str]]] = [
    (slugify(label), label, kind, [deaccent(k.lower()) for k in keys])
    for label, keys, kind in CURATED_TOPICS
]


def tokenize(text: str) -> list[str]:
    """Lowercase, de-accented content tokens with stopwords and noise removed."""
    out: list[str] = []
    for m in _WORD_RE.finditer(text or ""):
        w = deaccent(m.group(0).lower())
        if len(w) < 3 or w in STOPWORDS or w in MONTHS:
            continue
        if w.isdigit() and len(w) < 4:
            continue
        out.append(w)
    return out


def sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) > 25]


def extract_entities(title: str, description: str) -> list[dict]:
    """Capitalised multi-word sequences = candidate named entities.

    Deliberately conservative: it is a signal for clustering, not a claim about
    the world, so false positives are cheap and false negatives are tolerable.
    """
    found: dict[str, dict] = {}
    for text, boost in ((title or "", 2.0), (description or "", 1.0)):
        for sent in re.split(r"(?<=[.!?])\s+", text):
            body = sent.strip()
            if not body:
                continue
            for m in _PROPER_SEQ.finditer(body):
                raw = m.group(1).strip(" .,;:'\u2019")
                if not raw:
                    continue
                if m.start() == 0:
                    # Sentence-initial capitals are grammar, not evidence of a
                    # proper noun. Record the remainder at full weight
                    # ("Toestand Christa Pike" -> "Christa Pike") and keep the
                    # original as a weaker candidate in case it was genuine.
                    candidates = [(_peel_sentence_start(raw), 1.0), (raw, 0.4)]
                else:
                    candidates = [(raw, 1.0)]
                for name, factor in candidates:
                    if not name or len(name) < 3:
                        continue
                    low = deaccent(name.lower())
                    if low in STOPWORDS or low in MONTHS or low in DEMONYMS:
                        continue
                    if all(part in STOPWORDS or part in MONTHS or part in DEMONYMS
                           for part in low.split()):
                        continue
                    ent = found.setdefault(
                        low, {"name": name, "kind": _entity_kind(low), "weight": 0.0}
                    )
                    ent["weight"] += boost * factor
                    if len(name) > len(ent["name"]):
                        ent["name"] = name
    ranked = sorted(found.values(), key=lambda e: -e["weight"])
    return ranked[:12]


def _peel_sentence_start(name: str) -> str:
    """Drop the leading token (capitalised only by sentence position) plus any
    connecting words, keeping the proper-noun tail."""
    parts = name.split()[1:]
    while parts and (not parts[0][:1].isupper()
                     or deaccent(parts[0].lower()) in STOPWORDS):
        parts = parts[1:]
    return " ".join(parts)


def _entity_kind(low: str) -> str:
    if low in FRISIAN_PLACES or low in NL_PLACES:
        return "place"
    if any(low.endswith(suf) for suf in (" bv", " nv", " inc", " corp", " ag")):
        return "org"
    return "entity"


def detect_topics(title: str, description: str, category: str | None,
                  entities: list[dict]) -> list[str]:
    """Return topic slugs: curated matches + category + salient entities."""
    hay = deaccent(f"{title} {description}".lower())
    slugs: list[str] = []
    for slug, _label, _kind, keys in _CURATED_INDEX:
        for k in keys:
            if _contains_term(hay, k):
                slugs.append(slug)
                break
    if category:
        slugs.append(slugify(category))
    for ent in entities[:6]:
        if ent["weight"] >= 2.5 and len(ent["name"]) >= 4:
            slugs.append(slugify(ent["name"]))
    seen: set[str] = set()
    return [s for s in slugs if s and not (s in seen or seen.add(s))]


def _contains_term(haystack: str, term: str) -> bool:
    if " " in term:
        return term in haystack
    return re.search(rf"\b{re.escape(term)}\b", haystack) is not None


def topic_label(slug: str) -> str:
    for s, label, _kind, _k in _CURATED_INDEX:
        if s == slug:
            return label
    return slug.replace("-", " ").title()


def topic_kind(slug: str) -> str:
    for s, _label, kind, _k in _CURATED_INDEX:
        if s == slug:
            return kind
    return "topic"


def geo_scope(title: str, description: str, publisher_id: str) -> tuple[str, list[str]]:
    """Return (scope, places). Omrop Frysl\u00e2n defaults to the regional scope."""
    hay = deaccent(f"{title} {description}".lower())
    places = sorted({p for p in FRISIAN_PLACES if _contains_term(hay, deaccent(p))})
    nl_hits = sorted({p for p in NL_PLACES if _contains_term(hay, deaccent(p))})
    if places or publisher_id == "omrop":
        return "fryslan", places or (["fryslan"] if publisher_id == "omrop" else [])
    if nl_hits:
        return "nl", nl_hits
    return "world", []


def map_category(raw: str | None, url: str = "", hint: str | None = None,
                 publisher_id: str | None = None, scope: str | None = None,
                 topic_slugs: list[str] | None = None) -> str:
    """Normalise a publisher category onto our taxonomy.

    Priority: specific URL segment > specific feed category > feed hint >
    detected topics > geography. Generic labels such as 'nieuws' or 'artikel'
    never win, because publishers put the real section *after* them
    (e.g. rtl.nl/nieuws/buitenland/...).
    """
    from .config import CATEGORY_ALIASES, GENERIC_CATEGORY_TOKENS

    def scan(text: str) -> str | None:
        for part in re.split(r"[/_?&=.]+", (text or "").lower()):
            part = slugify(part)
            if not part or part in GENERIC_CATEGORY_TOKENS:
                continue
            if part in CATEGORY_ALIASES:
                return CATEGORY_ALIASES[part]
        return None

    for candidate in (url, raw, hint):
        found = scan(candidate or "")
        if found:
            return found
    if hint in CATEGORY_ALIASES.values():
        return hint
    # A regional broadcaster's own newsroom feed is regional news by default.
    if publisher_id == "omrop":
        return "fryslan"
    for slug in topic_slugs or []:
        if slug in TOPIC_TO_CATEGORY:
            return TOPIC_TO_CATEGORY[slug]
    if scope == "fryslan":
        return "fryslan"
    return "overig"


# Fallback for publishers that expose neither a feed category nor a readable
# URL path (NOS shortlinks, for example): infer from the detected topics.
TOPIC_TO_CATEGORY: dict[str, str] = {
    "voetbal": "sport", "formule-1": "sport", "sc-heerenveen": "sport",
    "nederlandse-politiek": "politiek", "justitie": "politiek",
    "economie": "economie", "wonen": "economie",
    "ai": "tech", "microsoft": "tech", "technologie": "tech",
    "oekraine": "buitenland", "midden-oosten": "buitenland",
    "verenigde-staten": "buitenland", "europa": "buitenland",
    "friesland": "fryslan", "makkum": "fryslan",
    "klimaat": "nederland", "gezondheid": "nederland",
    "onderwijs": "nederland", "verkeer": "nederland",
}
