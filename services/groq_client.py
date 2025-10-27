import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Dict, Optional

from groq import Groq

LOGGER = logging.getLogger(__name__)

DEFAULT_TOPICS = [
    "Algorithms & Data Structures",
    "Programming Languages",
    "Object-Oriented Programming",
    "Computer Networking",
    "Cybersecurity",
    "Cryptography",
    "System Design",
    "Distributed Systems",
    "Machine Learning",
    "Deep Learning",
    "Operating Systems",
    "Databases & SQL",
    "Bash & Shell Scripting",
    "Linux System Fundamentals",
    "Software Engineering Practices",
    "DevOps & CI/CD",
    "Computer Architecture",
    "Theory of Computation",
    "Big Data & Data Engineering",
    "Data Mining & Knowledge Discovery",
    "Computer Graphics",
    "Quantum Computing",
    "Project Management in Software Engineering",
]


@dataclass
class QuestionPayload:
    topic: str
    question: str
    options: Dict[str, str]
    answer: str
    explanation: Optional[str] = None


class GroqClient:
    """Wrapper around the Groq API used to request multiple-choice questions."""

    def __init__(self, api_key: Optional[str] = None, model: str = "llama-3.3-70b-versatile", timeout: int = 30):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model
        self.timeout = timeout
        self._client: Optional[Groq] = None
        if self.api_key:
            try:
                self._client = Groq(api_key=self.api_key)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.exception("Failed to initialise Groq client, falling back to offline questions.", exc_info=exc)
                self._client = None

    def generate_question(self, topic: Optional[str] = None) -> QuestionPayload:
        """Generate a question via Groq or fall back to the local sample library."""
        chosen_topic = topic or random.choice(DEFAULT_TOPICS)
        if not self._client:
            LOGGER.warning("Groq client unavailable; using local fallback question.")
            return self._fallback_question(chosen_topic)

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=0.7,
                max_tokens=512,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert computer science educator. You create rigorous multiple-choice questions "
                            "with exactly four options labelled A, B, C, D. Respond with valid JSON following the schema: "
                            '{"topic": "string", "question": "string", "options": {"A": "string", "B": "string", "C": "string", "D": "string"}, '
                            '"answer": "A|B|C|D", "explanation": "string"}.'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Create a single multiple-choice question about the topic '{chosen_topic}'.",
                    },
                ],
            )
        except Exception as exc:  # pragma: no cover - network/API failure
            LOGGER.exception("Groq API request failed, using fallback question.", exc_info=exc)
            return self._fallback_question(chosen_topic)

        try:
            message_content = response.choices[0].message.content
            if isinstance(message_content, dict):
                parsed = message_content
            else:
                parsed = json.loads(message_content)
        except (IndexError, KeyError, ValueError, TypeError) as exc:
            LOGGER.exception("Unexpected Groq response, using fallback question.", exc_info=exc)
            return self._fallback_question(chosen_topic)

        options = parsed.get("options", {})
        if not options or len(options) != 4:
            LOGGER.error("Groq response missing four options, using fallback question: %s", parsed)
            return self._fallback_question(chosen_topic)

        answer = parsed.get("answer", "").strip().upper()
        if answer not in ("A", "B", "C", "D"):
            LOGGER.error("Groq response answer invalid, using fallback question: %s", parsed)
            return self._fallback_question(chosen_topic)

        return QuestionPayload(
            topic=parsed.get("topic", chosen_topic),
            question=parsed.get("question", "No question returned."),
            options={opt.upper(): text for opt, text in options.items()},
            answer=answer,
            explanation=parsed.get("explanation"),
        )

    @staticmethod
    def _fallback_question(topic: str) -> QuestionPayload:
        sample_questions = {
            "Operating Systems": QuestionPayload(
                topic="Operating Systems",
                question="Which of the following scheduling algorithms is preemptive by design?",
                options={
                    "A": "First-Come, First-Served (FCFS)",
                    "B": "Shortest Job First (SJF)",
                    "C": "Round Robin",
                    "D": "Non-preemptive Priority",
                },
                answer="C",
                explanation="Round Robin uses time slices, forcing a context switch once the quantum expires.",
            ),
            "Algorithms & Data Structures": QuestionPayload(
                topic="Algorithms & Data Structures",
                question="What is the time complexity of inserting an element into a max-heap of size n?",
                options={
                    "A": "O(1)",
                    "B": "O(log n)",
                    "C": "O(n)",
                    "D": "O(n log n)",
                },
                answer="B",
                explanation="Heap insertion bubbles the value up at most log n levels.",
            ),
        }

        if topic in sample_questions:
            return sample_questions[topic]

        # Default fallback when topic not in curated sample list.
        return QuestionPayload(
            topic=topic,
            question="Which Big-O complexity class represents logarithmic time?",
            options={
                "A": "O(1)",
                "B": "O(n)",
                "C": "O(log n)",
                "D": "O(n^2)",
            },
            answer="C",
            explanation="Logarithmic time grows slowly, often observed in balanced divide-and-conquer algorithms.",
        )
