from __future__ import annotations

import typer

from audiobook_generator_cli.cli import generate


def run() -> None:
    """Run Typer CLI application with the audiobook generation command."""
    typer.run(generate)
