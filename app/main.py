import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import engine, get_db, Base
from app.models import User, Player, PlayerRating, Match, MatchPlayer, ATTRIBUTES, OUTFIELD_ATTRIBUTES, GK_ATTRIBUTES
from app.auth import oauth, get_current_user, require_login, require_admin, SECRET_KEY, ADMIN_GITHUB_USERNAME
from app.balancer import snake_draft

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="ASDA Footy 5s")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# ── Helpers ──────────────────────────────────────────────────────────────────

ADMIN_WEIGHT = 3.0
PLAYER_WEIGHT = 1.0


def get_weighted_ratings(db: Session, player_id: int) -> dict:
    """Get weighted average ratings for a player across all attributes."""
    ratings = db.query(PlayerRating).filter(PlayerRating.player_id == player_id).all()
    if not ratings:
        return {}

    attr_scores = {}
    for r in ratings:
        if r.attribute not in attr_scores:
            attr_scores[r.attribute] = {"weighted_sum": 0, "weight_total": 0}
        user = db.query(User).filter(User.id == r.rated_by_user_id).first()
        weight = ADMIN_WEIGHT if user and user.is_admin else PLAYER_WEIGHT
        attr_scores[r.attribute]["weighted_sum"] += r.score * weight
        attr_scores[r.attribute]["weight_total"] += weight

    return {
        attr: round(data["weighted_sum"] / data["weight_total"], 1)
        for attr, data in attr_scores.items()
        if data["weight_total"] > 0
    }


def get_overall_rating(weighted_ratings: dict) -> float:
    """Average of outfield attributes only."""
    outfield = [weighted_ratings.get(a, 0) for a in OUTFIELD_ATTRIBUTES if a in weighted_ratings]
    return round(sum(outfield) / len(outfield), 1) if outfield else 0


def compute_standings(db: Session) -> list:
    """Compute league table from matches. Each player's team results contribute."""
    matches = db.query(Match).order_by(Match.date.desc()).all()

    # Track team-level stats per match (not individual)
    # We'll track unique team compositions
    team_records = {}  # team_key -> {w, d, l, gf, ga}

    for match in matches:
        team_a_players = sorted([mp.player_id for mp in match.players if mp.team == "A"])
        team_b_players = sorted([mp.player_id for mp in match.players if mp.team == "B"])

        key_a = f"A_{match.id}"
        key_b = f"B_{match.id}"

        sa, sb = match.team_a_score, match.team_b_score

        team_records[key_a] = {
            "match_id": match.id, "team": "A", "date": match.date, "format": match.format,
            "players": team_a_players, "gf": sa, "ga": sb,
            "w": 1 if sa > sb else 0, "d": 1 if sa == sb else 0, "l": 1 if sa < sb else 0,
        }
        team_records[key_b] = {
            "match_id": match.id, "team": "B", "date": match.date, "format": match.format,
            "players": team_b_players, "gf": sb, "ga": sa,
            "w": 1 if sb > sa else 0, "d": 1 if sb == sa else 0, "l": 1 if sb < sa else 0,
        }

    return sorted(team_records.values(), key=lambda x: x["date"], reverse=True)


def compute_league_table(db: Session) -> list:
    """Aggregate W/D/L/GF/GA/GD/Pts across all matches, per-team-per-match."""
    matches = db.query(Match).order_by(Match.date.desc()).all()
    table = []
    for match in matches:
        sa, sb = match.team_a_score, match.team_b_score
        team_a_names = [mp.player.name for mp in match.players if mp.team == "A"]
        team_b_names = [mp.player.name for mp in match.players if mp.team == "B"]

        for team_names, gf, ga in [(team_a_names, sa, sb), (team_b_names, sb, sa)]:
            result = "W" if gf > ga else ("D" if gf == ga else "L")
            table.append({
                "match_id": match.id,
                "date": match.date.strftime("%d %b %Y"),
                "format": match.format,
                "team": ", ".join(sorted(team_names)),
                "gf": gf, "ga": ga, "gd": gf - ga,
                "result": result,
                "pts": 3 if result == "W" else (1 if result == "D" else 0),
            })

    return table


