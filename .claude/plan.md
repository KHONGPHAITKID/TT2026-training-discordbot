# 🧠 Discord Bot Project: “Daily Computer Science Quizmaster”

A **Discord bot** that automatically generates and manages **daily computer science multiple-choice questions**, powered by **LLMs (Groq API)**.  
The bot engages users by tracking scores, showing leaderboards, generating statistical charts, and allowing manual requests for new questions.  
All questions are domain-focused across the **entire Computer Science curriculum**.

---

## 🚀 1. Project Overview

### 🎯 Goal
To build an intelligent **Discord bot** that:
- Posts **daily computer science MCQs**.
- Allows **manual requests** for new questions via commands.
- Uses a **Large Language Model (LLM)** from the **Groq platform** to dynamically generate domain-specific questions.
- Tracks **user performance** and **displays stats** (charts, leaderboards, rankings).
- Provides a rich **interactive and educational experience** for computer science enthusiasts.

---

## 🧩 2. Core Features

| Feature | Description |
|----------|-------------|
| **Daily Question Broadcast** | Automatically posts one question per day to a designated channel. |
| **Manual Question Request** | Users can trigger a new question using `!question` or `/question new`. |
| **LLM-Generated Questions** | Questions are dynamically generated using **Groq API**, covering predefined CS domains. |
| **Answer Submission** | Users can answer via message command or emoji reactions (A/B/C/D). |
| **Answer Evaluation** | Bot evaluates correctness and awards points. |
| **Leaderboard System** | Tracks and ranks users based on correct answers. |
| **Charts & Visualizations** | Generates performance charts (weekly stats, accuracy rates, top users). |
| **Topic Coverage** | Randomly samples topics across 20+ computer science domains (see below). |
| **Persistent Storage** | Stores user data, questions, and history in a local SQLite database or PostgreSQL. |
| **Scheduled Jobs** | Uses APScheduler for daily execution at specific times. |
| **Admin Tools** | Commands to set channels, reset scores, force new questions, or view logs. |

---

## 🧠 3. Supported Topics

Each generated question will belong to one of these fields:

1. Algorithms & Data Structures  
2. Programming Languages (focus: **C++**)  
3. Object-Oriented Programming (OOP)  
4. Computer Networking  
5. Cybersecurity  
6. Cryptography  
7. System Design  
8. Distributed Systems  
9. Machine Learning  
10. Deep Learning  
11. Operating Systems  
12. Databases & SQL  
13. Bash & Shell Scripting  
14. Linux System Fundamentals  
15. Software Engineering Practices  
16. DevOps & CI/CD  
17. Computer Architecture  
18. Theory of Computation (Automata, Complexity, Decidability)  
19. Big Data & Data Engineering  
20. Data Mining & Knowledge Discovery  
21. Computer Graphics  
22. Quantum Computing  
23. Project Management in Software Engineering

---

## 🧰 4. Tech Stack

| Component | Technology |
|------------|-------------|
| **Language** | Python 3.10+ |
| **Discord API Wrapper** | `discord.py` |
| **Scheduling** | `apscheduler` |
| **Database** | SQLite (local) or PostgreSQL (for scalability) |
| **Data ORM** | SQLAlchemy or Peewee |
| **Charts** | `matplotlib` or `plotly` |
| **LLM (Question Generation)** | Groq API (using `requests` or `groq` Python SDK) |
| **Environment Management** | `python-dotenv` |
| **Hosting** | Ubuntu server (self-hosted) |
| **Process Manager** | `pm2` or `screen` |

---

## ⚙️ 5. Architecture Design

### 🧱 System Components

1. **Discord Bot Core**
   - Handles events, commands, message sending, and embeds.
2. **Question Generator (Groq LLM)**
   - Sends prompt to Groq API → receives MCQ JSON response.
3. **Question Manager**
   - Handles scheduling, storing, fetching, and displaying questions.
