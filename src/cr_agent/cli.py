"""
CLI entry point — `cr-agent review` command.

Phase 1: single diff file → JSON/markdown output.
Phase 2+: GitHub PR URL support, config file overrides.
"""

import asyncio
import sys
from pathlib import Path

import click
import structlog

from cr_agent import __version__
from cr_agent.agents import SecurityAgent
from cr_agent.core import ReviewOrchestrator
from cr_agent.llm import LLMClient

logger = structlog.get_logger("cli")


@click.group()
@click.version_option(__version__, prog_name="cr-agent")
def main():
    """CR-Agent: Multi-agent code review from the command line."""
    pass


@main.command()
@click.option(
    "--diff",
    "-d",
    "diff_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to a .patch or .diff file to review.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["json", "markdown", "md"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(),
    default=None,
    help="Write output to file (default: stdout).",
)
@click.option(
    "--threshold",
    "-t",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="Confidence threshold override (0.0-1.0).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress log output (only print the result).",
)
def review(
    diff_path: str,
    output_format: str,
    output_path: str | None,
    threshold: float | None,
    quiet: bool,
):
    """
    Review a diff/patch file for security, logic, and performance issues.

    \b
    Examples:
      cr-agent review -d changes.patch
      cr-agent review -d changes.patch -f markdown -o report.md
    """
    if not quiet:
        _configure_logging()

    diff_text = _read_file(diff_path)
    if not diff_text.strip():
        click.echo("Error: diff file is empty.", err=True)
        sys.exit(1)

    click.echo(f"Reviewing {diff_path}...", err=True)

    # Wire up the pipeline
    llm = LLMClient()
    agent = SecurityAgent(llm=llm)
    orchestrator = ReviewOrchestrator(
        agents=[agent],
        confidence_threshold=threshold or 0.7,
    )

    report = asyncio.run(orchestrator.run(diff_text))

    # Output
    if output_format in ("json",):
        result = report.to_json()
    else:
        result = report.to_markdown()

    if output_path:
        Path(output_path).write_text(result, encoding="utf-8")
        click.echo(f"Report written to {output_path}", err=True)
    else:
        # Write raw bytes to avoid Windows GBK encoding issues
        sys.stdout.buffer.write(result.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()

    # Summary to stderr
    click.echo(
        f"\nDone: {report.total_findings} findings | "
        f"{report.duration_ms}ms | "
        f"${report.cost_usd:.6f} | "
        f"{report.tokens_in}/{report.tokens_out} tokens",
        err=True,
    )

    # Non-zero exit if critical findings
    critical_count = sum(1 for f in report.findings if f.severity == "critical")
    if critical_count:
        click.echo(f"WARNING: {critical_count} critical issues found!", err=True)


def _read_file(path: str) -> str:
    """Read a file, trying common encodings (handles PowerShell UTF-16 output)."""
    raw = Path(path).read_bytes()
    # UTF-16 BOM (PowerShell default on Windows)
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be")
    # UTF-8 with BOM
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    # Try UTF-8, fall back to system locale
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return raw.decode(locale.getpreferredencoding())


def _configure_logging():
    """Set up minimal structured logging for CLI output."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )


if __name__ == "__main__":
    main()
