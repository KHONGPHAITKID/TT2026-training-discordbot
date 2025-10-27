import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402  (import after backend set)
import matplotlib.pyplot as plt  # noqa: E402

from services import db

LOGGER = logging.getLogger(__name__)

CHARTS_DIR = Path("data") / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def render_leaderboard_chart(leaderboard: List[dict]) -> Optional[Path]:
    """Render a horizontal bar chart for top users."""
    if not leaderboard:
        return None

    names = [entry["name"] for entry in leaderboard]
    scores = [entry["score"] for entry in leaderboard]
    positions = range(len(names))

    fig, ax = plt.subplots(figsize=(6, 0.6 * len(names) + 1))
    ax.barh(positions, scores, color="#4C72B0")
    ax.set_yticks(positions)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlabel("Score")
    ax.set_title("Leaderboard")
    for index, score in enumerate(scores):
        ax.text(score + 1, index, str(score), va="center")

    path = CHARTS_DIR / "leaderboard.png"
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def render_user_history_chart(user_id: int, username: str) -> Optional[Path]:
    """Render a cumulative score chart for an individual user."""
    history = db.iter_user_history(user_id)
    if not history:
        return None

    dates: List[datetime] = []
    cumulative_scores: List[int] = []
    score = 0
    for response in reversed(history):  # chronological order
        if response.is_correct:
            score += 10
        dates.append(response.answered_at)
        cumulative_scores.append(score)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(dates, cumulative_scores, marker="o", color="#55A868")
    ax.set_title(f"Performance Trend · {username}")
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative Score")
    ax.grid(True, which="both", axis="y", linestyle="--", linewidth=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate()

    path = CHARTS_DIR / f"user_{user_id}_history.png"
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path
