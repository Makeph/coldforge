"""coldforge command-line interface.

    coldforge init                       # set up local storage + example files
    coldforge icp build --site my.site   # learn what you sell → who buys it
    coldforge templates [show ID]        # browse the template pack
    coldforge leads import leads.csv     # load targets
    coldforge leads verify               # syntax / disposable / MX before sending
    coldforge leads score                # rank every lead 0-100 against the ICP
    coldforge research <lead|--all>      # gather personalization signals
    coldforge draft --lead X --template T# write a single email (AI if key set)
    coldforge lint --lead X --template T # spam-filter check the copy
    coldforge campaign create ...        # build a sequenced campaign
    coldforge campaign preview <name>    # review the full schedule
    coldforge campaign activate <name>   # schedule it
    coldforge tick [--dry-run]           # send due mail (run from cron)
    coldforge reply mark <lead>          # record a reply (cancels follow-ups)
    coldforge suppress add <email>       # do-not-contact list
    coldforge stats [name] [--by ...]    # results, incl. per template/variant
    coldforge doctor <domain>            # SPF/DKIM/DMARC deliverability
    coldforge geo check --query "..."    # are you mentioned by ChatGPT/Claude/Perplexity/Gemini?
    coldforge content plan               # cluster the ICP's keyword gaps into article briefs
    coldforge content draft <id>         # write one article (AI if key set)
    coldforge mcp                        # run the MCP server (any MCP client)
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
from .models import Campaign, Lead, Signal
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
icp_app = typer.Typer(help="Ideal Customer Profile: build from your site, then score leads.",
                      no_args_is_help=True)
suppress_app = typer.Typer(help="Do-not-contact list.", no_args_is_help=True)
geo_app = typer.Typer(help="AI-answer-engine visibility (GEO): are you mentioned when a "
                          "buyer asks ChatGPT / Claude / Perplexity / Gemini?",
                     no_args_is_help=True)
content_app = typer.Typer(help="SEO/GEO content: cluster the ICP's keyword gaps into "
                              "article briefs, then draft them.", no_args_is_help=True)
app.add_typer(templates_app, name="templates")
app.add_typer(leads_app, name="leads")
app.add_typer(campaign_app, name="campaign")
app.add_typer(reply_app, name="reply")
app.add_typer(icp_app, name="icp")
app.add_typer(suppress_app, name="suppress")
app.add_typer(geo_app, name="geo")
app.add_typer(content_app, name="content")


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
    """Run the MCP server (research_prospect / draft_email) over stdio."""
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
        console.print(
            Panel(t.deliverability_notes.strip(), title="deliverability", border_style="yellow")
        )
    console.print(
        Panel(
            f"[bold]Subject:[/] {t.subject}\n\n{t.body}", title="template", border_style="blue"
        )
    )


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
    """List stored leads (best fit first once scored)."""
    with _store() as store:
        leads = store.list_leads()
    if not leads:
        console.print("[yellow]No leads yet.[/] Import some: coldforge leads import leads.csv")
        return
    leads.sort(key=lambda ld: (ld.fit_score is None, -(ld.fit_score or 0), ld.id or 0))
    table = Table(show_lines=False)
    for col in ("id", "email", "name", "company", "title", "fit", "verify"):
        table.add_column(col)
    for ld in leads:
        name = " ".join(p for p in (ld.first_name, ld.last_name) if p)
        fit = "" if ld.fit_score is None else str(ld.fit_score)
        verify = {"ok": "[green]ok[/]", "risky": "[yellow]risky[/]",
                  "invalid": "[red]invalid[/]"}.get(ld.verify_status, "")
        table.add_row(str(ld.id), ld.email, name, ld.company, ld.title, fit, verify)
    console.print(table)


@leads_app.command("verify")
def leads_verify(
    lead: str = typer.Argument("", help="Lead id / email. Omit to verify every lead."),
    no_mx: bool = typer.Option(False, "--no-mx", help="Skip the network MX lookup."),
) -> None:
    """Check syntax / disposable domains / role accounts / MX; store the verdict.

    Invalid addresses are excluded from scheduling and sending automatically.
    """
    from .verify import verify_email

    with _store() as store:
        targets = [store.find_lead(lead)] if lead else store.list_leads()
        targets = [t for t in targets if t]
        if not targets:
            _err("No matching lead. Import some first: coldforge leads import leads.csv")
        counts = {"ok": 0, "risky": 0, "invalid": 0}
        for ld in targets:
            r = verify_email(ld.email, check_mx=not no_mx)
            store.set_verify_status(ld.id, r.status)  # type: ignore[arg-type]
            counts[r.status] += 1
            mark = {"ok": "[green]✓[/]", "risky": "[yellow]~[/]", "invalid": "[red]✗[/]"}[r.status]
            detail = f" [dim]{'; '.join(r.reasons)}[/]" if r.reasons else ""
            console.print(f"{mark} [cyan]{ld.email}[/] {r.status}{detail}")
    console.print(f"\n[green]{counts['ok']} ok[/] · [yellow]{counts['risky']} risky[/] · "
                  f"[red]{counts['invalid']} invalid[/] (invalid are never sent to)")


@leads_app.command("score")
def leads_score(
    lead: str = typer.Argument("", help="Lead id / email. Omit to score every lead."),
) -> None:
    """Score leads 0–100 against the stored ICP (build one first: coldforge icp build)."""
    from .icp import load_icp, score_lead

    settings = get_settings()
    icp = load_icp(settings)
    if not icp:
        _err("No ICP yet. Build one first: coldforge icp build --site your-site.com")
    with _store() as store:
        targets = [store.find_lead(lead)] if lead else store.list_leads()
        targets = [t for t in targets if t]
        if not targets:
            _err("No matching lead.")
        for ld in targets:
            signal = next(iter(store.signals_for(ld.id)), None) if ld.id else None
            score, reason = score_lead(ld, icp, signal_text=signal.text if signal else "",
                                       settings=settings)
            store.set_fit(ld.id, score, reason)  # type: ignore[arg-type]
            color = "green" if score >= 70 else ("yellow" if score >= 40 else "red")
            console.print(f"[{color}]{score:>3}[/] [cyan]{ld.email}[/] [dim]{reason}[/]")
    console.print("\n[dim]Ranked view: coldforge leads list[/]")


# ── icp ──────────────────────────────────────────────────────────────────────
@icp_app.command("build")
def icp_build(
    site: str = typer.Option(..., "--site", "-s", help="Your product site, e.g. acme.io"),
) -> None:
    """Read your own site and derive an ICP: product, pains, buyer segments.

    An LLM writes the profile when ANTHROPIC_API_KEY is set; otherwise a local
    keyword profile is extracted. The result is a plain JSON file you can edit.
    """
    from .icp import build_icp, save_icp

    settings = get_settings()
    try:
        icp = build_icp(site, settings)
    except ValueError as e:
        _err(str(e))
    path = save_icp(icp, settings)
    console.print(Panel.fit(
        f"[bold]{icp.get('site')}[/] [dim]({icp.get('source')})[/]\n\n"
        f"{(icp.get('product') or '')[:400]}",
        title="ICP", border_style="green",
    ))
    if icp.get("segments"):
        table = Table(title="Who buys it", show_lines=False)
        for col in ("segment", "fit", "why"):
            table.add_column(col, overflow="fold")
        for seg in icp["segments"]:
            table.add_row(str(seg.get("name", "")), str(seg.get("fit", "")),
                          str(seg.get("why", "")))
        console.print(table)
    if icp.get("content_gaps"):
        table = Table(title="Content/SEO gaps", show_lines=False)
        for col in ("topic", "why"):
            table.add_column(col, overflow="fold")
        for gap in icp["content_gaps"]:
            table.add_row(str(gap.get("topic", "")), str(gap.get("why", "")))
        console.print(table)
        console.print("[dim]Turn these into articles: coldforge content plan[/]")
    console.print(f"[dim]Saved to {path} — edit freely. "
                  f"Next: coldforge leads score[/]")


@icp_app.command("show")
def icp_show() -> None:
    """Print the stored ICP."""
    from .icp import icp_path, load_icp

    icp = load_icp()
    if not icp:
        _err("No ICP yet. Build one: coldforge icp build --site your-site.com")
    console.print_json(data=icp)
    console.print(f"[dim]{icp_path()}[/]")


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
        console.print(
            "[dim]tip: set TAVILY_API_KEY for stronger signals than the DuckDuckGo fallback.[/]"
        )


# ── draft ────────────────────────────────────────────────────────────────────
@app.command()
def draft(
    lead: str = typer.Option(..., "--lead", "-l", help="Lead id / email."),
    template: str = typer.Option(..., "--template", "-t", help="Template id."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Force deterministic template fill."),
    do_research: bool = typer.Option(False, "--research", help="Research first, then draft."),
) -> None:
    """Write a single email for a lead (LLM-personalized when a key is set)."""
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


# ── lint ─────────────────────────────────────────────────────────────────────
@app.command()
def lint(
    lead: str = typer.Option("", "--lead", "-l", help="Lead id / email to render for."),
    template: str = typer.Option("", "--template", "-t", help="Template id to render."),
    subject: str = typer.Option("", "--subject", help="Or lint raw copy: the subject."),
    body: str = typer.Option("", "--body", help="Or lint raw copy: the body."),
) -> None:
    """Spam-filter check an email before it sends (trigger words, links, caps…).

    Either render a template for a lead (--lead + --template) or pass raw copy
    (--subject + --body). Score < 70 means: rewrite before sending.
    """
    from .lint import lint_draft

    if template:
        from .personalize import draft_email

        settings = get_settings()
        with _store() as store:
            ld = store.find_lead(lead) if lead else None
            if lead and not ld:
                _err(f"Lead '{lead}' not found.")
            ld = ld or Lead(email="preview@example.com")
            signal = next(iter(store.signals_for(ld.id)), None) if ld.id else None
            d = draft_email(template, ld, signal=signal, settings=settings,
                            force_template_fill=True)
        subject, body = d.subject, d.body
    elif not (subject or body):
        _err("Pass --template (with optional --lead), or --subject/--body.")

    report = lint_draft(subject, body)
    for issue in report.issues:
        mark = "[red]✗[/]" if issue.severity == "block" else "[yellow]![/]"
        console.print(f"{mark} {issue.message}")
    color = "green" if report.ok else "red"
    verdict = "clean enough to send" if report.ok else "rewrite before sending"
    console.print(f"Score: [bold {color}]{report.score}/100[/] — {verdict}")
    if not report.ok:
        raise typer.Exit(1)


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
                      f"skipped (suppressed/invalid): {len(result.skipped_suppressed)}, "
                      f"canceled follow-ups: {result.canceled}.")
    if result.held_window:
        console.print("[dim]Held: outside send window/days.[/]")
    if result.held_limit:
        console.print(
            f"[dim]Held {result.held_limit} for the daily limit ({settings.daily_limit}).[/]"
        )


# ── replies ──────────────────────────────────────────────────────────────────
@reply_app.command("mark")
def reply_mark(
    lead: str = typer.Argument(..., help="Lead id / email that replied."),
    category: str = typer.Option("", "--category", "-c",
                                 help="interested | not_interested | unsubscribe | ooo | other"),
    text: str = typer.Option("", "--text", help="Paste the reply to auto-classify it."),
) -> None:
    """Record a reply manually — cancels that lead's pending follow-ups.

    Pass --text to auto-classify, or --category to set it yourself. An
    'unsubscribe' lands the address on the suppression list immediately.
    """
    from .replies import CATEGORIES, classify_reply

    if category and category not in CATEGORIES:
        _err(f"Unknown category '{category}'. One of: {', '.join(CATEGORIES)}")
    if not category and text:
        category = classify_reply("", text, get_settings())
    with _store() as store:
        ld = store.find_lead(lead)
        if not ld or ld.id is None:
            _err(f"Lead '{lead}' not found.")
        store.record_reply(ld.id, None, source="manual", category=category)
        if category == "unsubscribe":
            store.suppress(ld.email, reason="reply asked to stop")
        canceled = 0
        for c in store.list_campaigns():
            canceled += store.cancel_pending_for_lead(c.id, ld.id)  # type: ignore[arg-type]
    label = f" [dim]({category})[/]" if category else ""
    console.print(f"[green]✓[/] Recorded reply from {ld.email}{label}; "
                  f"canceled {canceled} pending follow-ups."
                  + (" Added to suppression list." if category == "unsubscribe" else ""))


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


# ── suppressions ─────────────────────────────────────────────────────────────
@suppress_app.command("add")
def suppress_add(
    email: str = typer.Argument(..., help="Address to never contact again."),
    reason: str = typer.Option("", "--reason", help="Why (kept for your records)."),
) -> None:
    """Add an address to the do-not-contact list; pending sends are canceled."""
    with _store() as store:
        store.suppress(email, reason)
        canceled = 0
        ld = store.find_lead(email)
        if ld and ld.id is not None:
            for c in store.list_campaigns():
                canceled += store.cancel_pending_for_lead(c.id, ld.id)  # type: ignore[arg-type]
    console.print(f"[green]✓[/] Suppressed {email.strip().lower()}"
                  + (f"; canceled {canceled} pending sends." if canceled else "."))


@suppress_app.command("remove")
def suppress_remove(email: str = typer.Argument(...)) -> None:
    """Remove an address from the do-not-contact list."""
    with _store() as store:
        removed = store.unsuppress(email)
    if removed:
        console.print(f"[green]✓[/] Removed {email.strip().lower()}.")
    else:
        console.print(f"[yellow]∅[/] {email.strip().lower()} was not suppressed.")


@suppress_app.command("list")
def suppress_list() -> None:
    """Show the do-not-contact list."""
    with _store() as store:
        rows = store.list_suppressions()
    if not rows:
        console.print("[dim]Suppression list is empty.[/]")
        return
    table = Table(show_lines=False)
    for col in ("email", "reason", "since"):
        table.add_column(col, overflow="fold")
    for email, reason, created in rows:
        table.add_row(email, reason, created[:10])
    console.print(table)


# ── geo (AI-answer-engine visibility) ────────────────────────────────────────
@geo_app.command("check")
def geo_check(
    query: str = typer.Option("", "--query", "-q",
                              help="Buyer question to ask (default: derived from the ICP)."),
    brand: str = typer.Option("", "--brand", "-b",
                              help="Your brand/domain to look for (default: the ICP site)."),
    lead: str = typer.Option("", "--lead", "-l",
                             help="Save the result as research signal(s) on this lead."),
) -> None:
    """Ask every configured AI engine a buyer question and see who gets mentioned.

    Configure at least one of ANTHROPIC_API_KEY / OPENAI_API_KEY /
    PERPLEXITY_API_KEY / GEMINI_API_KEY — engines without a key are skipped.
    """
    from .geo import check_visibility
    from .icp import load_icp

    settings = get_settings()
    icp = load_icp(settings)
    if not query:
        if not icp or not icp.get("segments"):
            _err("Pass --query, or build an ICP first: coldforge icp build --site your-site.com")
        query = f"What are the best tools or providers for {icp['segments'][0].get('name', '')}?"
    if not brand:
        if not icp or not icp.get("site"):
            _err("Pass --brand, or build an ICP first: coldforge icp build --site your-site.com")
        brand = icp["site"].split("//")[-1].split("/")[0].split(".")[0]

    results = check_visibility(query, brand, settings)
    if not results:
        _err("No GEO engine configured. Set one of ANTHROPIC_API_KEY / OPENAI_API_KEY / "
             "PERPLEXITY_API_KEY / GEMINI_API_KEY.")

    table = Table(title=f'"{query}" — looking for "{brand}"', show_lines=False)
    for col in ("engine", "mentioned", "excerpt"):
        table.add_column(col, overflow="fold")
    for r in results:
        mark = "[green]yes[/]" if r.mentioned else "[red]no[/]"
        table.add_row(r.engine, mark, r.snippet)
    console.print(table)

    if lead:
        with _store() as store:
            ld = store.find_lead(lead)
            if not ld or ld.id is None:
                _err(f"Lead '{lead}' not found.")
            for r in results:
                store.add_signal(Signal(
                    lead_id=ld.id,
                    text=(f"When asked '{query}', {r.engine} "
                          f"{'mentions' if r.mentioned else 'does NOT mention'} "
                          f"{brand}: {r.snippet}"),
                    source=f"geo:{r.engine}",
                ))
        console.print(f"[dim]Saved as {len(results)} research signal(s) on {lead}.[/]")


# ── content (SEO/GEO article planning) ───────────────────────────────────────
@content_app.command("plan")
def content_plan_cmd(
    count: int = typer.Option(6, "--count", "-n", help="How many article briefs to plan."),
) -> None:
    """Cluster the stored ICP's keywords / content_gaps into article briefs."""
    from .content import plan_content, plan_path, save_plan
    from .icp import load_icp

    settings = get_settings()
    icp = load_icp(settings)
    if not icp:
        _err("No ICP yet. Build one first: coldforge icp build --site your-site.com")
    briefs = plan_content(icp, count, settings)
    if not briefs:
        _err("Nothing to plan — the ICP has no keywords yet.")
    save_plan(briefs, settings)
    table = Table(title="Content plan", show_lines=False)
    for col in ("id", "topic", "keywords"):
        table.add_column(col, overflow="fold")
    for b in briefs:
        table.add_row(b.id, b.topic, ", ".join(b.keywords))
    console.print(table)
    console.print(f"[dim]Saved to {plan_path(settings)}. "
                  f"Next: coldforge content draft <id>[/]")