4. **Answer Tracker**
   - Listens for responses, validates correctness, and updates scores.
5. **Stats Engine**
   - Aggregates user data and generates visualizations.
6. **Storage Layer**
   - SQLite/PostgreSQL tables for users, questions, responses, and leaderboard.

---

## 🗂️ 6. Data Schema (SQLite Example)

### Table: `users`
| Field | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Discord user ID |
| `name` | TEXT | Username |
| `score` | INTEGER | Total score |
| `correct` | INTEGER | Number of correct answers |
| `wrong` | INTEGER | Number of wrong answers |
| `last_answer_time` | DATETIME | Timestamp of last response |

### Table: `questions`
| Field | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Question ID |
| `topic` | TEXT | Topic (e.g., “System Design”) |
| `question` | TEXT | Question text |
| `options` | JSON | A/B/C/D choices |
| `correct_answer` | TEXT | Correct choice (A/B/C/D) |
| `created_at` | DATETIME | Creation timestamp |

### Table: `responses`
| Field | Type | Description |
|--------|------|-------------|
| `user_id` | INTEGER | Reference to users.id |
| `question_id` | INTEGER | Reference to questions.id |
| `answer` | TEXT | User’s chosen answer |
| `is_correct` | BOOLEAN | True if correct |
| `timestamp` | DATETIME | When answered |

---

## 🧮 7. Ranking & Statistics

### 🏆 Leaderboard
- Generated weekly and monthly
- Uses weighted scoring (bonus for streaks or difficult questions)
- Example command: `!leaderboard`

### 📊 Performance Charts
- Generated using `matplotlib` or `plotly`
- Commands:
  - `!stats @user` → Shows personal accuracy graph
  - `!rank` → Shows bar chart of top scorers
  - `!topicstats` → Shows performance by category

Example graph types:
- Bar chart for top 10 users
- Line chart for daily accuracy trend
- Pie chart for topic coverage answered

---

## 🤖 8. Commands Specification

| Command | Description |
|----------|-------------|
| `!help` | Show command list |
| `!question` | Request a new question immediately |
| `!daily` | Get today’s scheduled question |
| `!answer A/B/C/D` | Submit an answer |
| `!leaderboard` | Display top users |
| `!stats [@user]` | Show performance stats |
| `!setchannel #channel` | Admin command to set the quiz channel |
| `!reset` | Admin reset leaderboard |
| `!topiclist` | Show all available question topics |
| `!addtopic topic_name` | Admin add a new topic |
| `!history` | Show user’s previous attempts |

---

## 🧠 9. LLM Question Generation (Groq Integration)

### Prompt Design Example
```python
prompt = f"""
Generate one multiple-choice question about {topic} in computer science.
Return the result strictly in JSON format:
{{
  "question": "string",
  "options": {{
    "A": "string",
    "B": "string",
    "C": "string",
    "D": "string"
  }},
  "answer": "A/B/C/D",
  "explanation": "brief explanation"
}}
"""
```

### Example Response
```json
{
  "question": "What is the time complexity of merge sort?",
  "options": {
    "A": "O(n^2)",
    "B": "O(n log n)",
    "C": "O(log n)",
    "D": "O(n)"
  },
  "answer": "B",
  "explanation": "Merge sort divides the array in halves and merges them in O(n log n) time."
}
```

### Integration Steps
1. Request → Groq API  
2. Parse JSON → store to DB  
3. Post embed to Discord  
4. Track answer correctness  

---

## 🕓 10. Daily Scheduler Logic

- Run every day at **09:00 (Asia/Ho_Chi_Minh)** using APScheduler:
  ```python
  scheduler.add_job(send_daily_question, 'cron', hour=9, timezone='Asia/Ho_Chi_Minh')
  ```
- Pulls or generates a question → posts to channel → listens for responses.

---

## 📊 11. Visualization Examples

