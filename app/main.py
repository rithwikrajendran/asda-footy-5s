import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.database import engine, get_db, Base
from app.models import (
    User, Player, PlayerRating, PlayerArchetype, Match, MatchPlayer,
    ATTRIBUTES, ATTRIBUTE_KEYS, OUTFIELD_ATTRIBUTE_KEYS,
    ARCHETYPES, ARCHETYPE_KEYS, MATCH_FORMATS,
)
from app.auth import oauth, get_current_user, require_login, require_admin, SECRET_KEY, ADMIN_GITHUB_USERNAME
from app.balancer import snake_draft

# ── DB Setup & Migration ────────────────────────────────────────────────────

from sqlalchemy import inspect, text


def migrate_db():
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as conn:
        if "players" in tables:
            existing = {c["name"] for c in inspector.get_columns("players")}
            if "profile" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN profile VARCHAR(200) DEFAULT ''"))
            if "age_range" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN age_range VARCHAR(10) DEFAULT ''"))
        if "player_ratings" in tables:
            existing = {c["name"] for c in inspector.get_columns("player_ratings")}
            if "rated_by_name" not in existing:
                conn.execute(text("ALTER TABLE player_ratings ADD COLUMN rated_by_name VARCHAR(100)"))
            if "comment" not in existing:
                conn.execute(text("ALTER TABLE player_ratings ADD COLUMN comment TEXT DEFAULT ''"))
            # Allow NULL on rated_by_user_id for public form ratings
            try:
                conn.execute(text("ALTER TABLE player_ratings ALTER COLUMN rated_by_user_id DROP NOT NULL"))
            except Exception:
                pass
            # Fix unique constraint to include rated_by_name
            try:
                conn.execute(text("ALTER TABLE player_ratings DROP CONSTRAINT IF EXISTS uq_player_rater_attr"))
            except Exception:
                pass
    Base.metadata.create_all(bind=engine)


migrate_db()

# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="ASDA Footy 5s")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


@app.middleware("http")
async def force_https_redirect_uri(request: Request, call_next):
    if request.headers.get("x-forwarded-proto") == "https":
        request.scope["scheme"] = "https"
    return await call_next(request)


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# ── Helpers ──────────────────────────────────────────────────────────────────

ADMIN_WEIGHT = 3.0
PLAYER_WEIGHT = 1.0


def get_weighted_ratings(db: Session, player_id: int) -> dict:
    """Compute weighted average per attribute using most recent non-NULL rating per rater."""
    ratings = db.query(PlayerRating).filter(
        PlayerRating.player_id == player_id,
        PlayerRating.score != None,
    ).all()
    if not ratings:
        return {}

    # Deduplicate: keep only the most recent rating per (attribute, rater)
    # Key: (attribute, rater_key) → most recent rating
    latest = {}
    for r in ratings:
        if r.score is None:
            continue
        # Identify the rater: authenticated users by user_id, public by name
        if r.rated_by_user_id:
            rater_key = f"user:{r.rated_by_user_id}"
        else:
            rater_key = f"name:{(r.rated_by_name or '').lower().strip()}"
        key = (r.attribute, rater_key)
        existing = latest.get(key)
        if existing is None or (r.updated_at or r.id) > (existing.updated_at or existing.id):
            latest[key] = r

    # Build weighted averages from deduplicated ratings
    # Cache admin lookups to avoid repeated queries
    admin_cache = {}
    attr_scores = {}
    for (attr, rater_key), r in latest.items():
        if attr not in attr_scores:
            attr_scores[attr] = {"weighted_sum": 0, "weight_total": 0}
        weight = PLAYER_WEIGHT
        if r.rated_by_user_id:
            if r.rated_by_user_id not in admin_cache:
                u = db.query(User).filter(User.id == r.rated_by_user_id).first()
                admin_cache[r.rated_by_user_id] = u and u.is_admin
            if admin_cache[r.rated_by_user_id]:
                weight = ADMIN_WEIGHT
        attr_scores[attr]["weighted_sum"] += r.score * weight
        attr_scores[attr]["weight_total"] += weight

    return {
        attr: round(data["weighted_sum"] / data["weight_total"], 1)
        for attr, data in attr_scores.items()
        if data["weight_total"] > 0
    }


