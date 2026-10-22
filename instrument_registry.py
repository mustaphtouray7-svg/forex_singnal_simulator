import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Set, Tuple, Union


REQUIRED_FIELDS = (
    "name",
    "instrument_type",
    "provider",
    "provider_symbol",
    "data_source",
)
REQUIRED_FIELD_SET = set(REQUIRED_FIELDS)


def load_instruments(path: Union[str, Path]) -> List[Dict[str, str]]:
    """Load and validate the provider-symbol registry from JSON configuration."""
    with Path(path).open(encoding="utf-8") as registry_file:
        registry: Any = json.load(registry_file)
    if not isinstance(registry, list) or not registry:
        raise ValueError("Instrument registry must be a non-empty JSON list.")

    instruments: List[Dict[str, str]] = []
    names: Set[str] = set()
    provider_symbols: Set[Tuple[str, str]] = set()
    for index, item in enumerate(registry):
        if not isinstance(item, dict):
            raise ValueError(f"Instrument at index {index} must be a JSON object.")
        missing_fields = REQUIRED_FIELD_SET - item.keys()
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"Instrument at index {index} is missing: {missing}.")
        if any(
            not isinstance(item[field], str) or not item[field].strip()
            for field in REQUIRED_FIELDS
        ):
            raise ValueError(
                f"Instrument at index {index} has an empty or invalid required field."
            )
        name = item["name"].strip()
        if name in names:
            raise ValueError(f"Duplicate instrument name in registry: {name}.")
        provider_symbol = (item["provider"].strip(), item["provider_symbol"].strip())
        if provider_symbol in provider_symbols:
            raise ValueError(
                f"Duplicate provider symbol in registry: "
                f"{provider_symbol[0]}:{provider_symbol[1]}."
            )
        names.add(name)
        provider_symbols.add(provider_symbol)
        instruments.append({field: item[field].strip() for field in REQUIRED_FIELDS})
    return instruments


def add_instrument(
    path: Union[str, Path], instrument: Dict[str, str]
) -> List[Dict[str, str]]:
    """Atomically append a validated candidate symbol to the local registry."""
    destination = Path(path)
    instruments = load_instruments(destination)
    instruments.append(instrument)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(destination.parent),
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as registry_file:
            temporary_path = Path(registry_file.name)
            json.dump(instruments, registry_file, indent=2)
            registry_file.write("\n")
        validated = load_instruments(temporary_path)
        os.replace(str(temporary_path), str(destination))
        return validated
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