**Leaderboard Chart Example**
```python
import matplotlib.pyplot as plt

def plot_leaderboard(users):
    names = [u.name for u in users]
    scores = [u.score for u in users]
    plt.bar(names, scores)
    plt.title("🏆 Weekly Leaderboard")
    plt.xlabel("Users")
    plt.ylabel("Score")
    plt.savefig("leaderboard.png")
```

**Performance Trend Example**
- Use line chart to show accuracy per day
- Save and send as image in Discord via:
  ```python
  await channel.send(file=discord.File("leaderboard.png"))
  ```

---

## 🔐 12. Security & Permissions

- Store secrets in `.env`:
  ```
  DISCORD_TOKEN=xxxxx
  GROQ_API_KEY=xxxxx
  ```
- Use `dotenv` to load credentials.
- Validate all admin commands by role ID.
- Limit question frequency to avoid API spam.

---

## 💡 13. Possible Enhancements

| Feature | Description |
|----------|-------------|
| ✅ LLM fine-tuning | Cache generated questions for consistency |
| ✅ Multiple difficulty levels | “Easy”, “Medium”, “Hard” |
| ✅ Topic filtering | `!question topic=Machine Learning` |
| ✅ Discord embed reactions | Emoji-based answering system |
| ✅ AI-generated feedback | After each question, show explanation |
| ✅ Web dashboard | Simple Flask UI to view stats & trends |
| ✅ Weekly recap summary | Auto-post summary every Sunday |

---

## 📁 14. Project Structure

```
discord-cs-quiz-bot/
├── bot.py                     # Main bot entry
├── cogs/
│   ├── questions.py           # Handle question posting, answering
│   ├── stats.py               # Generate charts & leaderboards
│   ├── admin.py               # Admin commands
│   └── scheduler.py           # APScheduler job setup
├── services/
│   ├── groq_client.py         # API calls to Groq LLM
│   ├── db.py                  # SQLAlchemy ORM setup
│   ├── charting.py            # Chart generation
│   └── utils.py               # Helper functions
├── data/
│   ├── questions.db           # SQLite database
│   ├── charts/                # Stored charts images
├── .env                       # Secrets (Discord token, Groq key)
├── requirements.txt
└── README.md
```

---

## 🧭 15. Deployment Plan

1. Clone repo to your Ubuntu server  
2. Install dependencies  
   ```bash
   pip install -r requirements.txt
   ```
3. Create `.env` with tokens  
4. Run bot using `pm2`:
   ```bash
   pm2 start bot.py --name cs-quiz-bot
   pm2 save
   pm2 startup
   ```
5. Watch logs:
   ```bash
   pm2 logs cs-quiz-bot
   ```

---

## ✅ 16. Deliverables

- Fully functional Discord bot
- SQLite/PostgreSQL database
- LLM-based question generation (Groq)
- Leaderboard and performance charts
- Configurable scheduling and commands

---

## 🧩 17. Example Workflow

1. At 9:00 AM → Bot posts question:  
   > 🧠 **Today's Topic:** Operating Systems  
   > **Question:** Which of the following scheduling algorithms is preemptive?  
   > A) FCFS  
   > B) SJF  
   > C) Round Robin  
   > D) Priority Queue  

2. User responds:  
   ```
   !answer C
   ```

3. Bot replies:  
   > ✅ Correct! Round Robin is a preemptive scheduling algorithm. (+10 pts)

4. User checks stats:  
   ```
   !stats @username
   ```

5. Bot sends chart + message:
   > **Your accuracy this week:** 85%  
   > 🏆 Current Rank: #2

---

## 🏁 Summary

This bot combines:
- **Educational content generation (via LLM)**
- **Community engagement (leaderboards, stats)**
- **Automation (daily schedules)**
- **Gamification (ranking & achievements)**

It’s not just a quiz bot — it’s a **24/7 interactive learning assistant for computer science mastery**.
