def format_name(name: str) -> str:
    """Convert snake_case / kebab-case names to a readable display form."""
    name = name.replace("_", " ").replace("-", " ").strip()
    if len(name) == 2 and name.lower() != "on":
        return name.upper()
    return name.title()
