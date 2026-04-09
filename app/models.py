from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

# 8 attributes with descriptions for the app and rating form
ATTRIBUTES = [
    {
        "key": "Fitness",
        "label": "Fitness",
        "desc": "Stamina, work rate & pressing. Can you run all game without dropping off?",
        "profile": "Engine",
    },
    {
        "key": "Pace",
        "label": "Pace",
        "desc": "Raw sprint speed. How fast are you in a straight line?",
        "profile": "Speedster",
    },
    {
        "key": "Dribbling",
        "label": "Dribbling",
        "desc": "Ball control, close control & 1v1 ability. Can you beat a man in tight spaces?",
        "profile": "Baller",
    },
    {
        "key": "Attacking",
        "label": "Attacking",
        "desc": "Shooting, finishing & movement. Can you score when it matters?",
        "profile": "Goalscorer",
    },
    {
        "key": "Passing",
        "label": "Passing",
        "desc": "Short & long range accuracy, vision, through balls & final ball.",
        "profile": "Playmaker",
    },
    {
        "key": "Defending",
        "label": "Defending",
        "desc": "Tackling, positioning, interceptions & reading the game defensively.",
        "profile": "Rock",
    },
    {
        "key": "TeamPlay",
        "label": "Team Play",
        "desc": "Communication, unselfishness, game IQ & decision-making. Are you a team player?",
        "profile": "Leader",
    },
    {
        "key": "Goalkeeping",
        "label": "Goalkeeping",
        "desc": "Shot stopping, positioning & distribution when in goal.",
        "profile": "Keeper",
    },
]

ATTRIBUTE_KEYS = [a["key"] for a in ATTRIBUTES]
OUTFIELD_ATTRIBUTE_KEYS = [a["key"] for a in ATTRIBUTES if a["key"] != "Goalkeeping"]
PROFILE_MAP = {a["key"]: a["profile"] for a in ATTRIBUTES}

MATCH_FORMATS = ["5v5", "6v6", "7v7", "8v8"]


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    github_id = Column(Integer, unique=True, nullable=False)
    github_username = Column(String(100), nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    age_range = Column(String(10), default="")  # e.g. "20-25", "30-35"
    usp = Column(Text, default="")
    top_rank = Column(Integer, nullable=True)
    profile = Column(String(200), default="")  # e.g. "Engine, Speedster, Playmaker"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ratings = relationship("PlayerRating", back_populates="player", cascade="all, delete-orphan")
    match_appearances = relationship("MatchPlayer", back_populates="player")


class PlayerRating(Base):
    __tablename__ = "player_ratings"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    rated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rated_by_name = Column(String(100), nullable=True)  # for anonymous/public form ratings
    attribute = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("Player", back_populates="ratings")
    rated_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("player_id", "rated_by_user_id", "rated_by_name", "attribute",
                         name="uq_player_rater_attr"),
    )


class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    format = Column(String(10), default="5v5")
    team_a_score = Column(Integer, nullable=False)
    team_b_score = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    players = relationship("MatchPlayer", back_populates="match", cascade="all, delete-orphan")


class MatchPlayer(Base):
    __tablename__ = "match_players"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    team = Column(String(1), nullable=False)  # 'A' or 'B'

    match = relationship("Match", back_populates="players")
    player = relationship("Player", back_populates="match_appearances")
