"""YETI CLI — terminal interface to the YETI system."""

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.table import Table

app = typer.Typer(name="yeti", help="Your Everyday Task Intelligence")
console = Console()

DEFAULT_API_URL = "http://localhost:8000"


def _api_url() -> str:
    """Resolve the YETI API base URL."""
    import os

    return os.environ.get("YETI_API_URL", DEFAULT_API_URL)


@app.command()
def chat(message: str = typer.Argument(None, help="Message to send to YETI")):
    """Chat with YETI. If no message is provided, starts an interactive session."""
    if message:
        _send_message(message)
    else:
        _interactive_session()


@app.command()
def status():
    """Show YETI system status."""
    try:
        response = httpx.get(f"{_api_url()}/api/status", timeout=5)
        data = response.json()

        table = Table(title="YETI System Status")
        table.add_column("Component", style="bold")
        table.add_column("Status")

        for service, state in data.get("services", {}).items():
            if state == "up":
                color = "green"
            elif state == "unknown":
                color = "yellow"
            else:
                color = "red"
            table.add_row(service, f"[{color}]{state}[/{color}]")

        console.print(table)

        int_table = Table(title="Integrations")
        int_table.add_column("Integration", style="bold")
        int_table.add_column("Status")

        for integration, state in data.get("integrations", {}).items():
            color = "green" if state == "connected" else "dim"
            int_table.add_row(integration, f"[{color}]{state}[/{color}]")

        console.print(int_table)

    except httpx.ConnectError:
        console.print("[red]Cannot connect to YETI API. Is the server running?[/red]")


@app.command()
def health():
    """Check if YETI is healthy."""
    try:
        response = httpx.get(f"{_api_url()}/health", timeout=5)
        data = response.json()
        if data.get("status") == "healthy":
            ver = data.get("version", "?")
            console.print(f"[green]YETI is healthy[/green] (v{ver})")
        else:
            console.print(f"[red]YETI is unhealthy: {data}[/red]")
    except httpx.ConnectError:
        console.print("[red]Cannot connect to YETI API. Is the server running?[/red]")


def _send_message(message: str):
    """Send a single message to YETI and display the response."""
    try:
        response = httpx.post(
            f"{_api_url()}/api/chat",
            json={"message": message},
            timeout=30,
        )
        data = response.json()
        reply = data.get("response", "No response")
        console.print(Markdown(reply))
    except httpx.ConnectError:
        console.print("[red]Cannot connect to YETI API. Is the server running?[/red]")


def _interactive_session():
    """Start an interactive REPL session with YETI."""
    console.print("[bold]YETI Interactive Session[/bold]")
    console.print("Type your message, or 'quit' to exit.\n")

    while True:
        try:
            message = Prompt.ask("[bold cyan]yeti[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\nBye!")
            break

        if message.lower() in ("quit", "exit", "q"):
            console.print("Bye!")
            break

        if not message.strip():
            continue

        _send_message(message)
        console.print()


if __name__ == "__main__":
    app()
