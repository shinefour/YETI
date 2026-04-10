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


def _headers() -> dict:
    """Auth headers for API requests."""
    import os

    key = os.environ.get("YETI_DASHBOARD_API_KEY", "")
    if key:
        return {"x-api-key": key}
    return {}


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
        response = httpx.get(f"{_api_url()}/api/status", timeout=5, headers=_headers())
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
        response = httpx.get(f"{_api_url()}/health", timeout=5, headers=_headers())
        data = response.json()
        if data.get("status") == "healthy":
            ver = data.get("version", "?")
            console.print(f"[green]YETI is healthy[/green] (v{ver})")
        else:
            console.print(f"[red]YETI is unhealthy: {data}[/red]")
    except httpx.ConnectError:
        console.print("[red]Cannot connect to YETI API. Is the server running?[/red]")


@app.command()
def actions(
    status: str = typer.Option(
        None, "--status", "-s", help="Filter by status"
    ),
    project: str = typer.Option(
        None, "--project", "-p", help="Filter by project"
    ),
):
    """List action items."""
    try:
        params = {}
        if status:
            params["status"] = status
        if project:
            params["project"] = project
        response = httpx.get(
            f"{_api_url()}/api/tasks",
            params=params,
            timeout=5,
            headers=_headers(),
        )
        items = response.json()

        if not items:
            console.print("[dim]No action items found.[/dim]")
            return

        table = Table(title="Action Items")
        table.add_column("Status", width=15)
        table.add_column("Title")
        table.add_column("Project", style="dim")
        table.add_column("ID", style="dim", width=8)

        for item in items:
            s = item["status"]
            if s == "active":
                color = "green"
            elif s == "pending_review":
                color = "yellow"
            elif s == "completed":
                color = "dim"
            else:
                color = "red"
            table.add_row(
                f"[{color}]{s}[/{color}]",
                item["title"],
                item.get("project", ""),
                item["id"][:8],
            )

        console.print(table)

    except httpx.ConnectError:
        console.print(
            "[red]Cannot connect to YETI API.[/red]"
        )


@app.command(name="add-action")
def add_action(
    title: str = typer.Argument(..., help="Action item title"),
    project: str = typer.Option("", "--project", "-p"),
    source: str = typer.Option("cli", "--source"),
):
    """Create a new action item."""
    try:
        response = httpx.post(
            f"{_api_url()}/api/tasks",
            json={
                "title": title,
                "project": project,
                "source": source,
            },
            timeout=5,
            headers=_headers(),
        )
        item = response.json()
        console.print(
            f"[green]Created:[/green] {item['title']} "
            f"[dim]({item['id'][:8]})[/dim]"
        )
    except httpx.ConnectError:
        console.print(
            "[red]Cannot connect to YETI API.[/red]"
        )


def _send_message(
    message: str, history: list[dict] | None = None
) -> str | None:
    """Send a message to YETI and display the response."""
    try:
        response = httpx.post(
            f"{_api_url()}/api/chat",
            json={"message": message, "history": history or []},
            timeout=60,
            headers=_headers(),
        )
        data = response.json()
        if "error" in data:
            console.print(f"[red]{data['error']}[/red]")
            return None
        reply = data.get("response", "No response")
        console.print(Markdown(reply))
        return reply
    except httpx.ConnectError:
        console.print(
            "[red]Cannot connect to YETI API. "
            "Is the server running?[/red]"
        )
        return None


def _interactive_session():
    """Start an interactive REPL session with YETI."""
    console.print("[bold]YETI Interactive Session[/bold]")
    console.print("Type your message, or 'quit' to exit.\n")

    history: list[dict] = []

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

        reply = _send_message(message, history)
        if reply:
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": reply})
        console.print()


if __name__ == "__main__":
    app()
