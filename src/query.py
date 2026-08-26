

from __future__ import annotations

import argparse
import sys
from .engine import Engine

BAR = '-' * 66

def _heading(text: str) -> None:
    print(f"\n{text}\n{BAR}")


def _wrap(text: str, indent: str = "      ", width: int = 66) -> str:
    words, lines, current = text.split(), [], ""
    for w in words:
        if len(current) + len(w) + 1 > width:
            lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    lines.append(current)
    return f"\n{indent}".join(lines)

def _print_pairings(engine: Engine, query: str, limit: int) -> int:
    foods, ranked = engine.pair_text(query, limit)
    if not foods:
        print("No food suggested.")
        matches = engine.resolve(query)
        if matches:
            print("Did you mean:")
            for m in matches[:5]:
                print(f" [{m.kind}] {m.id} - {m.label}")

        else:
            print("Try: python -m src.query flavours  (to browse what's in the dataset)")
        return 1


    _heading(f"{query} -> {', '.join(m.label for m in foods)}")
    if not ranked:
        print("No wine paired with these tags yet - to study...")
        return 0


    for i, w in enumerate(ranked, 1):
        regions = ', '.join(r['name'] for r in w.regions) or '-'
        print(f"\n{i}. {w.name}  [{w.strength}  score {w.score:.1f}]")
        print(f"    {regions}")
        for why in w.why:
            print(f"    {_wrap(why)}")
        if w.flavours:
            print(f"    flavours{', '.join(w.flavours)}")
    print(f"\nNext python-m src.query wine {ranked[0].wine_id}")
    return 0


def _print_wine(engine: Engine, wine_id: str) -> int:
    d = engine.wine_detail(wine_id)
    if d is None:
        print(f"No wine '{wine_id}'. Try: python -m src.query search {wine_id}")
        return 1

    w = d.wine
    _heading(f"{w['name']}  ({'/'.join(w.get('wine_type', []))})")
    scales = [f"{dim}={w[dim]}" for dim in ("body", "sweetness", "tannin", "acidity", "alcohol") if w.get(dim) is not None]
    print(" " + " ".join(scales))
    if d.flavours:
        print(f"    flavours: {', '.join(d.flavours)}")

    if d.regions:
        print(f"    regions: {', '.join(d.regions)}")

    if w.get('price_band'):
        print(f"    price: {w['price_band']}   popularity: {w.get('popularity', '-')}")

    if serving := w.get('serving'):
        bits = []
        if serving.get('temp_c'):
            bits.append(f"{serving['temp_c'][0]} - {serving['temp_c'][1]}ºC")
        if serving.get("glass"):
            bits.append(f"Use {serving['glass']}")
        if serving.get("decant_minutes"):
            bits.append(f"decant {serving['decant_minutes']} min")
        if serving.get("cellar_years"):
            bits.append(f"cellar {serving['cellar_year'][0]} - {serving['cellar_year'][1]} yrs")
        if bits:
            print(f"    serving : {' - '.join(bits)}")

    if w.get('notes'):
        print(f'\n {_wrap(w['notes'], indent='  ')}')

    if d.pairs_with:
        print("\n Pairs with:")
        for p in d.pairs_with:
            print(f"    - {p['label'] [{p['strength']}]}")
            print(f"      {_wrap(p['why'])}")
    if d.avoid:
        print("\n Avoid:")
        for a in d.avoid:
            print(f"    - {a['label']} - {_wrap(a['why'])}")

    if d.similar:
        print("\n Similar Wines:")
        for s in d.similar:
            shared = f" shares: {', '.join(s.shared_flavours)}" if s.shared_flavours else ""
            print(f"    - {s.name} ({s.similarity:.2f}{shared})")

    if d.also_try:
        print("\n   Also try: " + ", ".join(a['name'] for a in d.also_try))
    return 0


