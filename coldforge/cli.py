"""coldforge command-line interface.

    coldforge init                       # set up local storage + example files
    coldforge templates [show ID]        # browse the template pack
    coldforge leads import leads.csv     # load targets
    coldforge research <lead|--all>      # gather personalization signals
    coldforge draft --lead X --template T# write a single email (AI if key set)
    coldforge campaign create ...        # build a sequenced campaign
    coldforge campaign preview <name>    # review the full schedule
    coldforge campaign activate <name>   # schedule it
    coldforge tick [--dry-run]           # send due mail (run from cron)
    coldforge reply mark <lead>          # record a reply (cancels follow-ups)
    coldforge stats [name]               # results
    coldforge doctor <domain>            # SPF/DKIM/DMARC deliverability
    coldforge mcp                        # run the MCP server for Claude
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import get_settings
from .db import Store
from .models import Campaign, Lead
from .research import research_lead

app = typer.Typer(
    name="coldforge",
    help="Honest, local-first cold outreach: research → personalize → sequence → send → follow.",
    no_args_is_help=True,
    add_completion=False,
)
templates_app = typer.Typer(help="Browse the template pack.", no_args_is_help=True)
leads_app = typer.Typer(help="Manage outreach targets.", no_args_is_help=True)
campaign_app = typer.Typer(help="Build and run sequenced campaigns.", no_args_is_help=True)
reply_app = typer.Typer(help="Record / detect replies.", no_args_is_help=True)
app.add_typer(templates_app, name="templates")
app.add_typer(leads_app, name="leads")
app.add_typer(campaign_app, name="campaign")
app.add_typer(reply_app, name="reply")


def _make_console() -> Console:
    # Make output safe on legacy Windows consoles / redirected pipes (cp1252) so
    # a status glyph never crashes the CLI. Done before the Console is built.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return Console()


console = _make_console()


def _store() -> Store:
    return Store(get_settings().db_path)


def _err(msg: str) -> None:
    console.print(f"[bold red]✗[/] {msg}")
    raise typer.Exit(1)


# ── top-level ────────────────────────────────────────────────────────────────
@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"coldforge {__version__}")


@app.command()
def init(
    here: bool = typer.Option(False, "--here", help="Write example files into the current dir."),
) -> None:
    """Create local storage and drop example `leads.csv` / `sequence.yml`."""
    s = get_settings()
    s.home.mkdir(parents=True, exist_ok=True)
    Store(s.db_path).close()  # creates schema

    target = Path.cwd() if here else s.home
    examples = {
        "leads.csv": _EXAMPLE_LEADS,
        "sequence.yml": _EXAMPLE_SEQUENCE,
    }
    for name, content in examples.items():
        path = target / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    console.print(Panel.fit(
        f"[green]Ready.[/]\n"
        f"Storage:   [cyan]{s.db_path}[/]\n"
        f"Examples:  [cyan]{target / 'leads.csv'}[/], [cyan]{target / 'sequence.yml'}[/]\n\n"
        f"AI drafts: {'[green]on[/]' if s.has_ai else '[yellow]off (template fill)[/]'}    "
        f"Sending:   {'[green]configured[/]' if s.can_send else '[yellow]dry-run only[/]'}\n\n"
        f"Next: [bold]coldforge leads import {target / 'leads.csv'}[/]",
        title="coldforge init", border_style="green",
    ))


@app.command()
def doctor(domain: str = typer.Argument(..., help="Sending domain, e.g. acme.com")) -> None:
    """Check SPF / DKIM / DMARC before you send a single email."""
    from .deliverability import check_domain

    report = check_domain(domain)
    table = Table(title=f"Deliverability — {report.domain}", show_lines=False)
    table.add_column("Check")
    table.add_column("")
    table.add_column("Detail", overflow="fold")
    for c in report.checks:
        mark = "[green]✓[/]" if c.ok else "[red]✗[/]"
        table.add_row(c.name, mark, c.detail + ("" if c.ok else f"\n[dim]fix: {c.fix}[/]"))
    console.print(table)
    color = {"ready": "green", "needs work": "yellow"}.get(report.verdict.split(" —")[0], "red")
    console.print(f"Score: [bold {color}]{report.score}/100[/] — {report.verdict}")


@app.command("mcp")
def run_mcp() -> None:
    """Run the MCP server (research_prospect / draft_email) over stdio for Claude."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        _err("MCP extra not installed. Run: pip install 'coldforge[mcp]'")
    mcp_main()


# ── templates ────────────────────────────────────────────────────────────────
@templates_app.command("list")
def templates_list(
    category: str = typer.Option("", "--category", "-c", help="Filter by category."),
) -> None:
    """List available templates."""
    from .templates import by_category

    items = by_category(category or None)
    if not items:
        _err("No templates found.")
    table = Table(show_lines=False)
    table.add_column("id", style="cyan")
    table.add_column("category")
    table.add_column("use case", overflow="fold")
    for t in items:
        table.add_row(t.id, t.category, t.use_case or t.name)
    console.print(table)


