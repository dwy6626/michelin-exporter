"""CLI entrypoint for Michelin scraper Google Maps sync workflow."""

from typing import Annotated

import typer

from ..application.maps_login_use_case import run_maps_login
from ..application.sync_models import MapsLoginCommand, ScrapeSyncCommand
from ..application.sync_use_case import AUTH_REQUIRED_EXIT_CODE, run_scrape_sync
from ..catalog import (
    LANGUAGE_VALUES_HELP,
    LEVEL_CHOICES_HELP,
    TARGET_VALUES_HELP,
    parse_level_selection,
    resolve_language,
)
from ..config import (
    CA_BUNDLE_OPTION_FLAGS,
    CRAWL_DELAY_OPTION_FLAGS,
    DEBUG_SYNC_FAILURES_OPTION_FLAGS,
    DEFAULT_GOOGLE_USER_DATA_DIR,
    DEFAULT_LANGUAGE,
    DEFAULT_LIST_NAME_PREFIX,
    DEFAULT_LIST_NAME_TEMPLATE,
    DEFAULT_LOGIN_TIMEOUT_SECONDS,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_ROWS_PER_PAGE,
    DEFAULT_MAX_SAVE_RETRIES,
    DEFAULT_MISSING_LIST_POLICY,
    DEFAULT_RECORD_FIXTURES_DIR,
    DEFAULT_SLEEP_SECONDS,
    DEFAULT_SYNC_DELAY_SECONDS,
    DRY_RUN_OPTION_FLAGS,
    GOOGLE_USER_DATA_DIR_OPTION_FLAGS,
    HEADED_OPTION_FLAGS,
    HELP_OPTION_NAMES,
    IGNORE_CHECKPOINT_OPTION_FLAGS,
    IGNORE_EXISTING_LISTS_CHECK_OPTION_FLAGS,
    INSECURE_OPTION_FLAGS,
    LANGUAGE_LIST_NAME_TEMPLATE_OVERRIDES,
    LANGUAGE_OPTION_FLAGS,
    LEVELS_OPTION_FLAGS,
    LIST_NAME_PREFIX_OPTION_FLAGS,
    LIST_NAME_TEMPLATE_OPTION_FLAGS,
    LIST_NAME_TEMPLATE_PLACEHOLDERS,
    LOGIN_TIMEOUT_OPTION_FLAGS,
    MAPS_DELAY_OPTION_FLAGS,
    MAPS_PROBE_ONLY_OPTION_FLAGS,
    MAPS_PROBE_ROWS_FILE_OPTION_FLAGS,
    MAX_PAGES_OPTION_FLAGS,
    MAX_ROWS_PER_PAGE_OPTION_FLAGS,
    MAX_SAVE_RETRIES_OPTION_FLAGS,
    MISSING_LIST_POLICY_CHOICES,
    ON_MISSING_LIST_OPTION_FLAGS,
    RECORD_FIXTURES_DIR_OPTION_FLAGS,
    SANDBOX_DEFAULT_MAX_PAGES,
    SANDBOX_LIST_NAME_PREFIX,
    SANDBOX_OPTION_FLAGS,
    STATE_DIR_OPTION_FLAGS,
    TARGET_OPTION_FLAGS,
)
from ..output import ConsoleSyncPresenter

MISSING_LIST_POLICY_HELP = ", ".join(MISSING_LIST_POLICY_CHOICES)
LIST_NAME_TEMPLATE_PLACEHOLDERS_HELP = ", ".join(LIST_NAME_TEMPLATE_PLACEHOLDERS)
USER_OPTIONS_HELP_PANEL = "User Options"
DEVELOPER_DEBUG_OPTIONS_HELP_PANEL = "Developer and Debug Options"