def _print_region(engine: Engine, region_id: str) -> int:
    d = engine.region_detail(region_id)
    if d is None:
        print(f"No region '{region_id}'. Try: python -m src.query search {region_id}")
        return 1
    r = d.region
    _heading(f"{r['name']}, {r['country']}" + (f"  (in {r['parent_id']})" if r["parent_id"] else ""))
    if d.flavour_profile:
        print(f"  regional flavour profile: {', '.join(d.flavour_profile)}")
    print(f"\n  Wines ({len(d.wines)})")
    for w in d.wines:
        print(f"    • {w['name']}  [{'/'.join(w['wine_type'])}]")
        if w["flavours"]:
            print(f"      {', '.join(w['flavours'])}")
    if d.siblings:
        print("\n  Nearby: " + ", ".join(s["name"] for s in d.siblings))
    return 0
 
 
def _print_similar(engine: Engine, wine_id: str, limit: int) -> int:
    results = engine.similar(wine_id, limit)
    if not results:
        print(f"Nothing comparable to '{wine_id}' yet (needs more wines, or scale values).")
        return 1
    _heading(f"Wines similar to {wine_id}")
    for s in results:
        print(f"  {s.similarity:.2f}  {s.name}")
        print(f"        compared on: {', '.join(s.shared_dims)}")
        if s.shared_flavours:
            print(f"        shared flavours: {', '.join(s.shared_flavours)}")
    return 0
 
 
def _print_flavour(engine: Engine, flavour: str) -> int:
    results = engine.by_flavour(flavour)
    if not results:
        print(f"No wine has a flavour matching '{flavour}'.")
        return 1
    _heading(f"Wines tasting of '{flavour}'")
    for w in results:
        print(f"  • {w['name']}  ({', '.join(w['matched_flavours'])})")
    return 0
 
 
def _print_flavours(engine: Engine) -> int:
    index = engine.all_flavours()
    _heading(f"flavour index ({len(index)} flavours)")
    for item in index:
        print(f"  {item['count']:3}  {item['flavour']}")
    return 0
 
 
def _print_search(engine: Engine, query: str) -> int:
    matches = engine.resolve(query)
    if not matches:
        print(f"Nothing matches '{query}'.")
        return 1
    _heading(f"Search: '{query}'")
    for m in matches:
        print(f"  [{m.kind:6}] {m.id:22} {m.label}")
    return 0



def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default='build/wine.db')
    common.add_argument("--limit", type=int, default=10)

    parser = argparse.ArgumentParser(
        prog="python -m src.query",
        description="Ask the cellar. Bare text is treated as a food query.",
        parents=[common],
    )

    sub = parser.add_subparsers(dest='command')

    p_food = sub.add_parser("food", parents=[common], help='food -> ranked wines (the default)')
    p_food.add_argument("text", nargs='+')
    for name, help_text in [
        ('wine', 'everything about a wine'),
        ('region', 'a region and its wines'),
        ('similar', 'nearest neighbour to a wine'),
        ('flavour', 'wines sharing a flavour'),
        ('search', 'what the search box would offer'),
    ]:
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.add_argument("text", nargs='+')
    sub.add_parser('flavours', parents=[common], help='the whole flavour index')

    argv = sys.argv[1:] if argv is None else argv
    known = {"food", "wine", "region", "similar", "flavour", "flavours", "search"}
    if argv and not argv[0].startswith("-") and argv[0] not in known:
        argv = ['food', *argv]

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    try:
        engine = Engine(args.db)
    except FileNotFoundError as e:
        print(e)
        return 1


    text = " ".join(getattr(args, "text", []))
    return {
        "food": lambda: _print_pairings(engine, text, args.limit),
        "wine": lambda: _print_wine(engine, text),
        "region": lambda: _print_region(engine, text),
        "similar": lambda: _print_similar(engine, text),
        "flavour": lambda: _print_flavour(engine, text),
        "flavours": lambda: _print_flavours(engine),
        "search": lambda: _print_search(engine, text),
    }[args.command]()


if __name__ == "__main__":
    sys.exit(main())