def get_overall_rating(weighted_ratings: dict) -> float:
    outfield = [weighted_ratings.get(a, 0) for a in OUTFIELD_ATTRIBUTE_KEYS if a in weighted_ratings]
    return round(sum(outfield) / len(outfield), 1) if outfield else 0


def compute_archetype_scores(weighted_ratings: dict) -> list:
    """Compute all archetype scores from attribute ratings. Returns sorted list of (key, label, score)."""
    if not weighted_ratings:
        return []
    results = []
    for arch in ARCHETYPES:
        score = sum(
            weighted_ratings.get(attr, 0) * w
            for attr, w in arch["weights"].items()
        )
        results.append((arch["key"], arch["label"], round(score, 1)))
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def update_player_data(db: Session, player_id: int):
    """Recompute archetype scores for a player and save to DB."""
    wr = get_weighted_ratings(db, player_id)
    arch_scores = compute_archetype_scores(wr)

    for key, label, score in arch_scores:
        existing = db.query(PlayerArchetype).filter(
            PlayerArchetype.player_id == player_id,
            PlayerArchetype.archetype_key == key,
        ).first()
        if existing:
            existing.score = score
            existing.updated_at = datetime.utcnow()
        else:
            db.add(PlayerArchetype(player_id=player_id, archetype_key=key, score=score))


def get_player_top_archetypes(db: Session, player_id: int, n: int = 3) -> list:
    """Get top N archetype labels for a player."""
    archs = db.query(PlayerArchetype).filter(
        PlayerArchetype.player_id == player_id
    ).order_by(PlayerArchetype.score.desc()).limit(n).all()
    label_map = {a["key"]: a["label"] for a in ARCHETYPES}
    return [(label_map.get(a.archetype_key, a.archetype_key), a.score) for a in archs if a.score > 0]


def get_ranked_players(db: Session) -> list:
    """Return active players ranked by overall rating (auto-rank)."""
    players = db.query(Player).filter(Player.is_active == True).all()
    player_data = []
    for p in players:
        wr = get_weighted_ratings(db, p.id)
        overall = get_overall_rating(wr)
        top_archs = get_player_top_archetypes(db, p.id)
        player_data.append({
            "player": p,
            "ratings": wr,
            "overall": overall,
            "top_archetypes": top_archs,
        })
    player_data.sort(key=lambda x: x["overall"], reverse=True)
    for i, pd in enumerate(player_data):
        pd["rank"] = i + 1
    return player_data


def compute_league_table(db: Session) -> list:
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
    return templates.TemplateResponse("table.html", {"request": request, "table": table, "user": user})


