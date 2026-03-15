from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple

import pandas as pd

from config import INPUT_EXCEL, OUTPUT_EXCEL


def load_companies() -> pd.DataFrame:
    """
    Load the list of companies and locations from INPUT_EXCEL.

    Expects columns: 'company', 'location'.
    """
    path = Path(INPUT_EXCEL)
    if not path.exists():
        raise FileNotFoundError(f"Input Excel file not found: {path.resolve()}")

    df = pd.read_excel(path)
    expected_cols = {"company", "location"}
    missing = expected_cols - set(df.columns.str.lower())
    if missing:
        raise ValueError(
            f"Input Excel must contain columns {expected_cols}, found {set(df.columns)}"
        )

    # Normalize column names to lower case
    df.columns = [c.lower() for c in df.columns]
    df = df[["company", "location"]].dropna().reset_index(drop=True)
    return df


def _load_existing_results() -> pd.DataFrame:
    path = Path(OUTPUT_EXCEL)
    if not path.exists():
        return pd.DataFrame(
            columns=["company", "location", "first_name", "last_name", "email"]
        )
    return pd.read_excel(path)


def get_processed_company_location_pairs() -> set[Tuple[str, str]]:
    """
    Return a set of (company, location) pairs that already have at least one result.
    Used to support resuming the script without reprocessing completed companies.
    """
    df = _load_existing_results()
    if df.empty:
        return set()

    # Normalize column names
    df.columns = [c.lower() for c in df.columns]
    if not {"company", "location"}.issubset(set(df.columns)):
        return set()

    pairs = set()
    for _, row in df.iterrows():
        company = str(row["company"]).strip()
        location = str(row["location"]).strip()
        if company and location:
            pairs.add((company, location))
    return pairs


def save_results(rows: Iterable[Tuple[str, str, str, str, str]]) -> None:
    """
    Append rows to OUTPUT_EXCEL.

    Each row is a tuple:
        (company, location, first_name, last_name, email)
    """
    new_df = pd.DataFrame(
        list(rows),
        columns=["company", "location", "first_name", "last_name", "email"],
    )

    existing = _load_existing_results()
    if existing.empty:
        combined = new_df
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)

    combined.to_excel(OUTPUT_EXCEL, index=False, engine="openpyxl")

