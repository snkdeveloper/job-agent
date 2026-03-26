from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple

import pandas as pd

from config import (
    ALUMNI_OUTPUT_EXCEL,
    INPUT_EXCEL,
    OUTPUT_EXCEL,
    TECHNICAL_RECRUITER_OUTPUT_EXCEL,
)


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


def _load_existing_results(path: str) -> pd.DataFrame:
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame(
            columns=["company", "location", "first_name", "last_name", "email"]
        )
    return pd.read_excel(path_obj)


def _get_processed_company_location_pairs(path: str) -> set[Tuple[str, str]]:
    """Return a set of (company, location) pairs already present in the given results file."""
    df = _load_existing_results(path)
    if df.empty:
        return set()

    # Normalize column names
    df.columns = [c.lower() for c in df.columns]
    if not {"company", "location"}.issubset(set(df.columns)):
        return set()

    pairs: set[Tuple[str, str]] = set()
    for _, row in df.iterrows():
        company = str(row.get("company", "")).strip()
        location = str(row.get("location", "")).strip()
        if company and location:
            pairs.add((company, location))
    return pairs


def get_processed_company_location_pairs() -> set[Tuple[str, str]]:
    """Backward-compatible wrapper for the primary results file."""
    return _get_processed_company_location_pairs(OUTPUT_EXCEL)


def get_processed_alumni_company_location_pairs() -> set[Tuple[str, str]]:
    """Resume support for the Northeastern alumni results file."""
    return _get_processed_company_location_pairs(ALUMNI_OUTPUT_EXCEL)


def get_processed_technical_recruiter_company_location_pairs() -> set[Tuple[str, str]]:
    """Resume support for the technical recruiter results file."""
    return _get_processed_company_location_pairs(TECHNICAL_RECRUITER_OUTPUT_EXCEL)


def _save_results(path: str, rows: Iterable[Tuple[str, str, str, str, str]]) -> None:
    """Append rows to the specified output Excel file."""
    new_df = pd.DataFrame(
        list(rows),
        columns=["company", "location", "first_name", "last_name", "email"],
    )

    existing = _load_existing_results(path)
    if existing.empty:
        combined = new_df
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)

    combined.to_excel(path, index=False, engine="openpyxl")


def save_results(rows: Iterable[Tuple[str, str, str, str, str]]) -> None:
    """Backward-compatible wrapper for the primary results file."""
    _save_results(OUTPUT_EXCEL, rows)


def save_alumni_results(rows: Iterable[Tuple[str, str, str, str, str]]) -> None:
    """Append rows to the Northeastern alumni results file."""
    _save_results(ALUMNI_OUTPUT_EXCEL, rows)


def save_technical_recruiter_results(rows: Iterable[Tuple[str, str, str, str, str]]) -> None:
    """Append rows to the technical recruiter results file."""
    _save_results(TECHNICAL_RECRUITER_OUTPUT_EXCEL, rows)

