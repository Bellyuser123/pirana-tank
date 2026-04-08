from __future__ import annotations

import shutil
import textwrap
import zipfile
from pathlib import Path

PROJECT_NAME = "pirana_tank"
ZIP_NAME = "pirana_tank_project.zip"

APP_PY = r'''
import os
from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, func

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "pirana_tank.db")
DEFAULT_BALANCE = 1_000_000.0
ADMIN_PASSWORD = os.environ.get("PIRANA_ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PIRANA_SECRET_KEY", "pirana-tank-local-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Pirana(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    bank_balance = db.Column(db.Float, nullable=False, default=DEFAULT_BALANCE)


class Pitch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    startup_name = db.Column(db.String(120), nullable=False)
    founder_name = db.Column(db.String(120), nullable=False)
    ask_amount = db.Column(db.Float, nullable=False)
    ask_equity = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Offer(db.Model):
    __table_args__ = (UniqueConstraint("pirana_id", "pitch_id", name="uq_offer_pirana_pitch"),)
    id = db.Column(db.Integer, primary_key=True)
    pirana_id = db.Column(db.Integer, db.ForeignKey("pirana.id"), nullable=False)
    pitch_id = db.Column(db.Integer, db.ForeignKey("pitch.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    equity = db.Column(db.Float, nullable=False)
    is_merged = db.Column(db.Boolean, nullable=False, default=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("pirana.id"))
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    pirana = db.relationship("Pirana", foreign_keys=[pirana_id], backref="offers")
    partner = db.relationship("Pirana", foreign_keys=[partner_id], uselist=False)
    pitch = db.relationship("Pitch", backref="offers")


class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pitch_id = db.Column(db.Integer, db.ForeignKey("pitch.id"), nullable=False)
    pirana_id = db.Column(db.Integer, db.ForeignKey("pirana.id"), nullable=False)
    amount_spent = db.Column(db.Float, nullable=False, default=0.0)
    equity_gained = db.Column(db.Float, nullable=False, default=0.0)
    result = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    pitch = db.relationship("Pitch", backref="history_rows")
    pirana = db.relationship("Pirana", backref="history_rows")


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def implied_valuation(amount, equity):
    if amount is None or equity is None or equity <= 0:
        return None
    return amount / (equity / 100.0)


@app.template_filter("money")
def money_filter(value):
    if value is None:
        return "--"
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "--"


@app.template_filter("pct")
def pct_filter(value):
    if value is None:
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    txt = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{txt}%"


@app.context_processor
def inject_helpers():
    return {"implied_valuation": implied_valuation}


def get_current_pirana():
    pid = session.get("pirana_id")
    return db.session.get(Pirana, pid) if pid else None


def get_active_pitch():
    return Pitch.query.filter_by(is_active=True).order_by(Pitch.id.desc()).first()


def get_incoming_invites(pirana_id, pitch_id):
    if not pitch_id:
        return []
    return (
        Offer.query.filter_by(
            pitch_id=pitch_id, partner_id=pirana_id, is_merged=False, status="pending"
        )
        .filter(Offer.pirana_id != pirana_id)
        .order_by(Offer.id.desc())
        .all()
    )


@app.before_request
def strict_session_protection():
    path = request.path or "/"
    if path.startswith("/static/"):
        return None
    if session.get("pirana_id"):
        if path == "/" or path.startswith("/pirana"):
            return None
        return redirect(url_for("pirana_dashboard"))
    if session.get("admin_logged_in"):
        if path.startswith("/admin"):
            return None
        return redirect(url_for("admin_dashboard"))
    return None


@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("pirana_id"):
        return redirect(url_for("pirana_dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Please enter a name.", "error")
            return redirect(url_for("login"))
        pirana = Pirana.query.filter(func.lower(Pirana.name) == name.lower()).first()
        if not pirana:
            pirana = Pirana(name=name, bank_balance=DEFAULT_BALANCE)
            db.session.add(pirana)
            db.session.commit()
        session.clear()
        session["pirana_id"] = pirana.id
        return redirect(url_for("pirana_dashboard"))
    quick_names = [p.name for p in Pirana.query.order_by(Pirana.name.asc()).all()]
    return render_template("login.html", quick_names=quick_names)


@app.route("/pirana/logout", methods=["POST"])
def pirana_logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/pirana")
def pirana_dashboard():
    pirana = get_current_pirana()
    if not pirana:
        return redirect(url_for("login"))
    active_pitch = get_active_pitch()
    own_offer = None
    incoming_invites = []
    if active_pitch:
        own_offer = Offer.query.filter_by(pirana_id=pirana.id, pitch_id=active_pitch.id).first()
        incoming_invites = get_incoming_invites(pirana.id, active_pitch.id)
    history_rows = (
        History.query.filter_by(pirana_id=pirana.id)
        .order_by(History.created_at.desc(), History.id.desc())
        .limit(50)
        .all()
    )
    other_piranas = Pirana.query.filter(Pirana.id != pirana.id).order_by(Pirana.name.asc()).all()
    return render_template(
        "pirana.html",
        pirana=pirana,
        active_pitch=active_pitch,
        own_offer=own_offer,
        incoming_invites=incoming_invites,
        history_rows=history_rows,
        other_piranas=other_piranas,
    )
'''
APP_PY += r'''


@app.route("/pirana/active_pitch_panel")
def pirana_active_pitch_panel():
    pirana = get_current_pirana()
    if not pirana:
        return ""
    active_pitch = get_active_pitch()
    own_offer = None
    incoming_invites = []
    if active_pitch:
        own_offer = Offer.query.filter_by(pirana_id=pirana.id, pitch_id=active_pitch.id).first()
        incoming_invites = get_incoming_invites(pirana.id, active_pitch.id)
    return render_template(
        "partials/pirana_active_pitch_panel.html",
        active_pitch=active_pitch,
        own_offer=own_offer,
        incoming_invites=incoming_invites,
    )


@app.route("/pirana/offer", methods=["POST"])
def pirana_submit_or_update_offer():
    pirana = get_current_pirana()
    if not pirana:
        return redirect(url_for("login"))
    active_pitch = get_active_pitch()
    if not active_pitch:
        flash("No active pitch right now.", "error")
        return redirect(url_for("pirana_dashboard"))
    amount = to_float(request.form.get("amount"))
    equity = to_float(request.form.get("equity"))
    if amount is None or equity is None or amount <= 0 or equity <= 0:
        flash("Amount and equity must be valid positive numbers.", "error")
        return redirect(url_for("pirana_dashboard"))
    if amount > pirana.bank_balance:
        flash("Offer amount cannot exceed your bank balance.", "error")
        return redirect(url_for("pirana_dashboard"))
    offer = Offer.query.filter_by(pirana_id=pirana.id, pitch_id=active_pitch.id).first()
    created = offer is None
    if created:
        offer = Offer(pirana_id=pirana.id, pitch_id=active_pitch.id, amount=amount, equity=equity, status="pending")
        db.session.add(offer)
    else:
        if offer.status in {"accepted", "rejected"}:
            flash("This offer is already closed for the current pitch.", "error")
            return redirect(url_for("pirana_dashboard"))
        offer.amount = amount
        offer.equity = equity
        offer.status = "pending"
        offer.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Offer submitted." if created else "Offer updated.", "success")
    return redirect(url_for("pirana_dashboard"))


@app.route("/pirana/withdraw", methods=["POST"])
def pirana_withdraw_offer():
    pirana = get_current_pirana()
    if not pirana:
        return redirect(url_for("login"))
    active_pitch = get_active_pitch()
    if not active_pitch:
        flash("No active pitch to withdraw from.", "error")
        return redirect(url_for("pirana_dashboard"))
    offer = Offer.query.filter_by(pirana_id=pirana.id, pitch_id=active_pitch.id).first()
    if not offer or offer.status != "pending":
        flash("No pending offer found.", "error")
        return redirect(url_for("pirana_dashboard"))
    if offer.is_merged and offer.partner_id:
        partner_offer = Offer.query.filter_by(
            pirana_id=offer.partner_id, pitch_id=active_pitch.id, status="pending"
        ).first()
        if partner_offer and partner_offer.partner_id == pirana.id:
            partner_offer.is_merged = False
            partner_offer.partner_id = None
            partner_offer.updated_at = datetime.utcnow()
    offer.status = "withdrawn"
    offer.is_merged = False
    offer.partner_id = None
    offer.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Offer withdrawn.", "success")
    return redirect(url_for("pirana_dashboard"))


@app.route("/pirana/invite", methods=["POST"])
def pirana_invite_partner():
    pirana = get_current_pirana()
    if not pirana:
        return redirect(url_for("login"))
    active_pitch = get_active_pitch()
    if not active_pitch:
        flash("No active pitch available for collaboration.", "error")
        return redirect(url_for("pirana_dashboard"))
    partner_id = request.form.get("partner_id", type=int)
    if not partner_id or partner_id == pirana.id:
        flash("Select a valid partner.", "error")
        return redirect(url_for("pirana_dashboard"))
    partner = db.session.get(Pirana, partner_id)
    if not partner:
        flash("Partner does not exist.", "error")
        return redirect(url_for("pirana_dashboard"))
    my_offer = Offer.query.filter_by(pirana_id=pirana.id, pitch_id=active_pitch.id, status="pending").first()
    partner_offer = Offer.query.filter_by(pirana_id=partner_id, pitch_id=active_pitch.id, status="pending").first()
    if not my_offer:
        flash("Submit your own offer first.", "error")
        return redirect(url_for("pirana_dashboard"))
    if not partner_offer:
        flash(f"{partner.name} needs a pending offer first.", "error")
        return redirect(url_for("pirana_dashboard"))
    if my_offer.is_merged or partner_offer.is_merged:
        flash("One of these offers is already merged.", "error")
        return redirect(url_for("pirana_dashboard"))
    my_offer.partner_id = partner_id
    my_offer.is_merged = False
    my_offer.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"Collab invite sent to {partner.name}.", "success")
    return redirect(url_for("pirana_dashboard"))


@app.route("/pirana/respond_invite/<int:offer_id>", methods=["POST"])
def pirana_respond_invite(offer_id):
    pirana = get_current_pirana()
    if not pirana:
        return redirect(url_for("login"))
    active_pitch = get_active_pitch()
    if not active_pitch:
        flash("No active pitch right now.", "error")
        return redirect(url_for("pirana_dashboard"))
    decision = request.form.get("decision", "").lower()
    inviter_offer = Offer.query.filter_by(
        id=offer_id, pitch_id=active_pitch.id, status="pending", partner_id=pirana.id
    ).first()
    if not inviter_offer or inviter_offer.pirana_id == pirana.id:
        flash("Invitation not found.", "error")
        return redirect(url_for("pirana_dashboard"))
    if decision == "accept":
        own_offer = Offer.query.filter_by(
            pirana_id=pirana.id, pitch_id=active_pitch.id, status="pending"
        ).first()
        if not own_offer:
            flash("Submit your own offer before accepting.", "error")
            return redirect(url_for("pirana_dashboard"))
        if own_offer.is_merged or inviter_offer.is_merged:
            flash("Cannot merge because one offer is already merged.", "error")
            return redirect(url_for("pirana_dashboard"))
        inviter_offer.is_merged = True
        inviter_offer.partner_id = pirana.id
        inviter_offer.updated_at = datetime.utcnow()
        own_offer.is_merged = True
        own_offer.partner_id = inviter_offer.pirana_id
        own_offer.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f"Merged with {inviter_offer.pirana.name}.", "success")
        return redirect(url_for("pirana_dashboard"))
    inviter_offer.partner_id = None
    inviter_offer.is_merged = False
    inviter_offer.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Collab invite declined.", "info")
    return redirect(url_for("pirana_dashboard"))


def build_live_offer_cards(pitch):
    if not pitch:
        return []
    offers = Offer.query.filter_by(pitch_id=pitch.id, status="pending").order_by(Offer.id.asc()).all()
    by_pirana = {offer.pirana_id: offer for offer in offers}
    cards = []
    used = set()
    for offer in offers:
        if offer.id in used:
            continue
        if offer.is_merged and offer.partner_id:
            partner = by_pirana.get(offer.partner_id)
            if partner and partner.id not in used and partner.is_merged and partner.partner_id == offer.pirana_id:
                first, second = sorted([offer, partner], key=lambda row: row.id)
                used.add(first.id)
                used.add(second.id)
                total_amount = first.amount + second.amount
                total_equity = first.equity + second.equity
                cards.append(
                    {
                        "offer_id": first.id,
                        "display_name": f"{first.pirana.name} & {second.pirana.name}",
                        "amount": total_amount,
                        "equity": total_equity,
                        "valuation": implied_valuation(total_amount, total_equity),
                        "is_merged": True,
                    }
                )
                continue
        used.add(offer.id)
        cards.append(
            {
                "offer_id": offer.id,
                "display_name": offer.pirana.name,
                "amount": offer.amount,
                "equity": offer.equity,
                "valuation": implied_valuation(offer.amount, offer.equity),
                "is_merged": False,
            }
        )
    cards.sort(key=lambda row: row["amount"], reverse=True)
    return cards


def get_winning_offers(selected_offer):
    winners = [selected_offer]
    if selected_offer.is_merged and selected_offer.partner_id:
        partner = Offer.query.filter_by(
            pitch_id=selected_offer.pitch_id, pirana_id=selected_offer.partner_id, status="pending"
        ).first()
        if partner and partner.is_merged and partner.partner_id == selected_offer.pirana_id:
            winners.append(partner)
    uniq = {}
    for winner in winners:
        uniq[winner.id] = winner
    return list(uniq.values())


def funded_ticker_items():
    wins = History.query.filter_by(result="Won").order_by(History.id.desc()).all()
    grouped = {}
    for row in wins:
        if row.pitch_id not in grouped:
            grouped[row.pitch_id] = {
                "startup_name": row.pitch.startup_name if row.pitch else f"Pitch {row.pitch_id}",
                "piranas": [],
                "total": 0.0,
            }
        grouped[row.pitch_id]["total"] += row.amount_spent or 0.0
        if row.pirana and row.pirana.name not in grouped[row.pitch_id]["piranas"]:
            grouped[row.pitch_id]["piranas"].append(row.pirana.name)
    items = []
    for pitch_id in sorted(grouped.keys(), reverse=True):
        item = grouped[pitch_id]
        items.append(
            {
                "startup_name": item["startup_name"],
                "piranas": " & ".join(item["piranas"]),
                "total": item["total"],
            }
        )
    return items[:15]
'''
APP_PY += r'''


@app.route("/admin", methods=["GET", "POST"])
def admin_dashboard():
    if request.method == "POST" and not session.get("admin_logged_in"):
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin password.", "error")
        return redirect(url_for("admin_dashboard"))
    if not session.get("admin_logged_in"):
        return render_template("admin_login.html")
    active_pitch = get_active_pitch()
    offer_cards = build_live_offer_cards(active_pitch) if active_pitch else []
    piranas = Pirana.query.order_by(Pirana.name.asc()).all()
    return render_template(
        "admin.html", active_pitch=active_pitch, offer_cards=offer_cards, piranas=piranas
    )


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/live_offers_panel")
def admin_live_offers_panel():
    if not session.get("admin_logged_in"):
        return ""
    active_pitch = get_active_pitch()
    offer_cards = build_live_offer_cards(active_pitch) if active_pitch else []
    return render_template(
        "partials/admin_live_offers_panel.html",
        active_pitch=active_pitch,
        offer_cards=offer_cards,
    )


@app.route("/admin/reset_balances", methods=["POST"])
def admin_reset_balances():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    amount = to_float(request.form.get("amount"))
    if amount is None or amount <= 0:
        flash("Starting balance must be positive.", "error")
        return redirect(url_for("admin_dashboard"))
    Pirana.query.update({"bank_balance": amount})
    db.session.commit()
    flash(f"All balances reset to {money_filter(amount)}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/start_pitch", methods=["POST"])
def admin_start_pitch():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    startup_name = request.form.get("startup_name", "").strip()
    founder_name = request.form.get("founder_name", "").strip()
    ask_amount = to_float(request.form.get("ask_amount"))
    ask_equity = to_float(request.form.get("ask_equity"))
    if not startup_name or not founder_name:
        flash("Startup and founder names are required.", "error")
        return redirect(url_for("admin_dashboard"))
    if ask_amount is None or ask_equity is None or ask_amount <= 0 or ask_equity <= 0:
        flash("Ask amount and equity must be positive.", "error")
        return redirect(url_for("admin_dashboard"))
    Pitch.query.update({"is_active": False})
    db.session.add(
        Pitch(
            startup_name=startup_name,
            founder_name=founder_name,
            ask_amount=ask_amount,
            ask_equity=ask_equity,
            is_active=True,
        )
    )
    db.session.commit()
    flash("New pitch started.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/accept_offer/<int:offer_id>", methods=["POST"])
def admin_accept_offer(offer_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    active_pitch = get_active_pitch()
    if not active_pitch:
        flash("No active pitch to settle.", "error")
        return redirect(url_for("admin_dashboard"))
    selected_offer = Offer.query.filter_by(id=offer_id, pitch_id=active_pitch.id, status="pending").first()
    if not selected_offer:
        flash("Offer not found.", "error")
        return redirect(url_for("admin_dashboard"))
    winners = get_winning_offers(selected_offer)
    for winner in winners:
        if winner.pirana.bank_balance < winner.amount:
            flash(f"{winner.pirana.name} has insufficient balance.", "error")
            return redirect(url_for("admin_dashboard"))
    winner_ids = {winner.id for winner in winners}
    for winner in winners:
        winner.status = "accepted"
        winner.updated_at = datetime.utcnow()
        winner.pirana.bank_balance -= winner.amount
        db.session.add(
            History(
                pitch_id=active_pitch.id,
                pirana_id=winner.pirana_id,
                amount_spent=winner.amount,
                equity_gained=winner.equity,
                result="Won",
            )
        )
    all_offers = Offer.query.filter_by(pitch_id=active_pitch.id).all()
    for offer in all_offers:
        if offer.id in winner_ids:
            continue
        if offer.status == "pending":
            offer.status = "rejected"
            offer.updated_at = datetime.utcnow()
            db.session.add(
                History(
                    pitch_id=active_pitch.id,
                    pirana_id=offer.pirana_id,
                    amount_spent=0.0,
                    equity_gained=0.0,
                    result="Rejected",
                )
            )
    active_pitch.is_active = False
    db.session.commit()
    flash("Deal accepted and pitch closed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/walkout", methods=["POST"])
def admin_walkout():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    active_pitch = get_active_pitch()
    if not active_pitch:
        flash("No active pitch to walk out from.", "error")
        return redirect(url_for("admin_dashboard"))
    pending_offers = Offer.query.filter_by(pitch_id=active_pitch.id, status="pending").all()
    for offer in pending_offers:
        offer.status = "rejected"
        offer.updated_at = datetime.utcnow()
        db.session.add(
            History(
                pitch_id=active_pitch.id,
                pirana_id=offer.pirana_id,
                amount_spent=0.0,
                equity_gained=0.0,
                result="Passed",
            )
        )
    active_pitch.is_active = False
    db.session.commit()
    flash("Walk out applied. All pending offers marked as passed.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/display")
def audience_display():
    active_pitch = get_active_pitch()
    offer_cards = build_live_offer_cards(active_pitch) if active_pitch else []
    ticker_items = funded_ticker_items()
    return render_template(
        "display.html",
        active_pitch=active_pitch,
        offer_cards=offer_cards,
        ticker_items=ticker_items,
    )


@app.route("/display/state")
def audience_state():
    active_pitch = get_active_pitch()
    offer_cards = build_live_offer_cards(active_pitch) if active_pitch else []
    ticker_items = funded_ticker_items()
    return render_template(
        "partials/display_state.html",
        active_pitch=active_pitch,
        offer_cards=offer_cards,
        ticker_items=ticker_items,
    )


with app.app_context():
    os.makedirs(DATA_DIR, exist_ok=True)
    db.create_all()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1", port=port, debug=debug_mode)
'''
BASE_HTML = r'''
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pirana Tank</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        brand: { 400: "#38bdf8", 500: "#0ea5e9", 600: "#0284c7" }
                    }
                }
            }
        };
    </script>
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    <link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">
</head>
<body class="min-h-screen bg-slate-950 text-slate-100">
    <div class="mx-auto w-full max-w-7xl px-4 py-6 md:px-8">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                <div class="mb-4 space-y-2">
                    {% for category, message in messages %}
                        {% set tone = "border-sky-500/50 bg-sky-500/15 text-sky-100" %}
                        {% if category == "error" %}{% set tone = "border-rose-500/50 bg-rose-500/15 text-rose-100" %}{% endif %}
                        {% if category == "success" %}{% set tone = "border-emerald-500/50 bg-emerald-500/15 text-emerald-100" %}{% endif %}
                        {% if category == "info" %}{% set tone = "border-amber-500/50 bg-amber-500/15 text-amber-100" %}{% endif %}
                        <div class="rounded-xl border px-4 py-3 text-sm {{ tone }}">{{ message }}</div>
                    {% endfor %}
                </div>
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
    <script src="{{ url_for('static', filename='app.js') }}"></script>
</body>
</html>
'''
LOGIN_HTML = r'''
{% extends "base.html" %}
{% block content %}
<div class="mx-auto mt-6 grid max-w-5xl gap-6 lg:grid-cols-2">
    <section class="glass-card rounded-3xl p-8">
        <p class="text-xs uppercase tracking-[0.2em] text-slate-400">Live Investment Control</p>
        <h1 class="mt-3 text-4xl font-black tracking-tight text-slate-100">Pirana Tank</h1>
        <p class="mt-4 text-slate-300">Password-less investor entry.</p>
        <form method="post" class="mt-8 space-y-4">
            <label for="name" class="text-sm font-medium text-slate-300">Investor Name</label>
            <input id="name" name="name" type="text" required placeholder="e.g., Aman"
                   class="w-full rounded-xl border border-slate-700 bg-slate-900/80 px-4 py-3 text-lg text-slate-100 outline-none ring-brand-500 transition focus:ring-2">
            <button type="submit" class="w-full rounded-xl bg-brand-600 px-4 py-3 text-lg font-bold text-white transition hover:bg-brand-500">
                Enter Pirana Dashboard
            </button>
        </form>
        <a href="{{ url_for('admin_dashboard') }}" class="mt-6 inline-flex text-sm text-sky-300 hover:text-sky-200">Admin Login -></a>
    </section>
    <section class="glass-card rounded-3xl p-8">
        <h2 class="text-xl font-semibold text-slate-100">Quick Select</h2>
        <p class="mt-2 text-sm text-slate-400">Click a name to auto-fill and continue.</p>
        <div class="mt-6 flex flex-wrap gap-3">
            {% if quick_names %}
                {% for name in quick_names %}
                    <button type="button"
                            onclick="document.getElementById('name').value='{{ name|replace(\"'\", \"\\\\'\") }}';document.getElementById('name').focus();"
                            class="rounded-full border border-slate-600 bg-slate-800/70 px-4 py-2 text-sm font-medium text-slate-200 transition hover:border-sky-400 hover:text-sky-200">
                        {{ name }}
                    </button>
                {% endfor %}
            {% else %}
                <p class="text-slate-400">No investors yet.</p>
            {% endif %}
        </div>
    </section>
</div>
{% endblock %}
'''
PIRANA_HTML = r'''
{% extends "base.html" %}
{% block content %}
{% set has_pitch = active_pitch is not none %}
<div class="flex flex-wrap items-center justify-between gap-4">
    <div>
        <p class="text-xs uppercase tracking-[0.18em] text-slate-400">Investor Console</p>
        <h1 class="text-3xl font-black text-slate-100">{{ pirana.name }}</h1>
    </div>
    <div class="flex items-center gap-3">
        <div class="rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-100">
            Bank Balance: <span class="font-bold">{{ pirana.bank_balance|money }}</span>
        </div>
        <form method="post" action="{{ url_for('pirana_logout') }}">
            <button class="rounded-xl border border-slate-600 bg-slate-800 px-4 py-2 text-sm text-slate-200 hover:border-slate-500">Logout</button>
        </form>
    </div>
</div>

<div class="mt-6 grid gap-6 xl:grid-cols-3">
    <div class="space-y-6 xl:col-span-2">
        <div id="active-pitch-panel" hx-get="{{ url_for('pirana_active_pitch_panel') }}" hx-trigger="load, every 2s" hx-swap="innerHTML">
            {% include "partials/pirana_active_pitch_panel.html" %}
        </div>

        <section class="glass-card rounded-2xl p-6">
            <h2 class="text-xl font-bold text-slate-100">Action Area</h2>
            <p class="mt-1 text-sm text-slate-400">Enter amount and equity. Valuation auto-calculates as you type.</p>
            <form method="post" action="{{ url_for('pirana_submit_or_update_offer') }}" class="mt-5 grid gap-4 md:grid-cols-2">
                <div>
                    <label for="offer_amount" class="text-sm text-slate-300">Amount ($)</label>
                    <input id="offer_amount" name="amount" type="number" min="1" step="0.01" value="{{ own_offer.amount if own_offer else '' }}"
                           {% if not has_pitch %}disabled{% endif %} required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2 disabled:opacity-50">
                </div>
                <div>
                    <label for="offer_equity" class="text-sm text-slate-300">Equity (%)</label>
                    <input id="offer_equity" name="equity" type="number" min="0.01" step="0.01" value="{{ own_offer.equity if own_offer else '' }}"
                           {% if not has_pitch %}disabled{% endif %} required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2 disabled:opacity-50">
                </div>
                <div class="md:col-span-2 rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3">
                    <p class="text-xs uppercase tracking-wider text-slate-400">Implied Valuation</p>
                    <p id="offer_valuation" class="mt-1 text-2xl font-black text-sky-300">--</p>
                </div>
                <div class="md:col-span-2 flex flex-wrap gap-3">
                    <button type="submit" {% if not has_pitch %}disabled{% endif %}
                            class="rounded-xl bg-brand-600 px-6 py-3 text-sm font-bold text-white hover:bg-brand-500 disabled:opacity-50">
                        Submit Offer
                    </button>
                    <button type="submit" name="action" value="update" {% if not has_pitch %}disabled{% endif %}
                            class="rounded-xl border border-brand-500/60 bg-brand-500/10 px-6 py-3 text-sm font-bold text-brand-200 hover:bg-brand-500/20 disabled:opacity-50">
                        Update Offer
                    </button>
                </div>
            </form>
            <form method="post" action="{{ url_for('pirana_withdraw_offer') }}" class="mt-4">
                <button {% if not has_pitch %}disabled{% endif %}
                        class="rounded-xl border border-rose-500/60 bg-rose-500/10 px-5 py-2 text-sm font-semibold text-rose-200 hover:bg-rose-500/20 disabled:opacity-50">
                    Withdraw
                </button>
            </form>
        </section>

        <section class="glass-card rounded-2xl p-6">
            <h2 class="text-xl font-bold text-slate-100">Collaboration (Merge)</h2>
            <p class="mt-1 text-sm text-slate-400">Invite another Pirana to combine offers into one merged bid.</p>
            <form method="post" action="{{ url_for('pirana_invite_partner') }}" class="mt-4 flex flex-wrap items-end gap-3">
                <div class="min-w-[260px] flex-1">
                    <label for="partner_id" class="text-sm text-slate-300">Invite Partner</label>
                    <select id="partner_id" name="partner_id" {% if not has_pitch or not other_piranas %}disabled{% endif %}
                            class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2 disabled:opacity-50">
                        {% for other in other_piranas %}
                            <option value="{{ other.id }}">{{ other.name }}</option>
                        {% endfor %}
                    </select>
                </div>
                <button {% if not has_pitch or not other_piranas %}disabled{% endif %}
                        class="rounded-xl bg-slate-800 px-5 py-3 text-sm font-bold text-slate-100 hover:bg-slate-700 disabled:opacity-50">
                    Send Invite
                </button>
            </form>
        </section>
    </div>

    <aside class="glass-card rounded-2xl p-6">
        <h2 class="text-xl font-bold text-slate-100">History</h2>
        <p class="mt-1 text-sm text-slate-400">Won, rejected, and passed deals.</p>
        <div class="mt-4 max-h-[540px] overflow-y-auto rounded-xl border border-slate-800">
            <table class="w-full text-sm">
                <thead class="sticky top-0 bg-slate-900/95 text-xs uppercase tracking-wider text-slate-400">
                    <tr>
                        <th class="px-3 py-2 text-left">Startup</th>
                        <th class="px-3 py-2 text-left">Result</th>
                        <th class="px-3 py-2 text-left">Spent</th>
                        <th class="px-3 py-2 text-left">Equity</th>
                    </tr>
                </thead>
                <tbody>
                    {% if history_rows %}
                        {% for row in history_rows %}
                            <tr class="border-t border-slate-800/80">
                                <td class="px-3 py-2">{{ row.pitch.startup_name if row.pitch else "N/A" }}</td>
                                <td class="px-3 py-2">
                                    {% set tone = "text-slate-200" %}
                                    {% if row.result == "Won" %}{% set tone = "text-emerald-300" %}{% endif %}
                                    {% if row.result == "Rejected" %}{% set tone = "text-rose-300" %}{% endif %}
                                    {% if row.result == "Passed" %}{% set tone = "text-amber-300" %}{% endif %}
                                    <span class="font-semibold {{ tone }}">{{ row.result }}</span>
                                </td>
                                <td class="px-3 py-2">{{ row.amount_spent|money }}</td>
                                <td class="px-3 py-2">{{ row.equity_gained|pct }}</td>
                            </tr>
                        {% endfor %}
                    {% else %}
                        <tr><td colspan="4" class="px-3 py-4 text-center text-slate-400">No history yet.</td></tr>
                    {% endif %}
                </tbody>
            </table>
        </div>
    </aside>
</div>
{% endblock %}
'''
PIRANA_ACTIVE_PITCH_PANEL_HTML = r'''
{% if active_pitch %}
    <section class="glass-card rounded-2xl p-6">
        <div class="flex flex-wrap items-start justify-between gap-4">
            <div>
                <p class="text-xs uppercase tracking-[0.2em] text-slate-400">Active Pitch</p>
                <h2 class="mt-2 text-3xl font-black text-slate-100">{{ active_pitch.startup_name }}</h2>
                <p class="mt-1 text-lg text-slate-300">Founder: {{ active_pitch.founder_name }}</p>
            </div>
            <div class="rounded-xl border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
                <p class="text-slate-400">Ask</p>
                <p class="font-semibold text-slate-100">{{ active_pitch.ask_amount|money }} for {{ active_pitch.ask_equity|pct }}</p>
                <p class="mt-1 text-sky-300">Valuation: {{ implied_valuation(active_pitch.ask_amount, active_pitch.ask_equity)|money }}</p>
            </div>
        </div>
    </section>
    <div class="grid gap-4 md:grid-cols-2">
        <section class="glass-card rounded-2xl p-5">
            <h3 class="text-lg font-bold text-slate-100">Your Live Offer</h3>
            {% if own_offer and own_offer.status == "pending" %}
                <div class="mt-3 rounded-xl border border-slate-700 bg-slate-900/60 p-4">
                    <p class="text-slate-200">{{ own_offer.amount|money }} for {{ own_offer.equity|pct }}</p>
                    <p class="text-sky-300">Valuation: {{ implied_valuation(own_offer.amount, own_offer.equity)|money }}</p>
                    {% if own_offer.is_merged and own_offer.partner_id and own_offer.partner %}
                        <p class="mt-2 inline-flex rounded-full border border-violet-500/50 bg-violet-500/10 px-3 py-1 text-xs font-semibold text-violet-200">
                            Merged with {{ own_offer.partner.name }}
                        </p>
                    {% elif own_offer.partner_id and own_offer.partner %}
                        <p class="mt-2 text-xs text-amber-300">Invite pending with {{ own_offer.partner.name }}</p>
                    {% endif %}
                </div>
            {% else %}
                <p class="mt-3 text-slate-400">No pending offer yet.</p>
            {% endif %}
        </section>
        <section class="glass-card rounded-2xl p-5">
            <h3 class="text-lg font-bold text-slate-100">Incoming Collab Invites</h3>
            {% if incoming_invites %}
                <div class="mt-3 space-y-3">
                    {% for invite in incoming_invites %}
                        <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
                            <p class="font-semibold text-slate-100">{{ invite.pirana.name }}</p>
                            <p class="text-sm text-slate-300">{{ invite.amount|money }} for {{ invite.equity|pct }}</p>
                            <p class="text-xs text-sky-300">Valuation: {{ implied_valuation(invite.amount, invite.equity)|money }}</p>
                            <form method="post" action="{{ url_for('pirana_respond_invite', offer_id=invite.id) }}" class="mt-3 flex gap-2">
                                <button name="decision" value="accept" class="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-500">Accept</button>
                                <button name="decision" value="decline" class="rounded-lg border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-xs font-bold text-rose-200 hover:bg-rose-500/20">Decline</button>
                            </form>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <p class="mt-3 text-slate-400">No pending invites.</p>
            {% endif %}
        </section>
    </div>
{% else %}
    <section class="glass-card rounded-2xl p-6">
        <p class="text-xs uppercase tracking-[0.2em] text-slate-400">Active Pitch</p>
        <h2 class="mt-2 text-2xl font-black text-slate-200">Waiting for the next startup...</h2>
        <p class="mt-2 text-slate-400">Showrunner has not started a pitch yet.</p>
    </section>
{% endif %}
'''
ADMIN_LOGIN_HTML = r'''
{% extends "base.html" %}
{% block content %}
<div class="mx-auto mt-10 max-w-xl">
    <section class="glass-card rounded-3xl p-8">
        <p class="text-xs uppercase tracking-[0.2em] text-slate-400">Showrunner Access</p>
        <h1 class="mt-2 text-3xl font-black text-slate-100">Admin Control</h1>
        <form method="post" class="mt-6 space-y-4">
            <label for="password" class="text-sm text-slate-300">Admin Password</label>
            <input id="password" name="password" type="password" required
                   class="w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2">
            <button type="submit" class="w-full rounded-xl bg-brand-600 px-4 py-3 font-bold text-white hover:bg-brand-500">
                Enter Admin Panel
            </button>
        </form>
        <a href="{{ url_for('login') }}" class="mt-4 inline-flex text-sm text-sky-300 hover:text-sky-200">Back to Investor Login -></a>
    </section>
</div>
{% endblock %}
'''
ADMIN_HTML = r'''
{% extends "base.html" %}
{% block content %}
<div class="flex flex-wrap items-center justify-between gap-4">
    <div>
        <p class="text-xs uppercase tracking-[0.2em] text-slate-400">Showrunner & Contestant Voice</p>
        <h1 class="text-3xl font-black text-slate-100">Admin Control Deck</h1>
    </div>
    <form method="post" action="{{ url_for('admin_logout') }}">
        <button class="rounded-xl border border-slate-600 bg-slate-800 px-4 py-2 text-sm text-slate-200 hover:border-slate-500">Logout</button>
    </form>
</div>

<div class="mt-6 grid gap-6 xl:grid-cols-3">
    <div class="space-y-6 xl:col-span-2">
        <section class="glass-card rounded-2xl p-6">
            <h2 class="text-xl font-bold text-slate-100">Setup</h2>
            <p class="mt-1 text-sm text-slate-400">Set/reset starting balances for all Piranas.</p>
            <form method="post" action="{{ url_for('admin_reset_balances') }}" class="mt-4 flex flex-wrap items-end gap-3">
                <div class="min-w-[240px]">
                    <label for="balance_amount" class="text-sm text-slate-300">Starting Balance ($)</label>
                    <input id="balance_amount" name="amount" type="number" min="1" step="0.01" value="1000000" required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2">
                </div>
                <button class="rounded-xl bg-brand-600 px-5 py-3 text-sm font-bold text-white hover:bg-brand-500">Apply to All Piranas</button>
            </form>
        </section>

        <section class="glass-card rounded-2xl p-6">
            <h2 class="text-xl font-bold text-slate-100">Pitch Control</h2>
            <p class="mt-1 text-sm text-slate-400">Start a new pitch and close any previous active one.</p>
            <form method="post" action="{{ url_for('admin_start_pitch') }}" class="mt-5 grid gap-4 md:grid-cols-2">
                <div>
                    <label class="text-sm text-slate-300">Startup Name</label>
                    <input name="startup_name" type="text" required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2">
                </div>
                <div>
                    <label class="text-sm text-slate-300">Founder Name</label>
                    <input name="founder_name" type="text" required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2">
                </div>
                <div>
                    <label for="ask_amount" class="text-sm text-slate-300">Ask Amount ($)</label>
                    <input id="ask_amount" name="ask_amount" type="number" min="1" step="0.01" required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2">
                </div>
                <div>
                    <label for="ask_equity" class="text-sm text-slate-300">Ask Equity (%)</label>
                    <input id="ask_equity" name="ask_equity" type="number" min="0.01" step="0.01" required
                           class="mt-2 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-slate-100 outline-none ring-brand-500 focus:ring-2">
                </div>
                <div class="md:col-span-2 rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3">
                    <p class="text-xs uppercase tracking-wider text-slate-400">Implied Ask Valuation</p>
                    <p id="ask_valuation" class="mt-1 text-2xl font-black text-sky-300">--</p>
                </div>
                <div class="md:col-span-2">
                    <button class="rounded-xl bg-emerald-600 px-6 py-3 text-sm font-bold text-white hover:bg-emerald-500">Start Pitch</button>
                </div>
            </form>
            {% if active_pitch %}
                <div class="mt-5 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm text-emerald-100">
                    Live: {{ active_pitch.startup_name }} ({{ active_pitch.founder_name }}) asking {{ active_pitch.ask_amount|money }} for {{ active_pitch.ask_equity|pct }}.
                </div>
            {% else %}
                <div class="mt-5 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-sm text-slate-300">No active pitch.</div>
            {% endif %}
        </section>

        <section class="glass-card rounded-2xl p-6">
            <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 class="text-xl font-bold text-slate-100">Live Offers Board</h2>
                    <p class="mt-1 text-sm text-slate-400">Updates every 2 seconds.</p>
                </div>
                <form method="post" action="{{ url_for('admin_walkout') }}">
                    <button {% if not active_pitch %}disabled{% endif %}
                            class="rounded-xl border border-rose-500/60 bg-rose-500/10 px-4 py-2 text-sm font-bold text-rose-200 hover:bg-rose-500/20 disabled:opacity-50">
                        Walk Out (Pass All)
                    </button>
                </form>
            </div>
            <div id="admin-live-offers" class="mt-4" hx-get="{{ url_for('admin_live_offers_panel') }}" hx-trigger="load, every 2s" hx-swap="innerHTML">
                {% include "partials/admin_live_offers_panel.html" %}
            </div>
        </section>
    </div>

    <aside class="glass-card rounded-2xl p-6">
        <h2 class="text-xl font-bold text-slate-100">Pirana Balances</h2>
        <div class="mt-4 space-y-2">
            {% if piranas %}
                {% for pirana in piranas %}
                    <div class="flex items-center justify-between rounded-xl border border-slate-700 bg-slate-900/60 px-3 py-2">
                        <span class="font-medium text-slate-200">{{ pirana.name }}</span>
                        <span class="text-emerald-300">{{ pirana.bank_balance|money }}</span>
                    </div>
                {% endfor %}
            {% else %}
                <p class="text-slate-400">No Piranas yet.</p>
            {% endif %}
        </div>
    </aside>
</div>
{% endblock %}
'''
ADMIN_LIVE_OFFERS_PANEL_HTML = r'''
{% if active_pitch %}
    {% if offer_cards %}
        <div class="grid gap-4 md:grid-cols-2">
            {% for card in offer_cards %}
                <div class="rounded-2xl border border-slate-700 bg-slate-900/70 p-4">
                    <div class="flex items-center justify-between gap-3">
                        <h3 class="text-lg font-bold text-slate-100">{{ card.display_name }}</h3>
                        {% if card.is_merged %}
                            <span class="rounded-full border border-violet-500/60 bg-violet-500/10 px-2 py-1 text-xs font-semibold text-violet-200">Merged</span>
                        {% endif %}
                    </div>
                    <p class="mt-3 text-slate-200">{{ card.amount|money }} for {{ card.equity|pct }}</p>
                    <p class="text-sky-300">Valuation: {{ card.valuation|money }}</p>
                    <form method="post" action="{{ url_for('admin_accept_offer', offer_id=card.offer_id) }}" class="mt-4">
                        <button class="w-full rounded-xl bg-emerald-600 px-4 py-3 text-sm font-black text-white hover:bg-emerald-500">ACCEPT DEAL</button>
                    </form>
                </div>
            {% endfor %}
        </div>
    {% else %}
        <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-slate-300">No live offers yet for this pitch.</div>
    {% endif %}
{% else %}
    <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-slate-300">Start a pitch to receive live offers.</div>
{% endif %}
'''
DISPLAY_HTML = r'''
{% extends "base.html" %}
{% block content %}
<div id="display-state" hx-get="{{ url_for('audience_state') }}" hx-trigger="load, every 2s" hx-swap="innerHTML">
    {% include "partials/display_state.html" %}
</div>
{% endblock %}
'''
DISPLAY_STATE_HTML = r'''
<section class="glass-card rounded-2xl overflow-hidden">
    <div class="border-b border-slate-700/80 px-4 py-3 text-xs uppercase tracking-[0.2em] text-slate-400">Funded Startups Ticker</div>
    <div class="overflow-hidden">
        <div class="ticker-track">
            {% if ticker_items %}
                {% for item in ticker_items %}
                    <span class="ticker-chip">{{ item.startup_name }} • {{ item.piranas }} • {{ item.total|money }}</span>
                {% endfor %}
                {% for item in ticker_items %}
                    <span class="ticker-chip">{{ item.startup_name }} • {{ item.piranas }} • {{ item.total|money }}</span>
                {% endfor %}
            {% else %}
                <span class="ticker-chip">No funded startups yet</span>
                <span class="ticker-chip">No funded startups yet</span>
            {% endif %}
        </div>
    </div>
</section>

{% if active_pitch %}
    <section class="mt-6 rounded-3xl border border-brand-500/40 bg-slate-900/75 p-8 shadow-[0_0_60px_rgba(14,165,233,0.2)]">
        <p class="text-center text-sm uppercase tracking-[0.4em] text-slate-400">Main Stage</p>
        <h1 class="mt-4 text-center text-5xl font-black tracking-tight text-slate-100 md:text-7xl">{{ active_pitch.startup_name }}</h1>
        <p class="mt-3 text-center text-xl text-slate-300 md:text-3xl">Founder: <span class="font-semibold text-sky-300">{{ active_pitch.founder_name }}</span></p>
        <div class="mt-6 grid gap-4 md:grid-cols-3">
            <div class="rounded-2xl border border-slate-700 bg-slate-900/60 p-4 text-center">
                <p class="text-xs uppercase tracking-wider text-slate-400">Ask Amount</p>
                <p class="mt-1 text-3xl font-black text-emerald-300">{{ active_pitch.ask_amount|money }}</p>
            </div>
            <div class="rounded-2xl border border-slate-700 bg-slate-900/60 p-4 text-center">
                <p class="text-xs uppercase tracking-wider text-slate-400">Ask Equity</p>
                <p class="mt-1 text-3xl font-black text-amber-300">{{ active_pitch.ask_equity|pct }}</p>
            </div>
            <div class="rounded-2xl border border-slate-700 bg-slate-900/60 p-4 text-center">
                <p class="text-xs uppercase tracking-wider text-slate-400">Ask Valuation</p>
                <p class="mt-1 text-3xl font-black text-sky-300">{{ implied_valuation(active_pitch.ask_amount, active_pitch.ask_equity)|money }}</p>
            </div>
        </div>
    </section>
    <section class="mt-6">
        <div class="mb-3 flex items-center justify-between">
            <h2 class="text-2xl font-black text-slate-100">Live Offers</h2>
            <p class="text-sm text-slate-400">Auto-refreshing every 2 seconds</p>
        </div>
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {% if offer_cards %}
                {% for card in offer_cards %}
                    <article class="rounded-2xl border border-slate-700 bg-slate-900/70 p-5">
                        <div class="flex items-center justify-between gap-2">
                            <h3 class="text-lg font-bold text-slate-100">{{ card.display_name }}</h3>
                            {% if card.is_merged %}
                                <span class="rounded-full border border-violet-500/60 bg-violet-500/10 px-2 py-1 text-xs font-semibold text-violet-200">Merged</span>
                            {% endif %}
                        </div>
                        <p class="mt-3 text-slate-200">{{ card.amount|money }} for {{ card.equity|pct }}</p>
                        <p class="text-sky-300">Valuation: {{ card.valuation|money }}</p>
                    </article>
                {% endfor %}
            {% else %}
                <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-slate-300 sm:col-span-2 lg:col-span-3">Waiting for offers from the Piranas...</div>
            {% endif %}
        </div>
    </section>
{% else %}
    <section class="mt-6 rounded-3xl border border-slate-700 bg-slate-900/60 p-10 text-center">
        <h2 class="text-4xl font-black text-slate-100">No Active Pitch</h2>
        <p class="mt-3 text-slate-400">The next startup will appear here soon.</p>
    </section>
{% endif %}
'''
APP_JS = r'''
function parseNum(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
}

function computeValuation(amount, equity) {
    if (amount > 0 && equity > 0) {
        return amount / (equity / 100);
    }
    return 0;
}

function formatMoney(value) {
    return "$" + Math.round(value).toLocaleString();
}

function wireValuation(amountId, equityId, targetId) {
    const amountInput = document.getElementById(amountId);
    const equityInput = document.getElementById(equityId);
    const target = document.getElementById(targetId);
    if (!amountInput || !equityInput || !target) {
        return;
    }
    const refresh = () => {
        const amount = parseNum(amountInput.value);
        const equity = parseNum(equityInput.value);
        const valuation = computeValuation(amount, equity);
        target.textContent = valuation > 0 ? formatMoney(valuation) : "--";
    };
    amountInput.addEventListener("input", refresh);
    equityInput.addEventListener("input", refresh);
    amountInput.addEventListener("change", refresh);
    equityInput.addEventListener("change", refresh);
    refresh();
}

document.addEventListener("DOMContentLoaded", () => {
    wireValuation("offer_amount", "offer_equity", "offer_valuation");
    wireValuation("ask_amount", "ask_equity", "ask_valuation");
});
'''
STYLES_CSS = r'''
body {
    background:
        radial-gradient(circle at 15% 20%, rgba(14, 165, 233, 0.16), transparent 32%),
        radial-gradient(circle at 85% 8%, rgba(16, 185, 129, 0.14), transparent 30%),
        radial-gradient(circle at 45% 110%, rgba(59, 130, 246, 0.2), transparent 42%),
        #020617;
}

.glass-card {
    border: 1px solid rgba(71, 85, 105, 0.5);
    background: linear-gradient(145deg, rgba(15, 23, 42, 0.86), rgba(2, 6, 23, 0.82));
    box-shadow: 0 14px 45px rgba(2, 6, 23, 0.45);
    backdrop-filter: blur(4px);
}

.ticker-track {
    display: inline-flex;
    min-width: 200%;
    gap: 1rem;
    padding: 0.8rem 1rem;
    white-space: nowrap;
    animation: marquee 24s linear infinite;
}

.ticker-chip {
    border-radius: 9999px;
    border: 1px solid rgba(56, 189, 248, 0.4);
    background: rgba(14, 165, 233, 0.12);
    color: #e0f2fe;
    font-size: 0.8rem;
    padding: 0.35rem 0.75rem;
}

@keyframes marquee {
    0% { transform: translateX(0%); }
    100% { transform: translateX(-50%); }
}
'''
REQUIREMENTS_TXT = r'''
Flask>=3.0,<4.0
Flask-SQLAlchemy>=3.1,<4.0
SQLAlchemy>=2.0,<3.0
'''
README_MD = r'''
# Pirana Tank

Locally hosted Flask + SQLite control system for a live investment show.

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python app.py`
4. Open:
   - Investor login: `http://127.0.0.1:5000/`
   - Admin: `http://127.0.0.1:5000/admin`
   - Audience display: `http://127.0.0.1:5000/display`

Default admin password: `admin123` (or set `PIRANA_ADMIN_PASSWORD`).
'''