APP_HELP_TEXT = (
    "Scrape Michelin listings and sync restaurants into Google Maps lists.\n\n"
    "Quick Start:\n"
    "- Install dependencies and browser runtime:\n"
    "  uv sync\n"
    "  uv run playwright install chromium\n"
    "- Run sync:\n"
    f"  python -m michelin_scraper {TARGET_OPTION_FLAGS[0]} <TARGET>\n"
    f"- Default Google profile path: {DEFAULT_GOOGLE_USER_DATA_DIR}\n"
    "- Default state directory: .michelin-state/ (git-ignored, created in current working directory).\n"
    "- Login handling:\n"
    "  The command checks Google login state automatically.\n"
    "  If not logged in, it prompts to open a browser for interactive login,\n"
    "  then retries sync automatically.\n"
    "- If sign-in shows 'This browser or app may not be secure':\n"
    "  install or update Google Chrome and retry.\n"
    "  If still blocked, login once in regular Chrome with the same profile path,\n"
    "  then rerun this command.\n"
    "- Troubleshooting (browser starts then closes with SIGTRAP / TargetClosed):\n"
    "  close all Chrome windows and retry with a fresh profile path, for example:\n"
    f"    {GOOGLE_USER_DATA_DIR_OPTION_FLAGS[0]} ~/.michelin-gmaps-profile-fresh\n"
    "  The tool auto-cleans stale profile lock/session files at startup.\n"
    "  If crash persists, remove the profile directory and login again.\n"
    "- Troubleshooting (run stops with 'List already exists at startup'):\n"
    f"  rerun with {IGNORE_EXISTING_LISTS_CHECK_OPTION_FLAGS[0]} to reuse existing lists.\n"
    "  In that mode, rows already saved in target lists are skipped without modifying list membership.\n"
    "  Without that flag, detecting an already-saved row is treated as a hard error.\n"
    "- Safe workflow for small-batch debugging (no list writes):\n"
    f"  combine {MAPS_PROBE_ONLY_OPTION_FLAGS[0]} with "
    f"{MAX_PAGES_OPTION_FLAGS[0]} / {MAX_ROWS_PER_PAGE_OPTION_FLAGS[0]}.\n"
    "- Fast Maps-only probe from local rows (skip Michelin crawl):\n"
    f"  use {MAPS_PROBE_ROWS_FILE_OPTION_FLAGS[0]} <ROWS.jsonl>.\n"
    "  JSONL rows require 'Name'; optional fields: City, Address, Cuisine, Description, "
    "Rating, LevelSlug, Latitude, Longitude.\n"
    "- Safe live end-to-end validation in Google Maps:\n"
    f"  use {SANDBOX_OPTION_FLAGS[0]} to force test-list prefixing, reuse checks, "
    f"and a {SANDBOX_DEFAULT_MAX_PAGES}-page default crawl limit.\n"
    "- Automatically record de-identified debug snapshots as reusable fixtures:\n"
    f"  set {RECORD_FIXTURES_DIR_OPTION_FLAGS[0]} <DIR>.\n"
    f"- Sync failure policy ({ON_MISSING_LIST_OPTION_FLAGS[0]}):\n"
    "  stop = fail immediately on first row failure (including missing-list failures).\n"
    "  continue = keep processing rows and aggregate failures.\n"
    "- Troubleshooting (Playwright browser executable missing):\n"
    "  uv sync\n"
    "  uv run playwright install chromium\n"
    "  If still failing:\n"
    "    rm -rf ~/Library/Caches/ms-playwright\n"
    "    uv run playwright install chromium\n"
    f"- If login takes too long, increase timeout with "
    f"{LOGIN_TIMEOUT_OPTION_FLAGS[0]} (example: {LOGIN_TIMEOUT_OPTION_FLAGS[0]} 600).\n\n"
    "Option Sections:\n"
    f"- {USER_OPTIONS_HELP_PANEL}: normal day-to-day sync inputs and behavior controls.\n"
    f"- {DEVELOPER_DEBUG_OPTIONS_HELP_PANEL}: troubleshooting, probes, and developer diagnostics.\n\n"
    "Canonical CLI usage documentation is in this help output."
)

app = typer.Typer(
    add_completion=False,
    help=APP_HELP_TEXT,
    context_settings={"help_option_names": list(HELP_OPTION_NAMES)},
)


