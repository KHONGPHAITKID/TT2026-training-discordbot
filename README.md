# Daily Computer Science Quiz Bot

An educational Discord bot that delivers daily multiple-choice computer science questions, tracks answers, awards points, and shares visual performance summaries. Questions are generated dynamically through the Groq LLM API with a local fallback library for offline development.

## Features
- Automated daily question posting (configurable schedule and channel).
- Manual `/question` command to request fresh prompts on demand.
- `/tt` shortcut panel with buttons for common quiz actions.
- Message-based answers (A/B/C/D) with first-correct-wins scoring.
- Persistent leaderboard and per-user stats stored in SQLite via SQLAlchemy.
- Rich personal & global stat visualisations (accuracy, topic mastery, consistency leaders).
- Admin utilities to set quiz channels, assign admin roles, reset scores, and force new questions.

## Project Layout
```
bot.py                 # Bot entrypoint
cogs/                  # Discord command and event handlers
services/              # Database, Groq API, charting utilities
data/                  # SQLite database and generated chart assets
```

## Getting Started
1. **Install dependencies**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Create a `.env` file**
   ```ini
   DISCORD_TOKEN=your_discord_bot_token
   GROQ_API_KEY=optional_groq_token   # omit to rely on fallback questions
   DAILY_QUESTION_CRON=0 0 * * *      # optional cron expression (default is 07:00 UTC+7)
   BOT_TIMEZONE=Asia/Bangkok          # optional timezone for scheduler (default UTC+7)
   ```

3. **Run the bot**
   ```bash
   python bot.py
   ```

## Commands Overview
- `/question [topic]` (alias `/q`) - post a fresh question immediately.
- Tap the answer buttons (or type `A/B/C/D`) to respond; first correct reply wins and ends the round.
- `/show_question` - re-display the latest question embed.
- `/leaderboard` - show the top scorers (includes chart).
- `/leaderboard` now also surfaces accuracy leaders and topic specialists.
- `/stats [member]` - display stats and history for a user.
- `/ans` - reveal the most recent question's answer and explanation.
- `/recent_questions` - list the latest topics asked.
- `/tt` - open quick-action buttons for new question, stats, leaderboard.
- Admin-only: `/set_daily_channel`, `/set_admin_role`, `/reset_scores`.

## Development Notes
- SQLite is used by default; set `DATABASE_URL` for PostgreSQL or another backend.
- Matplotlib charts save to `data/charts/`. The bot automatically attaches these images when available.
- The Groq client requests JSON-formatted MCQs. If the API key is absent or a request fails, curated fallback questions are posted instead.

## Roadmap Ideas
- Emoji reaction answering workflow.
- Difficulty levels and topic filters.
- Weekly summary posts and external dashboard integration.