FILES = {
    "app.py": APP_PY,
    "requirements.txt": REQUIREMENTS_TXT,
    "README.md": README_MD,
    "templates/base.html": BASE_HTML,
    "templates/login.html": LOGIN_HTML,
    "templates/pirana.html": PIRANA_HTML,
    "templates/admin_login.html": ADMIN_LOGIN_HTML,
    "templates/admin.html": ADMIN_HTML,
    "templates/display.html": DISPLAY_HTML,
    "templates/partials/pirana_active_pitch_panel.html": PIRANA_ACTIVE_PITCH_PANEL_HTML,
    "templates/partials/admin_live_offers_panel.html": ADMIN_LIVE_OFFERS_PANEL_HTML,
    "templates/partials/display_state.html": DISPLAY_STATE_HTML,
    "static/app.js": APP_JS,
    "static/styles.css": STYLES_CSS,
    "data/.gitkeep": "",
}


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def main() -> None:
    root = Path.cwd()
    project_dir = root / PROJECT_NAME
    zip_path = root / ZIP_NAME

    if project_dir.exists():
        shutil.rmtree(project_dir)
    if zip_path.exists():
        zip_path.unlink()

    for folder in ("templates", "templates/partials", "static", "data"):
        (project_dir / folder).mkdir(parents=True, exist_ok=True)

    for relative_path, content in FILES.items():
        write_file(project_dir / relative_path, content)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in project_dir.rglob("*"):
            archive.write(file_path, file_path.relative_to(root))

    print(f"Project generated at: {project_dir}")
    print(f"Zip archive created: {zip_path}")


if __name__ == "__main__":
    main()
