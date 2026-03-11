import json
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import anthropic

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

LOGGER = logging.getLogger(__name__)

# Default topics used if topics.json is not found
DEFAULT_TOPICS = [
    "Algorithms & Data Structures",
    "Programming Languages",
    "Object-Oriented Programming",
    "Operating Systems",
    "Databases & SQL",
]


@dataclass
class QuestionPayload:
    topic: str
    question: str
    options: Dict[str, str]
    answer: str
    explanation: Optional[str] = None
    difficulty: Optional[str] = None
    model_name: str = "unknown"


@dataclass
class StoredQuestion:
    topic: str
    prompt: str
    options: Dict[str, str]
    answer: str
    explanation: Optional[str]


class ProviderAdapter:
    def build_request(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        settings: Dict[str, Any],
        provider_params: Dict[str, Any],
        model_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def send(self, client: Any, request: Dict[str, Any]) -> Any:
        raise NotImplementedError

    def extract_text(self, response: Any) -> Optional[str]:
        raise NotImplementedError


class OpenAIResponsesAdapter(ProviderAdapter):
    def build_request(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        settings: Dict[str, Any],
        provider_params: Dict[str, Any],
        model_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        params.update(provider_params)
        params.update(model_params)

        max_output_tokens = params.pop("max_output_tokens", None)
        if max_output_tokens is None:
            max_output_tokens = settings.get("max_tokens", 2048)

        input_text = params.pop("input", None)
        if input_text is None:
            input_text = f"{system_prompt}\n{user_prompt}"

        request: Dict[str, Any] = {
            "model": model_name,
            "max_output_tokens": max_output_tokens,
            "input": input_text,
        }

        if "reasoning" in params:
            request["reasoning"] = params.pop("reasoning")
        if "temperature" in params:
            request["temperature"] = params.pop("temperature")
        if "response_format" in params:
            request["response_format"] = params.pop("response_format")

        request.update(params)
        return request

    def send(self, client: Any, request: Dict[str, Any]) -> Any:
        return client.responses.create(**request)

    def extract_text(self, response: Any) -> Optional[str]:
        return getattr(response, "output_text", None)


class AnthropicMessagesAdapter(ProviderAdapter):
    def build_request(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        settings: Dict[str, Any],
        provider_params: Dict[str, Any],
        model_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        params.update(provider_params)
        params.update(model_params)

        max_tokens = params.pop("max_tokens", None)
        if max_tokens is None:
            max_tokens = settings.get("max_tokens", 2048)

        system_text = params.pop("system", system_prompt)
        messages = params.pop(
            "messages",
            [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        )

        request: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "system": system_text,
            "messages": messages,
        }

        if "temperature" in params:
            request["temperature"] = params.pop("temperature")

        request.update(params)
        return request

    def send(self, client: Any, request: Dict[str, Any]) -> Any:
        return client.messages.create(**request)

    def extract_text(self, response: Any) -> Optional[str]:
        content = getattr(response, "content", None)
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text" and block.get("text"):
                        parts.append(block["text"])
                else:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
            return "".join(parts).strip()
        return content


class LLMClient:
    """Wrapper around LLM APIs (Groq, OpenAI, etc.) used to request multiple-choice questions.

    Configuration is loaded from models.json in the project root.
    Supports multiple providers and automatically retries with different models on failure.
    """

    # Default fallback models if config file is not found
    AVAILABLE_MODELS = ["gpt-5.4"]

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, timeout: int = 30):
        # Load configuration from models.json
        self.config = self._load_config()
        self.settings = self.config.get("settings", {})
        self.prompts = self.config.get("prompts", {})

        # Load topics from topics.json
        self.available_topics = self._load_topics()

        # Build list of available models from enabled providers
        self.model_specs = self._get_model_specs()
        self.available_models = [spec["name"] for spec in self.model_specs]
        self._model_index = {spec["name"]: spec for spec in self.model_specs}

        self.model = model or random.choice(self.available_models if self.available_models else self.AVAILABLE_MODELS)
        self.timeout = self.settings.get("timeout", timeout)

        # Initialize clients for all enabled providers
        self._clients: Dict[str, Any] = {}
        self._initialize_provider_clients()

        self._adapters = {
            "openai": OpenAIResponsesAdapter(),
            "anthropic": AnthropicMessagesAdapter(),
            "claude": AnthropicMessagesAdapter(),
        }

    @staticmethod
    def _load_config() -> Dict:
        """Load LLM configuration from models.json file.

        Returns:
            Dictionary containing provider and settings configuration.
            Returns empty dict if file not found (uses defaults).
        """
        config_path = Path(__file__).parent.parent / "models.json"

        if not config_path.exists():
            LOGGER.warning("models.json not found at %s, using default configuration", config_path)
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                LOGGER.info("Loaded LLM configuration from models.json")
                return config
        except (json.JSONDecodeError, IOError) as exc:
            LOGGER.error("Failed to load models.json: %s. Using default configuration.", exc)
            return {}

    @staticmethod
    def _load_topics() -> List[str]:
        """Load quiz topics from topics.json file.

        Returns:
            List of topic strings from all enabled categories.
            Returns DEFAULT_TOPICS if file not found or error occurs.
        """
        topics_path = Path(__file__).parent.parent / "topics.json"

        if not topics_path.exists():
            LOGGER.warning("topics.json not found at %s, using default topics", topics_path)
            return DEFAULT_TOPICS

        try:
            with open(topics_path, "r", encoding="utf-8") as f:
                topics_config = json.load(f)
                categories = topics_config.get("categories", {})

                all_topics = []
                for category_name, category_data in categories.items():
                    if category_data.get("enabled", True):
                        category_topics = category_data.get("topics", [])
                        all_topics.extend(category_topics)
                        LOGGER.debug("Loaded %d topics from category '%s'",
                                    len(category_topics), category_name)

                if not all_topics:
                    LOGGER.warning("No topics found in topics.json, using defaults")
                    return DEFAULT_TOPICS

                LOGGER.info("Loaded %d topics from topics.json", len(all_topics))
                return all_topics

        except (json.JSONDecodeError, IOError) as exc:
            LOGGER.error("Failed to load topics.json: %s. Using default topics.", exc)
            return DEFAULT_TOPICS

    def _get_model_specs(self) -> List[Dict[str, Any]]:
        """Extract model specs from all enabled providers.

        Each spec has: name, provider, params.
        """
        specs: List[Dict[str, Any]] = []
        providers = self.config.get("providers", {})

        for provider_name, provider_config in providers.items():
            if not provider_config.get("enabled", False):
                continue
            provider_models = provider_config.get("models", [])

            for entry in provider_models:
                if isinstance(entry, str):
                    model_name = entry
                    model_params: Dict[str, Any] = {}
                elif isinstance(entry, dict):
                    model_name = entry.get("name") or entry.get("model")
                    model_params = entry.get("params", {}) or {}
                else:
                    continue

                if not model_name:
                    continue

                specs.append(
                    {
                        "name": model_name,
                        "provider": provider_name,
                        "params": model_params,
                    }
                )

            LOGGER.info(
                "Provider '%s' enabled with %d models: %s",
                provider_name,
                len(provider_models),
                provider_models,
            )

        if not specs:
            LOGGER.warning("No enabled providers found in config, using default models")
            return [
                {
                    "name": model_name,
                    "provider": "openai",
                    "params": {},
                }
                for model_name in self.AVAILABLE_MODELS
            ]

        return specs

    def _get_provider_for_model(self, model_name: str) -> Optional[Tuple[str, Dict]]:
        """Find which provider configuration matches the given model.

        Args:
            model_name: The model to look up.

        Returns:
            Tuple of (provider_name, provider_config) or None if not found.
        """
        model_spec = self._model_index.get(model_name)
        if not model_spec:
            return None

        provider_name = model_spec["provider"]
        providers = self.config.get("providers", {})
        provider_config = providers.get(provider_name, {})
        return (provider_name, provider_config)

    def _initialize_provider_clients(self) -> None:
        """Initialize API clients for all enabled providers.

        Creates client instances for Groq, OpenAI, and other providers based on
        what's enabled in models.json and what API keys are available.
        """
        providers = self.config.get("providers", {})

        for provider_name, provider_config in providers.items():
            if not provider_config.get("enabled", False):
                LOGGER.debug("Provider '%s' is disabled, skipping", provider_name)
                continue

            api_key_env = provider_config.get("api_key_env")
            if not api_key_env:
                LOGGER.warning("Provider '%s' has no api_key_env configured", provider_name)
                continue

            api_key = os.getenv(api_key_env)
            if not api_key:
                LOGGER.warning("Provider '%s' enabled but API key '%s' not found in environment",
                             provider_name, api_key_env)
                continue

            # Initialize the appropriate client based on provider name
            try:
                if provider_name == "openai":
                    if OpenAI is None:
                        LOGGER.error("OpenAI library not installed. Run: pip install openai")
                        continue
                    client = OpenAI(api_key=api_key)
                    self._clients[provider_name] = client
                    LOGGER.info("Initialized OpenAI client successfully")

                elif provider_name in ("anthropic", "claude"):
                    client = anthropic.Anthropic(api_key=api_key)
                    self._clients[provider_name] = client
                    LOGGER.info("Initialized Anthropic client successfully")

                else:
                    LOGGER.warning("Unknown provider '%s', skipping", provider_name)
                    continue

            except Exception as exc:
                LOGGER.error("Failed to initialize %s client: %s", provider_name, exc)
                continue

        if not self._clients:
            LOGGER.warning("No LLM provider clients initialized. Bot will use fallback questions.")

    def _get_client_for_model(self, model_name: str) -> Optional[Any]:
        """Get the appropriate API client for a given model.

        Args:
            model_name: The model to get a client for.

        Returns:
            The API client instance, or None if not available.
        """
        provider_info = self._get_provider_for_model(model_name)
        if not provider_info:
            LOGGER.warning("No provider found for model '%s'", model_name)
            return None

        provider_name, _ = provider_info
        client = self._clients.get(provider_name)

        if not client:
            LOGGER.warning("Client for provider '%s' not initialized", provider_name)
            return None

        return client

    def _get_client_and_provider_for_model(self, model_name: str) -> Tuple[Optional[str], Optional[Any]]:
        """Get both provider name and API client for a given model."""
        provider_info = self._get_provider_for_model(model_name)
        if not provider_info:
            LOGGER.warning("No provider found for model '%s'", model_name)
            return (None, None)

        provider_name, _ = provider_info
        client = self._clients.get(provider_name)

        if not client:
            LOGGER.warning("Client for provider '%s' not initialized", provider_name)
            return (provider_name, None)

        return (provider_name, client)

    def _get_model_params(self, model_name: str) -> Dict[str, Any]:
        spec = self._model_index.get(model_name)
        return spec.get("params", {}) if spec else {}

    def generate_question(self, topic: Optional[str] = None) -> QuestionPayload:
        """Generate a question via configured LLM providers or fall back to local questions.

        Retry logic: Attempts up to max_retries (default 3) different models before falling back.
        """
        chosen_topic = topic or random.choice(self.available_topics)
        # Weighted random selection: 50% Easy, 30% Medium, 20% Hard
        target_difficulty = random.choices(
            ["Easy", "Medium", "Hard"],
            weights=[30, 50, 20],
            k=1
        )[0]

        if not self._clients:
            LOGGER.warning("No LLM clients available; attempting stored question.")
            stored = self._reuse_stored_question(chosen_topic)
            if stored:
                self.model = stored.model_name
                return stored
            fallback = self._fallback_question(chosen_topic)
            self.model = fallback.model_name
            return fallback

        # Prepare models to try - shuffle for variety, limit to max_retries
        models_to_try = self.available_models[:]
        random.shuffle(models_to_try)
        max_retries = self.settings.get("max_retries", 3)
        models_to_try = models_to_try[:max_retries]

        # Get prompts from config or use defaults
        system_prompt = self.prompts.get(
            "system",
            "You are an expert computer science educator. You create rigorous multiple-choice questions "
            "with exactly four options labelled A, B, C, D. When a question references or benefits from code, include a concise snippet wrapped in Markdown triple backticks with an appropriate language tag (```python```, ```sql```, etc.). "
            "Respond with valid JSON following the schema: "
            '{"topic": "string", "question": "string", "options": {"A": "string", "B": "string", "C": "string", "D": "string"}, '
            '"answer": "A|B|C|D", "explanation": "string", "difficulty": "Easy|Medium|Hard"}.'
        )

        user_prompt_template = self.prompts.get(
            "user_template",
            "Generate one {difficulty} multiple-choice question about '{topic}'. "
            "Ensure the problem requires conceptual reasoning or multi-step thinking rather than simple recall. "
            "If code is necessary to pose the question or clarify the explanation, include it inside triple backticks with a language tag so that Discord can render it correctly."
        )

        user_prompt = user_prompt_template.format(
            difficulty=target_difficulty.lower(),
            topic=chosen_topic
        )

        failed_models = []

        for attempt_num, model_choice in enumerate(models_to_try, start=1):
            LOGGER.info("Attempt %d/%d: Trying model '%s' for topic '%s'",
                       attempt_num, max_retries, model_choice, chosen_topic)

            # Get the appropriate client for this model
            provider_name, client = self._get_client_and_provider_for_model(model_choice)
            if not client:
                LOGGER.warning("Attempt %d/%d: No client available for model '%s'",
                             attempt_num, max_retries, model_choice)
                failed_models.append((model_choice, "No client available"))
                continue

            try:
                adapter = self._adapters.get(provider_name)
                if not adapter:
                    LOGGER.warning("Attempt %d/%d: No adapter for provider '%s'",
                                  attempt_num, max_retries, provider_name)
                    failed_models.append((model_choice, f"No adapter for provider '{provider_name}'"))
                    continue

                provider_config = self.config.get("providers", {}).get(provider_name, {})
                provider_params = provider_config.get("default_params", {}) or {}
                model_params = self._get_model_params(model_choice)

                request = adapter.build_request(
                    model_name=model_choice,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    settings=self.settings,
                    provider_params=provider_params,
                    model_params=model_params,
                )
                response = adapter.send(client, request)
            except Exception as exc:  # pragma: no cover - network/API failure
                LOGGER.warning("Attempt %d/%d: Model '%s' API call failed: %s",
                              attempt_num, max_retries, model_choice, str(exc))
                failed_models.append((model_choice, f"API error: {str(exc)}"))
                continue

            try:
                adapter = self._adapters.get(provider_name)
                message_content = adapter.extract_text(response) if adapter else None

                if isinstance(message_content, dict):
                    parsed = message_content
                else:
                    parsed = json.loads(message_content)
            except (IndexError, KeyError, ValueError, TypeError) as exc:
                LOGGER.warning("Attempt %d/%d: Model '%s' returned unparseable response: %s",
                              attempt_num, max_retries, model_choice, str(exc))
                failed_models.append((model_choice, f"Parse error: {str(exc)}"))
                continue

            # Validate response structure
            options = parsed.get("options", {})
            if not options or len(options) != 4:
                LOGGER.warning("Attempt %d/%d: Model '%s' returned invalid options count: %d",
                              attempt_num, max_retries, model_choice, len(options))
                failed_models.append((model_choice, f"Invalid options: expected 4, got {len(options)}"))
                continue

            answer = parsed.get("answer", "").strip().upper()
            if answer not in ("A", "B", "C", "D"):
                LOGGER.warning("Attempt %d/%d: Model '%s' returned invalid answer: %s",
                              attempt_num, max_retries, model_choice, answer)
                failed_models.append((model_choice, f"Invalid answer: {answer}"))
                continue

            # Success! Return the validated question
            question_text = parsed.get("question", "No question returned.")
            self.model = model_choice
            LOGGER.info("Successfully generated question using model '%s' on attempt %d/%d",
                       model_choice, attempt_num, max_retries)
            return QuestionPayload(
                topic=parsed.get("topic", chosen_topic),
                question=question_text,
                options={opt.upper(): text for opt, text in options.items()},
                answer=answer,
                explanation=parsed.get("explanation"),
                difficulty=parsed.get("difficulty", target_difficulty),
                model_name=model_choice,
            )

        # All retries exhausted, use stored question then fallback
        LOGGER.error("All %d model attempts failed. Failed models: %s. Attempting stored question.",
                    max_retries, failed_models)
        stored = self._reuse_stored_question(chosen_topic)
        if stored:
            self.model = stored.model_name
            return stored
        fallback = self._fallback_question(chosen_topic)
        self.model = fallback.model_name
        return fallback

    def _reuse_stored_question(self, topic: str) -> Optional[QuestionPayload]:
        """Try to reuse a recent stored question from the database."""
        try:
            from services import db
        except Exception as exc:  # pragma: no cover - optional dependency
            LOGGER.warning("Database module not available: %s", exc)
            return None

        try:
            recent = db.fetch_recent_questions(limit=25)
        except Exception as exc:  # pragma: no cover - db failure
            LOGGER.warning("Failed to fetch stored questions: %s", exc)
            return None

        if not recent:
            return None

        candidates = [q for q in recent if q.get("topic") == topic]
        pool = candidates if candidates else recent
        choice = random.choice(pool)

        try:
            return QuestionPayload(
                topic=choice.get("topic", topic),
                question=choice.get("prompt", "No question stored."),
                options=choice.get("options", {}),
                answer=choice.get("correct_answer", "").strip().upper(),
                explanation=choice.get("explanation"),
                difficulty=None,
                model_name="stored",
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Stored question malformed: %s", exc)
            return None

    @staticmethod
    def _fallback_question(topic: str) -> QuestionPayload:
        """Return a curated fallback question when all LLM attempts fail.

        This ensures the bot always has quality questions to serve even during API outages.
        """
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
                difficulty="Medium",
                model_name="fallback",
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
                difficulty="Medium",
                model_name="fallback",
            ),
            "Databases & SQL": QuestionPayload(
                topic="Databases & SQL",
                question="In SQL, which isolation level prevents dirty reads but allows phantom reads?",
                options={
                    "A": "Read Uncommitted",
                    "B": "Read Committed",
                    "C": "Repeatable Read",
                    "D": "Serializable",
                },
                answer="B",
                explanation="Read Committed prevents dirty reads but still allows phantom reads and non-repeatable reads.",
                difficulty="Medium",
                model_name="fallback",
            ),
            "Computer Networking": QuestionPayload(
                topic="Computer Networking",
                question="Which layer of the OSI model is responsible for end-to-end communication and error recovery?",
                options={
                    "A": "Network Layer",
                    "B": "Transport Layer",
                    "C": "Session Layer",
                    "D": "Data Link Layer",
                },
                answer="B",
                explanation="The Transport Layer (Layer 4) provides end-to-end communication services including error recovery and flow control.",
                difficulty="Medium",
                model_name="fallback",
            ),
            "Machine Learning": QuestionPayload(
                topic="Machine Learning",
                question="Which technique helps prevent overfitting by randomly dropping neurons during training?",
                options={
                    "A": "Batch Normalization",
                    "B": "Dropout",
                    "C": "Early Stopping",
                    "D": "Data Augmentation",
                },
                answer="B",
                explanation="Dropout randomly disables neurons during training, forcing the network to learn redundant representations and preventing overfitting.",
                difficulty="Medium",
                model_name="fallback",
            ),
        }

        if topic in sample_questions:
            LOGGER.info("Using curated fallback question for topic: %s", topic)
            return sample_questions[topic]

        # Default fallback when topic not in curated sample list
        LOGGER.info("Using generic fallback question for topic: %s", topic)
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
            difficulty="Medium",
            model_name="fallback",
        )
