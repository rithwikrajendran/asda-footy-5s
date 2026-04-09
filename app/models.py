from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

# ── 8 Rating Attributes ──────────────────────────────────────────────────────

ATTRIBUTES = [
    {
        "key": "Fitness",
        "label": "Fitness",
        "desc": "Stamina, work rate & pressing. Can you run all game without dropping off?",
    },
    {
        "key": "Pace",
        "label": "Pace",
        "desc": "Raw sprint speed. How fast are you in a straight line?",
    },
    {
        "key": "Dribbling",
        "label": "Dribbling",
        "desc": "Ball control, close control & 1v1 ability. Can you beat a man in tight spaces?",
    },
    {
        "key": "Attacking",
        "label": "Attacking",
        "desc": "Shooting, finishing & movement. Can you score when it matters?",
    },
    {
        "key": "Passing",
        "label": "Passing",
        "desc": "Short & long range accuracy, vision, through balls & final ball.",
    },
    {
        "key": "Defending",
        "label": "Defending",
        "desc": "Tackling, positioning, interceptions & reading the game defensively.",
    },
    {
        "key": "TeamPlay",
        "label": "Team Play",
        "desc": "Communication, unselfishness, game IQ & decision-making. Are you a team player?",
    },
    {
        "key": "Goalkeeping",
        "label": "Goalkeeping",
        "desc": "Shot stopping, positioning & distribution when in goal.",
    },
]

ATTRIBUTE_KEYS = [a["key"] for a in ATTRIBUTES]
OUTFIELD_ATTRIBUTE_KEYS = [a["key"] for a in ATTRIBUTES if a["key"] != "Goalkeeping"]

# ── 12 Archetypes (auto-computed from attribute weights) ─────────────────────

ARCHETYPES = [
    {
        "key": "Engine",
        "label": "Engine",
        "desc": "High stamina, runs all game, presses non-stop. The player who never stops.",
        "weights": {"Fitness": 0.50, "TeamPlay": 0.25, "Defending": 0.15, "Pace": 0.10},
    },
    {
        "key": "Speedster",
        "label": "Speedster",
        "desc": "Pace merchant, runs in behind, stretches play. Burns defenders for fun.",
        "weights": {"Pace": 0.55, "Fitness": 0.20, "Dribbling": 0.15, "Attacking": 0.10},
    },
    {
        "key": "Playmaker",
        "label": "Playmaker",
        "desc": "Creative passer, vision, controls the tempo. The brain of the team.",
        "weights": {"Passing": 0.50, "Dribbling": 0.25, "TeamPlay": 0.15, "Attacking": 0.10},
    },
    {
        "key": "Goalscorer",
        "label": "Goalscorer",
        "desc": "Clinical finisher, always in the right place. Lives for goals.",
        "weights": {"Attacking": 0.55, "Pace": 0.20, "Dribbling": 0.15, "Passing": 0.10},
    },
    {
        "key": "Baller",
        "label": "Baller",
        "desc": "Skilful dribbler, takes players on, flair. The entertainer.",
        "weights": {"Dribbling": 0.50, "Attacking": 0.25, "Pace": 0.15, "Passing": 0.10},
    },
    {
        "key": "Rock",
        "label": "Rock",
        "desc": "Solid defender, reads the game, wins tackles. The last line of defence.",
        "weights": {"Defending": 0.55, "Fitness": 0.20, "TeamPlay": 0.15, "Goalkeeping": 0.10},
    },
    {
        "key": "Leader",
        "label": "Leader",
        "desc": "Organizes the team, vocal, lifts others. Everyone plays better around them.",
        "weights": {"TeamPlay": 0.50, "Fitness": 0.20, "Defending": 0.15, "Passing": 0.15},
    },
    {
        "key": "BoxToBox",
        "label": "Box-to-Box",
        "desc": "Does everything, end to end. Defends, attacks, scores — the complete player.",
        "weights": {"Fitness": 0.25, "Attacking": 0.20, "Defending": 0.20, "Passing": 0.15, "Pace": 0.10, "TeamPlay": 0.10},
    },
    {
        "key": "TargetMan",
        "label": "Target Man",
        "desc": "Physical presence, holds up play, brings others into the game.",
        "weights": {"Attacking": 0.35, "TeamPlay": 0.25, "Defending": 0.20, "Passing": 0.20},
    },
    {
        "key": "Terrier",
        "label": "Terrier",
        "desc": "Aggressive presser, wins the ball back, never stops running.",
        "weights": {"Fitness": 0.40, "Defending": 0.30, "Pace": 0.20, "TeamPlay": 0.10},
    },
    {
        "key": "Sniper",
        "label": "Sniper",
        "desc": "Long-range shooting, set piece specialist. Scores from anywhere.",
        "weights": {"Attacking": 0.65, "Passing": 0.20, "Dribbling": 0.15},
    },
    {
        "key": "Wall",
        "label": "Wall",
        "desc": "Defensive brick, impossible to get past. You shall not pass.",
        "weights": {"Defending": 0.50, "Goalkeeping": 0.25, "Fitness": 0.15, "TeamPlay": 0.10},
    },
]

ARCHETYPE_KEYS = [a["key"] for a in ARCHETYPES]

MATCH_FORMATS = ["5v5", "6v6", "7v7", "8v8"]


# ── Database Models ──────────────────────────────────────────────────────────

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
    age_range = Column(String(10), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ratings = relationship("PlayerRating", back_populates="player", cascade="all, delete-orphan")
    archetype_scores = relationship("PlayerArchetype", back_populates="player", cascade="all, delete-orphan")
    match_appearances = relationship("MatchPlayer", back_populates="player")


class PlayerRating(Base):
    __tablename__ = "player_ratings"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    rated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rated_by_name = Column(String(100), nullable=True)
    attribute = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)
    comment = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("Player", back_populates="ratings")
    rated_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("player_id", "rated_by_user_id", "rated_by_name", "attribute",
                         name="uq_player_rater_attr"),
    )


class PlayerArchetype(Base):
    __tablename__ = "player_archetypes"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    archetype_key = Column(String(50), nullable=False)
    score = Column(Float, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("Player", back_populates="archetype_scores")

    __table_args__ = (
        UniqueConstraint("player_id", "archetype_key", name="uq_player_archetype"),
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
    team = Column(String(1), nullable=False)

    match = relationship("Match", back_populates="players")
    player = relationship("Player", back_populates="match_appearances")
