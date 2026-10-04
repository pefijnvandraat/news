"""Checks on the ticker watchlist and its labelling.

Network-free where possible: only the add path needs Yahoo, because add
deliberately verifies a symbol before storing it.
"""
import sys

sys.path.insert(0, ".")

from app import stocks  # noqa: E402
from app.db import query, tx  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"{label:<52} -> {str(got):<28} (verwacht {want}) {'OK' if good else 'FOUT'}")


def snapshot():
    return [r["topic_slug"] for r in
            query("SELECT topic_slug FROM user_preferences WHERE kind='stock' ORDER BY rowid")]


saved = snapshot()
with tx() as c:
    c.execute("DELETE FROM user_preferences WHERE kind='stock'")

try:
    # --- labelling -------------------------------------------------------
    check("legal suffix weg", stocks.short_label("Microsoft Corporation", "MSFT"), "Microsoft")
    check("twee suffixen weg", stocks.short_label("Heineken Holding N.V.", "HEIO.AS"), "Heineken")
    check("plc weg", stocks.short_label("Shell plc", "SHELL.AS"), "Shell")
    check("niets te trimmen", stocks.short_label("ASML", "ASML.AS"), "ASML")
    check("lege naam valt terug op symbool", stocks.short_label("", "AAPL"), "AAPL")
    check("lange naam afgekapt",
          len(stocks.short_label("T-Rex 2X Long Microsoft Daily Target ETF", "MSFX")) <= 22, True)

    # --- watchlist -------------------------------------------------------
    check("begint leeg", stocks.watchlist(), [])
    stocks.add("HEIA.AS", "Heineken N.V.")
    check("na toevoegen", [w["symbol"] for w in stocks.watchlist()], ["HEIA.AS"])

    check("dubbel toevoegen meldt already", stocks.add("HEIA.AS").get("already"), True)
    check("en voegt niets toe", len(stocks.watchlist()), 1)

    check("kleine letters worden genormaliseerd",
          stocks.add("heia.as").get("already"), True)

    try:
        stocks.add("ONZIN123")
        check("onbekend symbool geweigerd", "geen fout", "ValueError")
    except ValueError:
        check("onbekend symbool geweigerd", "ValueError", "ValueError")
    check("en komt niet in de lijst", len(stocks.watchlist()), 1)

    # --- ambiguous labels ------------------------------------------------
    stocks.add("HEIO.AS", "Heineken Holding N.V.")
    labels = [q["label"] for q in stocks.ticker_quotes()]
    check("gelijke labels worden onderscheiden", len(set(labels)), 2)
    check("valt terug op het symbool", sorted(labels), ["HEIA", "HEIO"])

    # --- cap -------------------------------------------------------------
    with tx() as c:
        for i in range(stocks.MAX_WATCHLIST - 2):
            c.execute("INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,"
                      "weight,created_at,updated_at) "
                      "VALUES(?,'local','stock',?,?,1.0,'x','x')",
                      (f"stk_t{i}", f"TEST{i}", f"Test {i}"))
    check("lijst zit vol", len(stocks.watchlist()), stocks.MAX_WATCHLIST)
    try:
        stocks.add("AAPL", "Apple")
        check("vol -> geweigerd", "geen fout", "ValueError")
    except ValueError:
        check("vol -> geweigerd", "ValueError", "ValueError")

    # --- removal ---------------------------------------------------------
    stocks.remove("HEIA.AS")
    check("verwijderen werkt", "HEIA.AS" in [w["symbol"] for w in stocks.watchlist()], False)
    stocks.remove("BESTAATNIET")
    check("onbekend verwijderen is stil", True, True)

    # --- failures never drop a row --------------------------------------
    rows = stocks.ticker_quotes()
    check("elke bewaakte regel komt terug", len(rows), len(stocks.watchlist()))
    check("mislukte koers blijft zichtbaar",
          all("symbol" in r and "label" in r for r in rows), True)
finally:
    with tx() as c:
        c.execute("DELETE FROM user_preferences WHERE kind='stock'")
        for s in saved:
            c.execute("INSERT INTO user_preferences(id,user_id,kind,topic_slug,value,"
                      "weight,created_at,updated_at) "
                      "VALUES(?,'local','stock',?,?,1.0,'restored','restored')",
                      (f"stk_r{s}", s, s))

print(f"\n{ok}/{ok + fail} geslaagd")
sys.exit(1 if fail else 0)