# ── Auth Routes ──────────────────────────────────────────────────────────────

@app.get("/login")
async def login(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.github.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.github.authorize_access_token(request)
    resp = await oauth.github.get("user", token=token)
    github_user = resp.json()

    # Find or create user
    user = db.query(User).filter(User.github_id == github_user["id"]).first()
    if not user:
        user = User(
            github_id=github_user["id"],
            github_username=github_user["login"],
            is_admin=(github_user["login"] == ADMIN_GITHUB_USERNAME),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    request.session["user"] = {
        "id": user.id,
        "github_username": user.github_username,
        "is_admin": user.is_admin,
    }
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


# ── Public Pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def league_table(request: Request, db: Session = Depends(get_db)):
    table = compute_league_table(db)
    user = get_current_user(request)
    return templates.TemplateResponse("table.html", {
        "request": request, "table": table, "user": user,
    })


@app.get("/ratings", response_class=HTMLResponse)
async def ratings_page(request: Request, db: Session = Depends(get_db)):
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    player_data = []
    for p in players:
        wr = get_weighted_ratings(db, p.id)
        overall = get_overall_rating(wr)
        player_data.append({
            "player": p,
            "ratings": wr,
            "overall": overall,
        })
    player_data.sort(key=lambda x: x["overall"], reverse=True)

    # Power rankings
    ranked = db.query(Player).filter(
        Player.is_active == True, Player.top_rank != None
    ).order_by(Player.top_rank).all()

    user = get_current_user(request)
    return templates.TemplateResponse("ratings.html", {
        "request": request, "player_data": player_data, "ranked": ranked,
        "attributes": OUTFIELD_ATTRIBUTES, "gk_attributes": GK_ATTRIBUTES,
        "user": user,
    })


@app.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, db: Session = Depends(get_db)):
    matches = db.query(Match).order_by(Match.date.desc()).all()
    match_data = []
    for m in matches:
        team_a = [mp.player.name for mp in m.players if mp.team == "A"]
        team_b = [mp.player.name for mp in m.players if mp.team == "B"]
        match_data.append({
            "match": m,
            "team_a": team_a,
            "team_b": team_b,
        })
    user = get_current_user(request)
    return templates.TemplateResponse("matches.html", {
        "request": request, "match_data": match_data, "user": user,
    })


# ── Rate Players (authenticated) ────────────────────────────────────────────

@app.get("/rate", response_class=HTMLResponse)
async def rate_page(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()

    # Get existing ratings by this user
    existing = {}
    user_ratings = db.query(PlayerRating).filter(PlayerRating.rated_by_user_id == user["id"]).all()
    for r in user_ratings:
        existing[(r.player_id, r.attribute)] = r.score

    return templates.TemplateResponse("rate.html", {
        "request": request, "players": players, "attributes": ATTRIBUTES,
        "existing": existing, "user": user,
    })


@app.post("/rate", response_class=HTMLResponse)
async def rate_submit(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    form = await request.form()

    players = db.query(Player).filter(Player.is_active == True).all()
    for player in players:
        for attr in ATTRIBUTES:
            key = f"r_{player.id}_{attr}"
            value = form.get(key)
            if value and value.strip():
                score = float(value)
                if not (1 <= score <= 10):
                    continue
                existing = db.query(PlayerRating).filter(
                    PlayerRating.player_id == player.id,
                    PlayerRating.rated_by_user_id == user["id"],
                    PlayerRating.attribute == attr,
                ).first()
                if existing:
                    existing.score = score
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(PlayerRating(
                        player_id=player.id,
                        rated_by_user_id=user["id"],
                        attribute=attr,
                        score=score,
                    ))
    db.commit()
    return RedirectResponse(url="/rate?saved=1", status_code=302)


# ── Admin: Manage Players ───────────────────────────────────────────────────

@app.get("/admin/players", response_class=HTMLResponse)
async def admin_players(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).order_by(Player.name).all()
    return templates.TemplateResponse("admin_players.html", {
        "request": request, "players": players, "user": user,
    })


@app.post("/admin/players/add", response_class=HTMLResponse)
async def admin_add_player(request: Request, name: str = Form(...), usp: str = Form(""), db: Session = Depends(get_db)):
    require_admin(request)
    db.add(Player(name=name, usp=usp))
    db.commit()
    return RedirectResponse(url="/admin/players", status_code=302)


@app.post("/admin/players/{player_id}/edit", response_class=HTMLResponse)
async def admin_edit_player(
    request: Request, player_id: int,
    name: str = Form(...), usp: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    require_admin(request)
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(404)
    player.name = name
    player.usp = usp
    player.is_active = is_active
    db.commit()
    return RedirectResponse(url="/admin/players", status_code=302)


# ── Admin: Rankings ─────────────────────────────────────────────────────────

@app.get("/admin/rankings", response_class=HTMLResponse)
async def admin_rankings(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.top_rank.nullsfirst(), Player.name).all()
    return templates.TemplateResponse("admin_rankings.html", {
        "request": request, "players": players, "user": user,
    })


@app.post("/admin/rankings", response_class=HTMLResponse)
async def admin_rankings_save(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()
    players = db.query(Player).filter(Player.is_active == True).all()
    for p in players:
        rank_val = form.get(f"rank_{p.id}")
        p.top_rank = int(rank_val) if rank_val and rank_val.strip() else None
    db.commit()
    return RedirectResponse(url="/admin/rankings", status_code=302)


# ── Admin: Record Match ─────────────────────────────────────────────────────

@app.get("/admin/match", response_class=HTMLResponse)
async def admin_match(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    return templates.TemplateResponse("admin_match.html", {
        "request": request, "players": players, "user": user,
    })


@app.post("/admin/match", response_class=HTMLResponse)
async def admin_match_save(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    form = await request.form()

    match = Match(
        date=datetime.strptime(form["date"], "%Y-%m-%d"),
        format=form.get("format", "5v5"),
        team_a_score=int(form["team_a_score"]),
        team_b_score=int(form["team_b_score"]),
    )
    db.add(match)
    db.flush()

    team_a_ids = form.getlist("team_a")
    team_b_ids = form.getlist("team_b")
    for pid in team_a_ids:
        db.add(MatchPlayer(match_id=match.id, player_id=int(pid), team="A"))
    for pid in team_b_ids:
        db.add(MatchPlayer(match_id=match.id, player_id=int(pid), team="B"))

    db.commit()
    return RedirectResponse(url="/matches", status_code=302)


# ── Admin: Team Generator ───────────────────────────────────────────────────

@app.get("/admin/generate", response_class=HTMLResponse)
async def admin_generate(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    return templates.TemplateResponse("admin_generate.html", {
        "request": request, "players": players, "user": user,
        "team_a": None, "team_b": None,
    })


@app.post("/admin/generate", response_class=HTMLResponse)
async def admin_generate_teams(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    form = await request.form()
    selected_ids = form.getlist("players")

    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()

    if len(selected_ids) < 2:
        return templates.TemplateResponse("admin_generate.html", {
            "request": request, "players": players, "user": user,
            "team_a": None, "team_b": None, "error": "Select at least 2 players",
        })

    players_with_ratings = []
    for pid in selected_ids:
        p = db.query(Player).filter(Player.id == int(pid)).first()
        if p:
            wr = get_weighted_ratings(db, p.id)
            overall = get_overall_rating(wr)
            players_with_ratings.append((p.id, p.name, overall))

    team_a, team_b, avg_a, avg_b = snake_draft(players_with_ratings)

    return templates.TemplateResponse("admin_generate.html", {
        "request": request, "players": players, "user": user,
        "team_a": team_a, "team_b": team_b,
        "avg_a": avg_a, "avg_b": avg_b,
        "selected_ids": [int(x) for x in selected_ids],
    })
