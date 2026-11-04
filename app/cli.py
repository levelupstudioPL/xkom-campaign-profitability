"""CLI: python -m app.cli <polecenie>"""
from __future__ import annotations

import json
from datetime import date

import typer

from . import db, model, sync
from .config import get_settings

cli = typer.Typer(help="Rentowność kampanii per produkt — narzędzia administracyjne", no_args_is_help=True)


@cli.command()
def check():
    """Sprawdza połączenia z Google Ads i GA4."""
    for line in sync.check_connections():
        typer.echo(line)


@cli.command("sync")
def sync_cmd(date_from: str = typer.Option(None, "--from"), date_to: str = typer.Option(None, "--to")):
    """Synchronizuje zakres dat (domyślnie: ostatnie SYNC_LOOKBACK_DAYS dni)."""
    db.init_db()
    if date_from and date_to:
        res = sync.sync_range(date.fromisoformat(date_from), date.fromisoformat(date_to))
    else:
        res = sync.sync_recent()
    typer.echo(json.dumps(res, indent=2, default=str))


@cli.command()
def backfill(days: int = typer.Option(90, help="Ile dni wstecz"), chunk: int = typer.Option(31, help="Dni w jednej paczce")):
    """Pełne zasilenie historii w paczkach."""
    db.init_db()
    typer.echo(json.dumps(sync.sync_backfill(days, chunk_days=chunk), indent=2, default=str))


@cli.command("seed-margins")
def seed_margins(path: str = typer.Argument(None)):
    """Wczytuje marże z CSV (kategoria;marza_proc)."""
    db.init_db()
    n = db.seed_margins_from_file(path or get_settings().margins_seed_file)
    typer.echo(f"Wczytano {n} marż")


@cli.command()
def report(date_from: str = typer.Option(..., "--from"), date_to: str = typer.Option(..., "--to"), basis: str = "attr"):
    """Wypisuje rentowność kategorii w konsoli."""
    r = model.compute(model.Params(date.fromisoformat(date_from), date.fromisoformat(date_to), basis))
    for c in sorted(r["categories"], key=lambda c: -(c["cost"] or 0)):
        eaten = f"{c['eaten']:.0%}" if c["eaten"] is not None else "—"
        typer.echo(f"{c['category'][:28]:<28} koszt {c['cost']:>12,.0f}  marża {c['margin']:>12,.0f}  zjadane {eaten:>6}  [{c['status']}]")
    t = r["totals"]
    typer.echo(f"\nRAZEM koszt {t['total_cost']:,.0f} zł | marża {t['margin']:,.0f} zł | zjadane {t['eaten'] or 0:.1%} | koszt/szt. {t['cost_unit'] or 0:,.2f} zł")


if __name__ == "__main__":
    cli()
