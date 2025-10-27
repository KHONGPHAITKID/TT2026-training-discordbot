import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402  (import after backend set)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from services import db

LOGGER = logging.getLogger(__name__)

CHARTS_DIR = Path("data") / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

plt.style.use("seaborn-v0_8")


def _save_fig(fig: plt.Figure, filename: str) -> Path:
    path = CHARTS_DIR / filename
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def render_leaderboard_chart(leaderboard: List[dict]) -> Optional[Path]:
    """Render a horizontal bar chart for top users."""
    if not leaderboard:
        return None

    names = [entry["name"] for entry in leaderboard]
    scores = [entry["score"] for entry in leaderboard]
    positions = np.arange(len(names))

    fig, ax = plt.subplots(figsize=(7, 0.65 * len(names) + 1.5))
    bars = ax.barh(positions, scores, color="#4776E6", edgecolor="#1E3C72")
    ax.set_yticks(positions)
    ax.set_yticklabels(names, fontsize=11)
    ax.invert_yaxis()
    ax.set_xlabel("Score", fontsize=11)
    ax.set_title("Global Leaderboard", fontsize=14, weight="bold")
    ax.grid(axis="x", linestyle="--", alpha=0.3)

    for bar, value in zip(bars, scores):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2, str(value), va="center", fontsize=10)

    return _save_fig(fig, "leaderboard.png")


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

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(dates, cumulative_scores, marker="o", color="#00B09B", linewidth=2)
    ax.fill_between(dates, cumulative_scores, color="#00B09B", alpha=0.15)
    ax.set_title(f"Performance Trend - {username}", fontsize=14, weight="bold")
    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Cumulative Score", fontsize=11)
    ax.grid(True, which="both", axis="y", linestyle="--", linewidth=0.6, alpha=0.4)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate()

    return _save_fig(fig, f"user_{user_id}_history.png")


def render_user_accuracy_chart(username: str, correct: int, incorrect: int) -> Optional[Path]:
    total = correct + incorrect
    if total == 0:
        return None

    fig, ax = plt.subplots(figsize=(4, 4))
    _wedges, _labels, _autotexts = ax.pie(
        [correct, incorrect],
        labels=["Correct", "Incorrect"],
        colors=["#16C172", "#FF6B6B"],
        autopct="%1.0f%%",
        startangle=140,
        wedgeprops={"linewidth": 1.5, "edgecolor": "white"},
    )
    ax.set_title(f"Answer Accuracy - {username}", fontsize=12, weight="bold")
    centre_circle = plt.Circle((0, 0), 0.60, fc="white")
    fig.gca().add_artist(centre_circle)
    ax.text(0, 0, f"{(correct/total)*100:.1f}%", ha="center", va="center", fontsize=14, weight="bold")

    return _save_fig(fig, f"user_{username}_accuracy.png")


def render_user_topic_breakdown(username: str, topics: Iterable[Dict[str, object]]) -> Optional[Path]:
    topics_list = list(topics)
    if not topics_list:
        return None

    topics_list.sort(key=lambda item: item.get("attempts", 0), reverse=True)
    labels = [item["topic"] for item in topics_list]
    correct_values = np.array([item.get("correct", 0) for item in topics_list], dtype=float)
    incorrect_values = np.array([item.get("attempts", 0) - item.get("correct", 0) for item in topics_list], dtype=float)
    positions = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(7, 0.5 * len(labels) + 2))
    ax.barh(positions, correct_values, color="#4CAF50", label="Correct")
    ax.barh(positions, incorrect_values, left=correct_values, color="#E57373", label="Incorrect", alpha=0.8)

    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Answers", fontsize=11)
    ax.set_title(f"Topic Breakdown - {username}", fontsize=14, weight="bold")
    ax.legend(frameon=False, loc="lower right")

    for idx, (c_val, i_val) in enumerate(zip(correct_values, incorrect_values)):
        total = c_val + i_val
        ax.text(
            total + 0.1,
            idx,
            f"{int(total)}",
            va="center",
            fontsize=9,
        )

    return _save_fig(fig, f"user_{username}_topics.png")


def render_accuracy_leaders_chart(entries: List[Dict[str, object]]) -> Optional[Path]:
    if not entries:
        return None

    labels = [entry["user_label"] for entry in entries]
    accuracy = [entry["accuracy"] * 100 for entry in entries]
    attempts = [entry["attempts"] for entry in entries]
    positions = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(7, 0.55 * len(labels) + 2))
    bars = ax.barh(positions, accuracy, color="#FFB347")
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Accuracy (%)", fontsize=11)
    ax.set_xlim(0, 100)
    ax.set_title("Accuracy Leaders", fontsize=14, weight="bold")
    ax.grid(axis="x", linestyle="--", alpha=0.3)

    for bar, value, attempt_count in zip(bars, accuracy, attempts):
        ax.text(
            value + 1,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.1f}% ({attempt_count} answers)",
            va="center",
            fontsize=9,
        )

    return _save_fig(fig, "accuracy_leaders.png")


def render_topic_leaders_chart(entries: List[Dict[str, object]]) -> Optional[Path]:
    if not entries:
        return None

    topics = [entry["topic"] for entry in entries]
    scores = [entry["correct"] for entry in entries]
    labels = [entry["user_label"] for entry in entries]
    positions = np.arange(len(topics))

    fig, ax = plt.subplots(figsize=(7, 0.6 * len(topics) + 2))
    bars = ax.barh(positions, scores, color="#8E54E9")
    ax.set_yticks(positions)
    ax.set_yticklabels(topics, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Correct Answers", fontsize=11)
    ax.set_title("Topic Specialists", fontsize=14, weight="bold")

    for bar, label, score in zip(bars, labels, scores):
        ax.text(
            bar.get_width() + 0.3,
            bar.get_y() + bar.get_height() / 2,
            f"{label} ({score})",
            va="center",
            fontsize=9,
        )

    return _save_fig(fig, "topic_leaders.png")
