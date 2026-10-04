"""Checks on saved quick filters.

The point of interest is canonicalisation: two filters that mean the same thing
must compare equal, or the UI can never say which chip is active and duplicate
chips would both light up.
"""
import sys

sys.path.insert(0, ".")

from app import quickfilters as qf  # noqa: E402
from app.db import query, tx  # noqa: E402

TEST_USER = "u:testsuite@example.invalid"

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"{label:<54} -> {str(got):<34} (verwacht {want}) {'OK' if good else 'FOUT'}")


def expect_error(label, fn):
    try:
        fn()
        check(label, "geen fout", "ValueError")
    except ValueError:
        check(label, "ValueError", "ValueError")


saved = [(r["topic_slug"], r["value"], r["weight"]) for r in
         query("SELECT topic_slug, value, weight FROM user_preferences "
               "WHERE kind='quickfilter' AND user_id='u:testsuite@example.invalid' ORDER BY weight, rowid")]
with tx() as c:
    c.execute("DELETE FROM user_preferences WHERE kind='quickfilter' AND user_id='u:testsuite@example.invalid'")

try:
    # --- canonicalisation ------------------------------------------------
    check("sleutelvolgorde vast", qf.normalise("hours=24&category=tech"),
          "category=tech&hours=24")
    check("andere invoervolgorde, zelfde uitkomst",
          qf.normalise("category=tech&hours=24"), "category=tech&hours=24")
    check("onbekende sleutel weg", qf.normalise("q=ai&limit=60&zzz=1"), "q=ai")
    check("ongeldige periode weg", qf.normalise("hours=999"), "")
    check("ongeldige regio weg", qf.normalise("location=mars"), "")
    check("saved genormaliseerd", qf.normalise("saved=1"), "saved=true")
    check("saved=nee telt niet", qf.normalise("saved=nee"), "")
    check("spaties getrimd", qf.normalise("q=  ai  "), "q=ai")
    check("dict werkt ook", qf.normalise({"category": "tech", "hours": "24"}),
          "category=tech&hours=24")
    check("leeg blijft leeg", qf.normalise(""), "")

    # --- adding ----------------------------------------------------------
    check("begint leeg", qf.list_all(TEST_USER), [])
    a = qf.add(TEST_USER, "Fryslân nu", "hours=24&location=fryslan")
    check("opgeslagen in canonieke vorm", a["qs"], "location=fryslan&hours=24")
    check("slug afgeleid van naam", a["slug"], "fryslan-nu")

    expect_error("zelfde filter, andere naam geweigerd",
                 lambda: qf.add(TEST_USER, "Anders", "location=fryslan&hours=24"))
    expect_error("leeg filter geweigerd", lambda: qf.add(TEST_USER, "Niets", "limit=60"))
    expect_error("naamloos geweigerd", lambda: qf.add(TEST_USER, "", "category=tech"))
    check("geen van die pogingen is opgeslagen", len(qf.list_all(TEST_USER)), 1)

    qf.add(TEST_USER, "Fryslân nu", "category=sport")
    check("dubbele naam krijgt eigen slug",
          [f["slug"] for f in qf.list_all(TEST_USER)], ["fryslan-nu", "fryslan-nu-2"])

    # --- ordering --------------------------------------------------------
    qf.add(TEST_USER, "Derde", "category=tech")
    qf.reorder(TEST_USER, ["derde", "fryslan-nu-2", "fryslan-nu"])
    check("volgorde toegepast", [f["slug"] for f in qf.list_all(TEST_USER)],
          ["derde", "fryslan-nu-2", "fryslan-nu"])

    qf.reorder(TEST_USER, ["fryslan-nu"])
    check("gedeeltelijke lijst laat niets vallen", len(qf.list_all(TEST_USER)), 3)
    check("genoemde komt vooraan", qf.list_all(TEST_USER)[0]["slug"], "fryslan-nu")
    check("niet-genoemde houden hun onderlinge volgorde",
          [f["slug"] for f in qf.list_all(TEST_USER)], ["fryslan-nu", "derde", "fryslan-nu-2"])

    qf.reorder(TEST_USER, ["bestaatniet", "derde"])
    check("onbekende slug wordt genegeerd", len(qf.list_all(TEST_USER)), 3)
    check("en de rest blijft op volgorde", [f["slug"] for f in qf.list_all(TEST_USER)],
          ["derde", "fryslan-nu", "fryslan-nu-2"])

    # --- renaming and removing -------------------------------------------
    qf.rename(TEST_USER, "derde", "Hernoemd")
    check("hernoemd", next(f["label"] for f in qf.list_all(TEST_USER) if f["slug"] == "derde"),
          "Hernoemd")
    check("filter zelf ongewijzigd",
          next(f["qs"] for f in qf.list_all(TEST_USER) if f["slug"] == "derde"), "category=tech")
    expect_error("lege naam geweigerd", lambda: qf.rename(TEST_USER, "derde", "   "))

    try:
        qf.rename(TEST_USER, "bestaatniet", "X")
        check("onbekende slug hernoemen", "geen fout", "LookupError")
    except LookupError:
        check("onbekende slug hernoemen", "LookupError", "LookupError")

    qf.remove(TEST_USER, "derde")
    check("verwijderd", [f["slug"] for f in qf.list_all(TEST_USER)],
          ["fryslan-nu", "fryslan-nu-2"])
    qf.remove(TEST_USER, "bestaatniet")
    check("onbekend verwijderen is stil", len(qf.list_all(TEST_USER)), 2)

    # --- cap -------------------------------------------------------------
    for i in range(qf.MAX_FILTERS - 2):
        qf.add(TEST_USER, f"Test {i}", f"q=test{i}")
    check("lijst zit vol", len(qf.list_all(TEST_USER)), qf.MAX_FILTERS)
    expect_error("vol -> geweigerd", lambda: qf.add(TEST_USER, "Te veel", "q=extra"))

    # --- label length ----------------------------------------------------
    qf.remove(TEST_USER, "fryslan-nu")
    long_name = "x" * 80
    f = qf.add(TEST_USER, long_name, "q=lang")
    check("naam afgekapt", len(f["label"]), qf.MAX_LABEL)
finally:
    with tx() as c:
        c.execute("DELETE FROM user_preferences WHERE kind='quickfilter' AND user_id='u:testsuite@example.invalid'")
        for slug, value, weight in saved:
            c.execute("INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,"
                      "weight,created_at,updated_at) "
                      "VALUES(?,?,'quickfilter',?,?,?,'restored','restored')",
                      (f"qf_r{slug}", TEST_USER, slug, value, weight))

print(f"\n{ok}/{ok + fail} geslaagd")
sys.exit(1 if fail else 0)
