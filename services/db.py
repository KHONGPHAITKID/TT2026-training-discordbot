import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker

LOGGER = logging.getLogger(__name__)

Base = declarative_base()


def _resolve_database_url() -> str:
    """Return the database URL, defaulting to a local SQLite file."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'questions.db'}"


ENGINE = create_engine(
    _resolve_database_url(),
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in _resolve_database_url() else {},
)
SESSION_FACTORY = scoped_session(
    sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, expire_on_commit=False)
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(String(255), nullable=False)
    score = Column(Integer, default=0, nullable=False)
    correct = Column(Integer, default=0, nullable=False)
    wrong = Column(Integer, default=0, nullable=False)
    last_answer_time = Column(DateTime, nullable=True)

    responses = relationship("Response", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "id": self.id,
            "name": self.name,
            "score": self.score,
            "correct": self.correct,
            "wrong": self.wrong,
            "last_answer_time": self.last_answer_time.isoformat() if self.last_answer_time else None,
        }


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, nullable=True)  # Discord message that announced the question
    channel_id = Column(Integer, nullable=True)
    topic = Column(String(120), nullable=False)
    prompt = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)
    correct_answer = Column(String(2), nullable=False)
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    responses = relationship("Response", back_populates="question", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "topic": self.topic,
            "prompt": self.prompt,
            "options": self.options,
            "correct_answer": self.correct_answer,
            "explanation": self.explanation,
            "created_at": self.created_at.isoformat(),
        }


class Response(Base):
    __tablename__ = "responses"
    __table_args__ = (UniqueConstraint("question_id", "user_id", name="uq_question_user"),)

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    answer = Column(String(2), nullable=False)
    is_correct = Column(Integer, default=0, nullable=False)
    answered_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="responses")
    question = relationship("Question", back_populates="responses")

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "id": self.id,
            "question_id": self.question_id,
            "user_id": self.user_id,
            "answer": self.answer,
            "is_correct": self.is_correct,
            "answered_at": self.answered_at.isoformat(),
        }


class GuildConfig(Base):
    __tablename__ = "guild_config"

    guild_id = Column(Integer, primary_key=True, autoincrement=False)
    daily_channel_id = Column(Integer, nullable=True)
    admin_role_id = Column(Integer, nullable=True)

    def to_dict(self) -> Dict[str, Optional[int]]:
        return {
            "guild_id": self.guild_id,
            "daily_channel_id": self.daily_channel_id,
            "admin_role_id": self.admin_role_id,
        }


def init_db() -> None:
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(ENGINE)
    LOGGER.info("Database initialised at %s", _resolve_database_url())


@contextmanager
def get_session():
    """Provide a transactional scope around a series of operations."""
    session = SESSION_FACTORY()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        LOGGER.exception("Database error: {exc}", exc_info=exc)
        raise
    finally:
        session.close()


def upsert_user(user_id: int, name: str) -> User:
    """Fetch an existing user or create a new record."""
    with get_session() as session:
        user = session.get(User, user_id)
        if user:
            if user.name != name:
                user.name = name
            session.add(user)
            session.flush()
            session.refresh(user)
            session.expunge(user)
            return user

        user = User(id=user_id, name=name)
        session.add(user)
        session.flush()
        session.refresh(user)
        session.expunge(user)
        return user


def record_question(
    topic: str,
    prompt: str,
    options: Dict[str, str],
    correct_answer: str,
    explanation: Optional[str] = None,
    message_id: Optional[int] = None,
    channel_id: Optional[int] = None,
) -> Question:
    """Persist a new question and return the stored object."""
    with get_session() as session:
        question = Question(
            topic=topic,
            prompt=prompt,
            options=options,
            correct_answer=correct_answer,
            explanation=explanation,
            message_id=message_id,
            channel_id=channel_id,
        )
        session.add(question)
        session.flush()
        session.refresh(question)
        session.expunge(question)
        return question


def attach_message_id(question_id: int, message_id: int) -> None:
    """Update the stored message ID for a previously created question."""
    with get_session() as session:
        question = session.get(Question, question_id)
        if not question:
            return
        question.message_id = message_id
        session.add(question)


def record_response(
    question_id: int,
    user_id: int,
    username: str,
    answer: str,
    is_correct: bool,
) -> Response:
    """Record a user response and update their stats atomically."""
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            user = User(id=user_id, name=username)
            session.add(user)
            session.flush()

        user.last_answer_time = datetime.utcnow()
        user.name = username
        if is_correct:
            user.score += 10
            user.correct += 1
        else:
            user.wrong += 1

        response = Response(
            question_id=question_id,
            user_id=user_id,
            answer=answer,
            is_correct=1 if is_correct else 0,
        )
        session.add(response)
        session.flush()
        session.refresh(response)
        session.expunge(response)
        return response


def get_leaderboard(limit: int = 10) -> List[Dict[str, Optional[str]]]:
    """Return the top users by score."""
    with get_session() as session:
        users = session.query(User).order_by(User.score.desc(), User.correct.desc()).limit(limit).all()
        return [user.to_dict() for user in users]


def get_user_stats(user_id: int) -> Optional[Dict[str, Optional[str]]]:
    with get_session() as session:
        user = session.get(User, user_id)
        return user.to_dict() if user else None


def fetch_recent_questions(limit: int = 20) -> List[Dict[str, Optional[str]]]:
    with get_session() as session:
        questions = (
            session.query(Question).order_by(Question.created_at.desc()).limit(limit).all()
        )
        return [question.to_dict() for question in questions]


def get_question(question_id: int) -> Optional[Question]:
    with get_session() as session:
        question = session.get(Question, question_id)
        if question:
            session.expunge(question)
        return question


def get_latest_question() -> Optional[Question]:
    with get_session() as session:
        question = session.query(Question).order_by(Question.created_at.desc()).first()
        if question:
            session.expunge(question)
        return question


def get_latest_question_for_channel(channel_id: int) -> Optional[Question]:
    with get_session() as session:
        question = (
            session.query(Question)
            .filter(Question.channel_id == channel_id)
            .order_by(Question.created_at.desc())
            .first()
        )
        if question:
            session.expunge(question)
        return question


def get_question_and_responses(question_id: int) -> Tuple[Optional[Question], List[Response]]:
    with get_session() as session:
        question = session.get(Question, question_id)
        if not question:
            return None, []
        responses = (
            session.query(Response)
            .filter(Response.question_id == question_id)
            .order_by(Response.answered_at.asc())
            .all()
        )
        session.expunge(question)
        for response in responses:
            session.expunge(response)
        return question, responses


def iter_user_history(user_id: int) -> Iterable[Response]:
    with get_session() as session:
        responses = (
            session.query(Response)
            .filter(Response.user_id == user_id)
            .order_by(Response.answered_at.desc())
            .all()
        )
        for response in responses:
            session.expunge(response)
        return list(responses)


def has_user_answered(question_id: int, user_id: int) -> bool:
    with get_session() as session:
        result = (
            session.query(Response)
            .filter(Response.question_id == question_id, Response.user_id == user_id)
            .first()
        )
        return result is not None


def get_first_correct_response(question_id: int) -> Optional[Response]:
    with get_session() as session:
        result = (
            session.query(Response)
            .filter(Response.question_id == question_id, Response.is_correct == 1)
            .order_by(Response.answered_at.asc())
            .first()
        )
        if result:
            session.expunge(result)
        return result


def get_guild_config(guild_id: int) -> GuildConfig:
    with get_session() as session:
        config = session.get(GuildConfig, guild_id)
        if config:
            session.expunge(config)
            return config
        config = GuildConfig(guild_id=guild_id)
        session.add(config)
        session.flush()
        session.expunge(config)
        return config


def update_guild_config(guild_id: int, **kwargs) -> GuildConfig:
    with get_session() as session:
        config = session.get(GuildConfig, guild_id)
        if not config:
            config = GuildConfig(guild_id=guild_id)
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        session.add(config)
        session.flush()
        session.expunge(config)
        return config
