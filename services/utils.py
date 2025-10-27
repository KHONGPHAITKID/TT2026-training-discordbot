import logging
import os
from typing import Dict, Iterable

from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)


def load_environment(dotenv_path: str = ".env") -> None:
    """Load environment variables from a .env file if present."""
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
        LOGGER.info("Environment variables loaded from %s", dotenv_path)
    else:
        LOGGER.info(".env file not found at %s; relying on process environment.", dotenv_path)


def format_options(options: Dict[str, str]) -> str:
    """Return the options dictionary as neatly formatted text."""
    lines = [f"{key}: {value}" for key, value in options.items()]
    return "\n".join(lines)


def normalise_answer(answer: str) -> str:
    """Convert user supplied answer tokens into canonical A/B/C/D format."""
    token = answer.strip().upper()
    if token in ("A", "B", "C", "D"):
        return token
    if len(token) > 1 and token[0] in ("A", "B", "C", "D"):
        if token[1] in {")", ".", " ", "-"}:
            return token[0]
    if token.startswith("OPTION "):
        token = token.replace("OPTION ", "")
    if token.startswith("CHOICE "):
        token = token.replace("CHOICE ", "")
    if token in ("1", "2", "3", "4"):
        # map numeric to letter
        return chr(ord("A") + int(token) - 1)
    return token


def chunk(iterable: Iterable, size: int):
    """Yield successive chunks from an iterable."""
    chunk_buffer = []
    for item in iterable:
        chunk_buffer.append(item)
        if len(chunk_buffer) == size:
            yield chunk_buffer
            chunk_buffer = []
    if chunk_buffer:
        yield chunk_buffer