@app.command(help=APP_HELP_TEXT)
def main(
    target: Annotated[
        str,
        typer.Option(
            *TARGET_OPTION_FLAGS,
            help=(
                "Required target (country or predefined city). "
                f"{TARGET_VALUES_HELP} "
                "When the same value exists in both lists, the city target is used."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ],
    google_user_data_dir: Annotated[
        str,
        typer.Option(
            *GOOGLE_USER_DATA_DIR_OPTION_FLAGS,
            help=(
                "Persistent Chrome profile path used to store Google login session. "
                f"Default: {DEFAULT_GOOGLE_USER_DATA_DIR}."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_GOOGLE_USER_DATA_DIR,
    language: Annotated[
        str,
        typer.Option(
            *LANGUAGE_OPTION_FLAGS,
            help=(
                "Language segment in Michelin Guide URLs. "
                f"{LANGUAGE_VALUES_HELP} "
                "When language resolves to zh-tw, list scope names prefer Traditional Chinese labels."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_LANGUAGE,
    levels: Annotated[
        str,
        typer.Option(
            *LEVELS_OPTION_FLAGS,
            help=(
                "Comma-separated level slugs to sync. "
                f"Supported values: {LEVEL_CHOICES_HELP}. "
                "Empty value means default buckets: stars, selected, bib-gourmand. "
                "Use one-star,two-star,three-star to keep star lists separate."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = "",
    state_dir: Annotated[
        str,
        typer.Option(
            *STATE_DIR_OPTION_FLAGS,
            help=(
                "Directory for checkpoint, sync journal, and failure reports. "
                "Default: .michelin-state/ in the current working directory (git-ignored)."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = "",
    ignore_checkpoint: Annotated[
        bool,
        typer.Option(
            *IGNORE_CHECKPOINT_OPTION_FLAGS,
            help="Ignore checkpoint and start scraping from the first page.",
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    debug_sync_failures: Annotated[
        bool,
        typer.Option(
            *DEBUG_SYNC_FAILURES_OPTION_FLAGS,
            help=(
                "Emit per-page sync failure debug details (reason counts and sample queries), "
                "and persist de-identified debug HTML snapshots."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    max_pages: Annotated[
        int | None,
        typer.Option(
            *MAX_PAGES_OPTION_FLAGS,
            help=(
                "Maximum Michelin listing pages to scrape this run. "
                "Use 0 for no limit. "
                "When omitted, default is no limit; "
                f"sandbox mode overrides omitted value to {SANDBOX_DEFAULT_MAX_PAGES}."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = None,
    max_rows_per_page: Annotated[
        int,
        typer.Option(
            *MAX_ROWS_PER_PAGE_OPTION_FLAGS,
            help=(
                "Maximum rows per scraped page to send into Maps sync. "
                "Use 0 for no limit."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_MAX_ROWS_PER_PAGE,
    headed: Annotated[
        bool,
        typer.Option(
            *HEADED_OPTION_FLAGS,
            help="Run browser in headed mode. Use --headless for background execution.",
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = True,
    crawl_delay: Annotated[
        float,
        typer.Option(
            *CRAWL_DELAY_OPTION_FLAGS,
            help=(
                "Delay between Michelin crawl requests in seconds. "
                "Applied between restaurant detail items and between listing pages."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_SLEEP_SECONDS,
    maps_delay: Annotated[
        float,
        typer.Option(
            *MAPS_DELAY_OPTION_FLAGS,
            help="Delay between Google Maps sync UI operations in seconds.",
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_SYNC_DELAY_SECONDS,
    max_save_retries: Annotated[
        int,
        typer.Option(
            *MAX_SAVE_RETRIES_OPTION_FLAGS,
            help="Maximum retry attempts for transient Google Maps save failures.",
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_MAX_SAVE_RETRIES,
    dry_run: Annotated[
        bool,
        typer.Option(
            *DRY_RUN_OPTION_FLAGS,
            help="Scrape and route rows by level without writing to Google Maps.",
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    sandbox: Annotated[
        bool,
        typer.Option(
            *SANDBOX_OPTION_FLAGS,
            help=(
                f"Safe live validation mode. Forces list prefix to '{SANDBOX_LIST_NAME_PREFIX}', "
                "forces existing-list reuse checks on, and defaults max-pages to "
                f"{SANDBOX_DEFAULT_MAX_PAGES} unless provided."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    maps_probe_only: Annotated[
        bool,
        typer.Option(
            *MAPS_PROBE_ONLY_OPTION_FLAGS,
            help=(
                "Run real Google Maps search/match checks without creating lists "
                "or saving places."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    maps_probe_rows_file: Annotated[
        str,
        typer.Option(
            *MAPS_PROBE_ROWS_FILE_OPTION_FLAGS,
            help=(
                "Path to JSONL rows for direct Maps probe mode (skip Michelin crawl). "
                "One JSON object per line. "
                "Required field: Name. Optional fields: City, Address, Cuisine, Description, "
                "Rating, LevelSlug, Latitude, Longitude."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = "",
    record_fixtures_dir: Annotated[
        str,
        typer.Option(
            *RECORD_FIXTURES_DIR_OPTION_FLAGS,
            help=(
                "Optional directory that receives de-identified fixture HTML + metadata "
                "for every captured debug sync snapshot."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_RECORD_FIXTURES_DIR,
    list_name_prefix: Annotated[
        str,
        typer.Option(
            *LIST_NAME_PREFIX_OPTION_FLAGS,
            help="Prefix prepended to generated list names.",
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_LIST_NAME_PREFIX,
    list_name_template: Annotated[
        str,
        typer.Option(
            *LIST_NAME_TEMPLATE_OPTION_FLAGS,
            help=(
                "List naming template using placeholders: "
                f"{LIST_NAME_TEMPLATE_PLACEHOLDERS_HELP}. "
                "Use {level_badge} for the default list suffixes, {level_label} for readable text, "
                "and {level_slug} (or {level}) for slugs."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_LIST_NAME_TEMPLATE,
    on_missing_list: Annotated[
        str,
        typer.Option(
            *ON_MISSING_LIST_OPTION_FLAGS,
            help=(
                "Failure policy for row sync errors and missing-list events. "
                "Use 'stop' to fail immediately on first row failure; "
                "use 'continue' to keep processing rows and aggregate failures. "
                f"Allowed values: {MISSING_LIST_POLICY_HELP}."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_MISSING_LIST_POLICY,
    ignore_existing_lists_check: Annotated[
        bool,
        typer.Option(
            *IGNORE_EXISTING_LISTS_CHECK_OPTION_FLAGS,
            help=(
                "Skip startup failure when generated Google Maps lists already exist. "
                "Existing lists are reused."
            ),
            rich_help_panel=USER_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    ca_bundle: Annotated[
        str,
        typer.Option(
            *CA_BUNDLE_OPTION_FLAGS,
            help="Path to custom CA bundle PEM file for Michelin HTTPS requests.",
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = "",
    insecure: Annotated[
        bool,
        typer.Option(
            *INSECURE_OPTION_FLAGS,
            help="Disable HTTPS certificate verification for Michelin requests.",
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = False,
    login_timeout: Annotated[
        int,
        typer.Option(
            *LOGIN_TIMEOUT_OPTION_FLAGS,
            help=(
                "Timeout in seconds for prompted interactive login when "
                "no authenticated session is detected."
            ),
            rich_help_panel=DEVELOPER_DEBUG_OPTIONS_HELP_PANEL,
        ),
    ] = DEFAULT_LOGIN_TIMEOUT_SECONDS,
) -> None:
    try:
        selected_levels = parse_level_selection(levels)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    effective_language = resolve_language(language)
    effective_list_name_template = list_name_template
    default_template_override = LANGUAGE_LIST_NAME_TEMPLATE_OVERRIDES.get(effective_language)
    if default_template_override and list_name_template == DEFAULT_LIST_NAME_TEMPLATE:
        effective_list_name_template = default_template_override

    command = ScrapeSyncCommand(
        target=target,
        google_user_data_dir=google_user_data_dir,
        levels=selected_levels,
        language=effective_language,
        state_dir=state_dir,
        ignore_checkpoint=ignore_checkpoint,
        debug_sync_failures=debug_sync_failures,
        max_pages=DEFAULT_MAX_PAGES if max_pages is None else max_pages,
        max_pages_specified=max_pages is not None,
        max_rows_per_page=max_rows_per_page,
        sleep_seconds=crawl_delay,
        sync_delay_seconds=maps_delay,
        max_save_retries=max_save_retries,
        headless=not headed,
        dry_run=dry_run,
        sandbox=sandbox,
        maps_probe_only=maps_probe_only,
        maps_probe_rows_file=maps_probe_rows_file,
        record_fixtures_dir=record_fixtures_dir,
        list_name_prefix=list_name_prefix,
        list_name_template=effective_list_name_template,
        on_missing_list=on_missing_list,
        ignore_existing_lists_check=ignore_existing_lists_check,
        ca_bundle=ca_bundle,
        insecure=insecure,
    )
    presenter = ConsoleSyncPresenter()
    exit_code = run_scrape_sync(command=command, output=presenter)
    if exit_code != AUTH_REQUIRED_EXIT_CODE:
        raise typer.Exit(exit_code)

    should_login_now = typer.confirm(
        (
            "No authenticated Google Maps session was found. "
            "Open browser and login now?"
        ),
        default=True,
    )
    if not should_login_now:
        presenter.show_failure("Google login is required before sync can continue.")
        raise typer.Exit(AUTH_REQUIRED_EXIT_CODE)

    login_command = MapsLoginCommand(
        google_user_data_dir=google_user_data_dir,
        login_timeout_seconds=login_timeout,
        headless=not headed,
    )
    login_exit_code = run_maps_login(command=login_command, output=presenter)
    if login_exit_code != 0:
        raise typer.Exit(login_exit_code)

    raise typer.Exit(run_scrape_sync(command=command, output=presenter))
