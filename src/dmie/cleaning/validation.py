"""Post-normalization sanity checks for listing records before DB write."""

REQUIRED_FIELDS = ["listing_id", "asin", "title", "raw_source_file", "created_at"]


def find_violations(listings: list[dict]) -> list[str]:
    violations: list[str] = []
    seen_ids: set[str] = set()

    for rec in listings:
        listing_id = rec.get("listing_id", "?")

        for field_name in REQUIRED_FIELDS:
            if not rec.get(field_name):
                violations.append(f"{listing_id}: missing required field '{field_name}'")

        if listing_id in seen_ids:
            violations.append(f"duplicate listing_id in normalized output: {listing_id}")
        seen_ids.add(listing_id)

        price = rec.get("price")
        if price is not None and price <= 0:
            violations.append(f"{listing_id}: invalid price {price}")

        rating = rec.get("rating")
        if rating is not None and not (0 <= rating <= 5):
            violations.append(f"{listing_id}: invalid rating {rating}")

        for field_name in ("monthly_sales", "monthly_revenue"):
            value = rec.get(field_name)
            if value is not None and value < 0:
                violations.append(f"{listing_id}: negative {field_name} {value}")

    return violations
