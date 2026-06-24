"""Demo taxonomy for the example app — healthcare mini-domain."""

DEMO_MAPPING = {
    "categories": [
        {"slug": "medication", "label": "Medication"},
        {"slug": "lab_results", "label": "Lab Results"},
        {"slug": "symptoms", "label": "Symptoms"},
    ],
    "rules": [
        {"word": "metformin", "category_slug": "medication"},
        {"word": "insulin", "category_slug": "medication"},
        {"word": "troponin", "category_slug": "lab_results"},
        {"word": "hba1c", "category_slug": "lab_results"},
        {"word": "fever", "category_slug": "symptoms"},
        {"word": "chest_pain", "category_slug": "symptoms"},
    ],
}


def demo_records() -> list[dict]:
    """Inline ingest records derived from mapping rules."""
    return [
        {"word": r["word"], "text": r["word"].replace("_", " ")}
        for r in DEMO_MAPPING["rules"]
    ]