@templates_app.command("show")
def templates_show(template_id: str) -> None:
    """Show one template's metadata and body."""
    from .templates import get as get_template

    try:
        t = get_template(template_id)
    except KeyError as e:
        _err(str(e))
    meta = (f"[bold]{t.name}[/]  [dim]({t.id} · {t.category})[/]\n"
            f"[dim]persona:[/] {t.persona}\n[dim]use case:[/] {t.use_case}\n"
            f"[dim]variables:[/] {', '.join(sorted(t.required_vars()))}")
    console.print(Panel(meta, border_style="cyan"))
    if t.deliverability_notes:
        console.print(Panel(t.deliverability_notes.strip(), title="deliverability", border_style="yellow"))
    console.print(Panel(f"[bold]Subject:[/] {t.subject}\n\n{t.body}", title="template", border_style="blue"))


# ── leads ────────────────────────────────────────────────────────────────────
_CANON = {"email", "first_name", "last_name", "company", "title", "website", "linkedin"}


def _import_csv(store: Store, path: Path) -> int:
    """Load leads from *path* into *store*; returns the row count. Raises on a
    missing email column."""
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "email" not in [f.lower() for f in reader.fieldnames]:
            raise ValueError("CSV must have an 'email' column.")
        for row in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("email"):
                continue
            custom = {k: v for k, v in row.items() if k not in _CANON and v}
            store.upsert_lead(Lead(
                email=row["email"], first_name=row.get("first_name", ""),
                last_name=row.get("last_name", ""), company=row.get("company", ""),
                title=row.get("title", ""), website=row.get("website", ""),
                linkedin=row.get("linkedin", ""), custom=custom,
            ))
            count += 1
    return count


@leads_app.command("import")
def leads_import(
    path: Path = typer.Argument(..., exists=True, readable=True, help="CSV of leads."),
) -> None:
    """Import leads from a CSV. Unknown columns become template variables."""
    with _store() as store:
        try:
            count = _import_csv(store, path)
        except ValueError as e:
            _err(str(e))
    console.print(f"[green]✓[/] Imported / updated [bold]{count}[/] leads.")


@leads_app.command("list")
def leads_list() -> None:
    """List stored leads."""
    with _store() as store:
        leads = store.list_leads()
    if not leads:
        console.print("[yellow]No leads yet.[/] Import some: coldforge leads import leads.csv")
        return
    table = Table(show_lines=False)
    for col in ("id", "email", "name", "company", "title"):
        table.add_column(col)
    for ld in leads:
        name = " ".join(p for p in (ld.first_name, ld.last_name) if p)
        table.add_row(str(ld.id), ld.email, name, ld.company, ld.title)
    console.print(table)


# ── research ─────────────────────────────────────────────────────────────────
@app.command()
def research(
    lead: str = typer.Argument("", help="Lead id / email. Omit with --all."),
    all_leads: bool = typer.Option(False, "--all", help="Research every stored lead."),
) -> None:
    """Gather personalization signals for one or all leads and store them."""
    settings = get_settings()
    with _store() as store:
        targets = store.list_leads() if all_leads else (
            [store.find_lead(lead)] if lead else [])
        targets = [t for t in targets if t]
        if not targets:
            _err("No matching lead. Use a lead id/email or --all.")
        for ld in targets:
            result = research_lead(ld, settings)
            if result.best:
                store.add_signal(result.best)
                console.print(f"[green]✓[/] [cyan]{ld.email}[/] · "
                              f"[dim]{result.best.source}[/] {result.best.text[:120]}")
            else:
                console.print(f"[yellow]∅[/] [cyan]{ld.email}[/] — no signal found")
    if not settings.tavily_api_key:
        console.print("[dim]tip: set TAVILY_API_KEY for stronger signals than the DuckDuckGo fallback.[/]")


