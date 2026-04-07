from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

ATTRIBUTES = [
    "Pace", "Dribbling", "Passing", "Shooting", "Defending",
    "Physicality", "Pressing", "Game IQ", "Team Play", "Consistency",
    "GK: Shot Stopping", "GK: Positioning",
]
OUTFIELD_ATTRIBUTES = ATTRIBUTES[:10]
GK_ATTRIBUTES = ATTRIBUTES[10:]


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
    usp = Column(Text, default="")
    top_rank = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ratings = relationship("PlayerRating", back_populates="player", cascade="all, delete-orphan")
    match_appearances = relationship("MatchPlayer", back_populates="player")


class PlayerRating(Base):
    __tablename__ = "player_ratings"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    rated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    attribute = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("Player", back_populates="ratings")
    rated_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("player_id", "rated_by_user_id", "attribute", name="uq_player_user_attr"),
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
