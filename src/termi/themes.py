"""Rich theme definitions for Termi TUI."""
from rich.theme import Theme

THEMES = {
    "monokai": Theme({
        "termi.prompt": "bold green",
        "termi.command": "bold cyan",
        "termi.warning": "bold yellow",
        "termi.error": "bold red",
        "termi.success": "bold green",
        "termi.info": "dim white",
        "termi.highlight": "bold magenta",
        "termi.muted": "dim",
        "termi.header": "bold underline cyan",
        "termi.step": "bold yellow",
        "termi.thought": "italic dim",
    }),
    "dracula": Theme({
        "termi.prompt": "bold #50fa7b",
        "termi.command": "bold #8be9fd",
        "termi.warning": "bold #f1fa8c",
        "termi.error": "bold #ff5555",
        "termi.success": "bold #50fa7b",
        "termi.info": "#6272a4",
        "termi.highlight": "bold #bd93f9",
        "termi.muted": "#6272a4",
        "termi.header": "bold underline #ff79c6",
        "termi.step": "bold #ffb86c",
        "termi.thought": "italic #6272a4",
    }),
    "minimal": Theme({
        "termi.prompt": "bold white",
        "termi.command": "bold white",
        "termi.warning": "yellow",
        "termi.error": "red",
        "termi.success": "green",
        "termi.info": "dim",
        "termi.highlight": "bold",
        "termi.muted": "dim",
        "termi.header": "bold underline",
        "termi.step": "bold",
        "termi.thought": "italic dim",
    }),
}


def get_theme(name: str) -> Theme:
    return THEMES.get(name, THEMES["monokai"])
