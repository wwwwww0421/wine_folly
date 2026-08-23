"""Runs the whole pipeline end-to-end."""


from __future__ import annotations
import sys
import time
from pathlib import Path

from src.load import DatasetError, load_dataset, write_sqlite


def main() -> int:

    start = time.perf_counter()

    ## 1. Validate
    try:
        df = load_dataset(Path("data"))
    except DatasetError as e:
        print(f"BUILD FAILED!!! - {len(e.errors)} issues to fix!")
        for err in e.errors:
            print(err)
        return 1


    ## 2. Compile
    db_path = write_sqlite(df)

    ## 3. report
    for w in df.warnings:
        print(w)

    elapsed = (time.perf_counter() - start) * 1000
    print(f"{len(df.wines)} of wines, {len(df.food_tags)} of food tags, {len(df.regions)} of regions -> {db_path} ({elapsed:.0f}) ms.")

    study = [(w.id, gaps) for w in df.wines if (gaps := w.missing_info())]
    if study:
        print("Study list:")
        for wine_id, gaps, in study:
            print(f"{wine_id}: {', '.join(gaps)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())