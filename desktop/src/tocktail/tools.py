"""Console entry point shipped alongside the windowed desktop executable."""

import argparse


def main(argv: list[str] | None = None) -> int:
    import sys
    parser = argparse.ArgumentParser(description="Tocktail local management tools")
    parser.add_argument("tool", choices=("sites", "browser-host", "startup", "monitor"))
    values = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(values[:1])
    rest = values[1:]
    if args.tool == "sites":
        from tocktail.browser.report import main as run
    elif args.tool == "browser-host":
        from tocktail.browser.registration import main as run
    elif args.tool == "startup":
        from tocktail.config.startup import main as run
    else:
        from tocktail.cli import main as run
    return run(rest)