# ── draft ────────────────────────────────────────────────────────────────────
@app.command()
def draft(
    lead: str = typer.Option(..., "--lead", "-l", help="Lead id / email."),
    template: str = typer.Option(..., "--template", "-t", help="Template id."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Force deterministic template fill."),
    do_research: bool = typer.Option(False, "--research", help="Research first, then draft."),
) -> None:
    """Write a single email for a lead (Claude-personalized when a key is set)."""
    from .personalize import draft_email

    settings = get_settings()
    with _store() as store:
        ld = store.find_lead(lead)
        if not ld:
            _err(f"Lead '{lead}' not found.")
        if do_research and ld.id:
            r = research_lead(ld, settings)
            if r.best:
                store.add_signal(r.best)
        signal = next(iter(store.signals_for(ld.id)), None) if ld.id else None
        d = draft_email(template, ld, signal=signal, settings=settings, force_template_fill=no_ai)

    badge = "[green]AI-personalized[/]" if d.personalized else "[yellow]template fill[/]"
    console.print(Panel(f"[bold]To:[/] {ld.email}    {badge}\n"
                        f"[bold]Subject:[/] {d.subject}\n\n{d.body}",
                        title=f"draft · {d.template_id}", border_style="blue"))
    if d.notes:
        console.print(f"[dim]{d.notes}[/]")


# ── campaign ─────────────────────────────────────────────────────────────────
@campaign_app.command("create")
def campaign_create(
    name: str = typer.Option(..., "--name", "-n"),
    sequence: Path = typer.Option(None, "--sequence", "-s", help="Sequence YAML (optional)."),
    template: str = typer.Option("", "--template", "-t", help="Single-step shortcut template id."),
    from_email: str = typer.Option("", "--from", help="Sending address (defaults to .env)."),
) -> None:
    """Create a campaign from a sequence file or a single template."""
    from .sequence import DEFAULT_SEQUENCE, load_sequence, normalize_sequence

    settings = get_settings()
    if sequence:
        seq = load_sequence(sequence)
    elif template:
        seq = normalize_sequence([{"template": template, "wait_days": 0, "condition": "always"}])
    else:
        seq = DEFAULT_SEQUENCE
    with _store() as store:
        if store.get_campaign(name):
            _err(f"Campaign '{name}' already exists.")
        c = Campaign(name=name, from_email=from_email or settings.from_email, sequence=seq)
        store.create_campaign(c)
    console.print(f"[green]✓[/] Created campaign [bold]{name}[/] "
                  f"({len(seq)} step{'s' if len(seq) != 1 else ''}). "
                  f"Next: coldforge campaign activate {name} --leads leads.csv")


@campaign_app.command("activate")
def campaign_activate(
    name: str = typer.Argument(...),
    leads: Path = typer.Option(None, "--leads", help="CSV to import + enroll (optional)."),
    personalize: bool = typer.Option(False, "--personalize", help="AI-personalize each step now."),
    start: str = typer.Option("", "--start", help="ISO datetime to begin (default now)."),
) -> None:
    """Schedule every step for every enrolled lead and mark the campaign active."""
    from .sequence import schedule_campaign

    settings = get_settings()
    start_dt = datetime.fromisoformat(start) if start else None
    with _store() as store:
        c = store.get_campaign(name)
        if not c:
            _err(f"Campaign '{name}' not found.")
        if leads:
            try:
                _import_csv(store, leads)
            except ValueError as e:
                _err(str(e))
        enrolled = store.list_leads()
        if not enrolled:
            _err("No leads to enroll. Pass --leads or run: coldforge leads import …")
        created = schedule_campaign(store, c, enrolled, settings,
                                    start=start_dt, personalize=personalize)
        store.set_campaign_status(c.id, "active")  # type: ignore[arg-type]
    console.print(f"[green]✓[/] Activated [bold]{name}[/]: scheduled [bold]{created}[/] messages "
                  f"for {len(enrolled)} leads. Preview: coldforge campaign preview {name}")


@campaign_app.command("preview")
def campaign_preview(name: str = typer.Argument(...)) -> None:
    """Show the full scheduled timeline before anything sends."""
    with _store() as store:
        c = store.get_campaign(name)
        if not c:
            _err(f"Campaign '{name}' not found.")
        msgs = store.messages_for_campaign(c.id)  # type: ignore[arg-type]
        if not msgs:
            console.print("[yellow]Nothing scheduled yet.[/] Run: coldforge campaign activate "
                          f"{name} --leads leads.csv")
            return
        table = Table(title=f"{name} · {c.status}", show_lines=False)
        for col in ("when", "step", "to", "subject", "status"):
            table.add_column(col, overflow="fold")
        for m in msgs[:200]:
            ld = store.get_lead(m.lead_id)
            table.add_row(m.scheduled_at.strftime("%Y-%m-%d %H:%M"), str(m.step),
                          ld.email if ld else "?", m.subject, m.status)
    console.print(table)


@campaign_app.command("list")
def campaign_list() -> None:
    """List campaigns."""
    with _store() as store:
        items = store.list_campaigns()
    if not items:
        console.print("[yellow]No campaigns yet.[/]")
        return
    table = Table(show_lines=False)
    for col in ("id", "name", "status", "steps", "from"):
        table.add_column(col)
    for c in items:
        table.add_row(str(c.id), c.name, c.status, str(len(c.sequence)), c.from_email)
    console.print(table)


# ── tick (the worker) ────────────────────────────────────────────────────────
@app.command()
def tick(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would send; send nothing."),
    scan: bool = typer.Option(False, "--scan-replies", help="Poll IMAP for replies first."),
) -> None:
    """Send all due messages, honouring guardrails and the reply→cancel rule."""
    from .sender import make_sender, scan_replies
    from .sequence import tick as run_tick

    settings = get_settings()
    with _store() as store:
        if scan and settings.can_detect_replies:
            n = scan_replies(store, settings)
            console.print(f"[dim]reply scan: {n} new[/]")
        sender = make_sender(settings, dry_run=dry_run)
        is_dry = dry_run or not settings.can_send
        result = run_tick(store, settings, sender, dry_run=is_dry)
        # Resolve recipients before the store closes.
        preview = [(store.get_lead(m.lead_id), m) for m in result.sent[:20]]

    if is_dry:
        console.print(f"[yellow]DRY-RUN[/] — would send [bold]{len(result.sent)}[/] "
                      f"({'no SMTP configured' if not settings.can_send else '--dry-run'}).")
        for ld, m in preview:
            console.print(f"  → step {m.step} · {ld.email if ld else '?'}: {m.subject}")
    else:
        console.print(f"[green]✓[/] Sent [bold]{len(result.sent)}[/]. "
                      f"Skipped (replied): {len(result.skipped_replied)}, "
                      f"canceled follow-ups: {result.canceled}.")
    if result.held_window:
        console.print("[dim]Held: outside send window/days.[/]")
    if result.held_limit:
        console.print(f"[dim]Held {result.held_limit} for the daily limit ({settings.daily_limit}).[/]")


# ── replies ──────────────────────────────────────────────────────────────────
@reply_app.command("mark")
def reply_mark(lead: str = typer.Argument(..., help="Lead id / email that replied.")) -> None:
    """Record a reply manually — cancels that lead's pending follow-ups."""
    with _store() as store:
        ld = store.find_lead(lead)
        if not ld or ld.id is None:
            _err(f"Lead '{lead}' not found.")
        store.record_reply(ld.id, None, source="manual")
        canceled = 0
        for c in store.list_campaigns():
            canceled += store.cancel_pending_for_lead(c.id, ld.id)  # type: ignore[arg-type]
    console.print(f"[green]✓[/] Recorded reply from {ld.email}; canceled {canceled} pending follow-ups.")


@reply_app.command("scan")
def reply_scan() -> None:
    """Poll IMAP and record replies from known leads."""
    from .sender import scan_replies

    settings = get_settings()
    if not settings.can_detect_replies:
        _err("IMAP not configured (set IMAP_HOST / IMAP_USER / IMAP_PASSWORD).")
    with _store() as store:
        n = scan_replies(store, settings)
    console.print(f"[green]✓[/] Recorded [bold]{n}[/] new replies.")


# ── stats ────────────────────────────────────────────────────────────────────
@app.command()
def stats(name: str = typer.Argument("", help="Campaign name (omit for all).")) -> None:
    """Show send / reply counts."""
    with _store() as store:
        campaigns = ([store.get_campaign(name)] if name else store.list_campaigns())
        campaigns = [c for c in campaigns if c]
        if not campaigns:
            _err("No campaigns found.")
        table = Table(show_lines=False)
        for col in ("campaign", "scheduled", "sent", "replied", "skipped", "reply %"):
            table.add_column(col)
        for c in campaigns:
            msgs = store.messages_for_campaign(c.id)  # type: ignore[arg-type]
            leads_in = {m.lead_id for m in msgs}
            sent = sum(1 for m in msgs if m.status == "sent")
            scheduled = sum(1 for m in msgs if m.status == "scheduled")
            skipped = sum(1 for m in msgs if m.status in ("skipped", "canceled"))
            replied = sum(1 for lid in leads_in if store.has_replied(lid, c.id))
            rate = f"{(replied / len(leads_in) * 100):.0f}%" if leads_in else "—"
            table.add_row(c.name, str(scheduled), str(sent), str(replied), str(skipped), rate)
    console.print(table)


# ── example file contents ────────────────────────────────────────────────────
_EXAMPLE_LEADS = (
    "email,first_name,last_name,company,title,website,pain,outcome\n"
    "alex@acme.io,Alex,Rivera,Acme,Head of Ops,acme.io,"
    "manual invoice reconciliation,close the books 4 days faster\n"
    "sam@northwind.co,Sam,Lee,Northwind,Founder,northwind.co,"
    "support tickets piling up overnight,cut first-response time in half\n"
)

_EXAMPLE_SEQUENCE = (
    "# A two-touch sequence: opener now, one soft bump after 3 days if no reply.\n"
    "- template: sales_pain_point\n"
    "  wait_days: 0\n"
    "  condition: always\n"
    "- template: followup_bump\n"
    "  wait_days: 3\n"
    "  condition: no_reply\n"
)


if __name__ == "__main__":
    app()