@content_app.command("list")
def content_list() -> None:
    """Show the planned briefs and their draft status."""
    from .content import load_plan

    briefs = load_plan()
    if not briefs:
        _err("No content plan yet. Run: coldforge content plan")
    table = Table(show_lines=False)
    for col in ("id", "status", "topic", "keywords"):
        table.add_column(col, overflow="fold")
    for b in briefs:
        table.add_row(b.id, b.status, b.topic, ", ".join(b.keywords))
    console.print(table)


@content_app.command("draft")
def content_draft(
    brief_id: str = typer.Argument(..., help="Brief id from `coldforge content plan`."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Force the deterministic skeleton."),
) -> None:
    """Write one article from a brief (LLM if a key is set, else a skeleton outline)."""
    from .content import draft_article, find_brief, load_plan, save_plan
    from .icp import load_icp

    settings = get_settings()
    icp = load_icp(settings) or {}
    brief = find_brief(brief_id, settings)
    if not brief:
        _err(f"Brief '{brief_id}' not found. Run: coldforge content list")
    draft_article(brief, icp, settings, force_heuristic=no_ai)
    briefs = [brief if b.id == brief.id else b for b in load_plan(settings)]
    save_plan(briefs, settings)
    console.print(Panel(brief.body, title=f"draft · {brief.id}", border_style="blue"))
    console.print(f"[dim]Saved into the content plan. Next: coldforge content show {brief.id}[/]")


@content_app.command("show")
def content_show(brief_id: str = typer.Argument(...)) -> None:
    """Print a brief's metadata and drafted body (if any)."""
    from .content import find_brief

    brief = find_brief(brief_id)
    if not brief:
        _err(f"Brief '{brief_id}' not found.")
    meta = (f"[bold]{brief.topic}[/]  [dim]({brief.id} · {brief.status})[/]\n"
            f"[dim]angle:[/] {brief.angle}\n[dim]keywords:[/] {', '.join(brief.keywords)}")
    console.print(Panel(meta, border_style="cyan"))
    if brief.body:
        console.print(Panel(brief.body, title="article", border_style="blue"))
    else:
        console.print(f"[yellow]Not drafted yet.[/] Run: coldforge content draft {brief.id}")


# ── stats ────────────────────────────────────────────────────────────────────
@app.command()
def stats(
    name: str = typer.Argument("", help="Campaign name (omit for all)."),
    by: str = typer.Option("", "--by", help="Break down by 'template' or 'variant' "
                                            "to see what earns replies."),
) -> None:
    """Show send / reply counts — overall, or per template/variant with --by."""
    if by and by not in ("template", "variant"):
        _err("--by must be 'template' or 'variant'.")
    with _store() as store:
        campaigns = ([store.get_campaign(name)] if name else store.list_campaigns())
        campaigns = [c for c in campaigns if c]
        if not campaigns:
            _err("No campaigns found.")

        if by:
            # Which copy earns replies: group sent messages, attribute a lead's
            # reply to every group that actually reached them.
            groups: dict[str, dict[str, set[int]]] = {}
            for c in campaigns:
                for m in store.messages_for_campaign(c.id):  # type: ignore[arg-type]
                    if m.status != "sent":
                        continue
                    key = (m.template_id if by == "template" else m.variant) or "—"
                    g = groups.setdefault(key, {"sent_to": set(), "replied": set()})
                    g["sent_to"].add(m.lead_id)
                    if store.has_replied(m.lead_id, m.campaign_id):
                        g["replied"].add(m.lead_id)
            if not groups:
                console.print("[yellow]Nothing sent yet.[/]")
                return
            table = Table(title=f"by {by}", show_lines=False)
            for col in (by, "leads reached", "replied", "reply %"):
                table.add_column(col)
            ranked = sorted(groups.items(),
                            key=lambda kv: -(len(kv[1]["replied"]) / max(1, len(kv[1]["sent_to"]))))
            for key, g in ranked:
                n, r = len(g["sent_to"]), len(g["replied"])
                table.add_row(key, str(n), str(r), f"{r / n * 100:.0f}%" if n else "—")
            console.print(table)
            console.print("[dim]Double down on the top row; retire the bottom one.[/]")
            return

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
        cats = store.reply_categories()
    console.print(table)
    if cats:
        pretty = " · ".join(f"{k}: {v}" for k, v in sorted(cats.items()))
        console.print(f"[dim]replies — {pretty}[/]")


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