@app.get("/ratings", response_class=HTMLResponse)
async def ratings_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    # Temporarily hidden while ratings are being collected
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="en" data-theme="dark">
    <head>
        <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Ratings — ASDA Footy 5s</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
        <link rel="stylesheet" href="/static/style.css">
        <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&family=Lora:wght@400;500;600;700&display=swap" rel="stylesheet">
    </head>
    <body>
        <main class="container" style="text-align:center; padding-top:4rem;">
            <h2 style="color:var(--accent);">Ratings Update In Progress</h2>
            <p style="font-size:1.1em; opacity:0.8;">We're collecting ratings from all players. This page will be back once everyone's submitted their scores.</p>
            <p><a href="/rate/public" role="button">Submit Your Ratings</a></p>
            <p><a href="/">← Back to League Table</a></p>
        </main>
    </body>
    </html>
    """)


@app.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, db: Session = Depends(get_db)):
    matches = db.query(Match).order_by(Match.date.desc()).all()
    match_data = []
    for m in matches:
        team_a = [mp.player.name for mp in m.players if mp.team == "A"]
        team_b = [mp.player.name for mp in m.players if mp.team == "B"]
        match_data.append({"match": m, "team_a": team_a, "team_b": team_b})
    user = get_current_user(request)
    return templates.TemplateResponse("matches.html", {"request": request, "match_data": match_data, "user": user})


# ── Public Rating Form (shareable, no login, dropdown) ───────────────────────

@app.get("/rate/public", response_class=HTMLResponse)
async def public_rate_page(request: Request, db: Session = Depends(get_db)):
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    user = get_current_user(request)
    player_id = request.query_params.get("player_id")
    return templates.TemplateResponse("rate_public.html", {
        "request": request, "players": players, "attributes": ATTRIBUTES,
        "user": user, "selected_player_id": int(player_id) if player_id else None,
    })


@app.post("/rate/public", response_class=HTMLResponse)
async def public_rate_submit(request: Request, db: Session = Depends(get_db)):
    import logging
    logger = logging.getLogger("uvicorn.error")

    form = await request.form()
    rater_name = form.get("rater_name", "").strip()
    player_id = form.get("player_id")

    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    user = get_current_user(request)

    if not rater_name:
        return templates.TemplateResponse("rate_public.html", {
            "request": request, "players": players, "attributes": ATTRIBUTES,
            "user": user, "error": "Please enter your name.",
            "selected_player_id": int(player_id) if player_id else None,
        })

    if not player_id:
        return templates.TemplateResponse("rate_public.html", {
            "request": request, "players": players, "attributes": ATTRIBUTES,
            "user": user, "error": "Please select a player to rate.",
            "selected_player_id": None,
        })

    # Prevent self-rating
    pid = int(player_id)
    rated_player = db.query(Player).filter(Player.id == pid).first()
    if rated_player and rated_player.name.lower().strip() == rater_name.lower().strip():
        return templates.TemplateResponse("rate_public.html", {
            "request": request, "players": players, "attributes": ATTRIBUTES,
            "user": user, "error": "You can't rate yourself! Pick a different player.",
            "selected_player_id": pid,
        })

    try:
        for attr in ATTRIBUTES:
            key = f"r_{attr['key']}"
            value = form.get(key)
            if value and value.strip():
                score = float(value)
                if not (1 <= score <= 10):
                    continue
                existing = db.query(PlayerRating).filter(
                    PlayerRating.player_id == pid,
                    PlayerRating.rated_by_name == rater_name,
                    PlayerRating.rated_by_user_id == None,
                    PlayerRating.attribute == attr["key"],
                ).first()
                if existing:
                    existing.score = score
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(PlayerRating(
                        player_id=pid,
                        rated_by_name=rater_name,
                        rated_by_user_id=None,
                        attribute=attr["key"],
                        score=score,
                    ))
                db.flush()

        db.flush()

        # Save comment
        comment = form.get("comment", "").strip()
        if comment:
            first_rating = db.query(PlayerRating).filter(
                PlayerRating.player_id == pid,
                PlayerRating.rated_by_name == rater_name,
            ).first()
            if first_rating:
                first_rating.comment = comment

        update_player_data(db, pid)
        db.commit()
        return RedirectResponse(url="/rate/public?saved=1", status_code=302)

    except Exception as e:
        db.rollback()
        logger.error(f"Public rate submit error: {type(e).__name__}: {e}")
        return templates.TemplateResponse("rate_public.html", {
            "request": request, "players": players, "attributes": ATTRIBUTES,
            "user": user, "selected_player_id": int(player_id) if player_id else None,
            "error": f"Something went wrong: {type(e).__name__}. Please try again.",
        })


# ── API: player list for dynamic dropdown ────────────────────────────────────

@app.get("/api/players")
async def api_players(db: Session = Depends(get_db)):
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    return [{"id": p.id, "name": p.name} for p in players]


# ── Rate Players (authenticated) ────────────────────────────────────────────

@app.get("/rate", response_class=HTMLResponse)
async def rate_page(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()

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
            key = f"r_{player.id}_{attr['key']}"
            value = form.get(key)
            if value and value.strip():
                score = float(value)
                if not (1 <= score <= 10):
                    continue
                existing = db.query(PlayerRating).filter(
                    PlayerRating.player_id == player.id,
                    PlayerRating.rated_by_user_id == user["id"],
                    PlayerRating.attribute == attr["key"],
                ).first()
                if existing:
                    existing.score = score
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(PlayerRating(
                        player_id=player.id,
                        rated_by_user_id=user["id"],
                        attribute=attr["key"],
                        score=score,
                    ))
        update_player_data(db, player.id)
    db.commit()
    return RedirectResponse(url="/rate?saved=1", status_code=302)


# ── Admin: Manage Players ───────────────────────────────────────────────────

@app.get("/admin/players", response_class=HTMLResponse)
async def admin_players(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).order_by(Player.name).all()
    return templates.TemplateResponse("admin_players.html", {"request": request, "players": players, "user": user})


@app.post("/admin/players/add", response_class=HTMLResponse)
async def admin_add_player(request: Request, name: str = Form(...), age_range: str = Form(""), db: Session = Depends(get_db)):
    require_admin(request)
    db.add(Player(name=name, age_range=age_range))
    db.commit()
    return RedirectResponse(url="/admin/players", status_code=302)


@app.post("/admin/players/{player_id}/edit", response_class=HTMLResponse)
async def admin_edit_player(
    request: Request, player_id: int,
    name: str = Form(...), age_range: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    require_admin(request)
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(404)
    player.name = name
    player.age_range = age_range
    player.is_active = is_active
    db.commit()
    return RedirectResponse(url="/admin/players", status_code=302)


# ── Admin: Record Match ─────────────────────────────────────────────────────

@app.get("/admin/match", response_class=HTMLResponse)
async def admin_match(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    return templates.TemplateResponse("admin_match.html", {
        "request": request, "players": players, "user": user, "formats": MATCH_FORMATS,
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

    for pid in form.getlist("team_a"):
        db.add(MatchPlayer(match_id=match.id, player_id=int(pid), team="A"))
    for pid in form.getlist("team_b"):
        db.add(MatchPlayer(match_id=match.id, player_id=int(pid), team="B"))

    db.commit()
    return RedirectResponse(url="/matches", status_code=302)


# ── Admin: Team Generator ───────────────────────────────────────────────────

@app.get("/admin/generate", response_class=HTMLResponse)
async def admin_generate(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    player_archs = {}
    for p in players:
        top = get_player_top_archetypes(db, p.id, n=2)
        player_archs[p.id] = ", ".join(label for label, _ in top) if top else ""
    return templates.TemplateResponse("admin_generate.html", {
        "request": request, "players": players, "user": user,
        "team_a": None, "team_b": None, "formats": MATCH_FORMATS,
        "player_archs": player_archs,
    })


@app.post("/admin/generate", response_class=HTMLResponse)
async def admin_generate_teams(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    form = await request.form()
    selected_ids = form.getlist("players")
    game_format = form.get("format", "5v5")

    players = db.query(Player).filter(Player.is_active == True).order_by(Player.name).all()
    player_archs = {}
    for p in players:
        top = get_player_top_archetypes(db, p.id, n=2)
        player_archs[p.id] = ", ".join(label for label, _ in top) if top else ""

    if len(selected_ids) < 2:
        return templates.TemplateResponse("admin_generate.html", {
            "request": request, "players": players, "user": user,
            "team_a": None, "team_b": None, "error": "Select at least 2 players",
            "formats": MATCH_FORMATS, "player_archs": player_archs,
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
        "formats": MATCH_FORMATS, "selected_format": game_format,
        "player_archs": player_archs,
    })
