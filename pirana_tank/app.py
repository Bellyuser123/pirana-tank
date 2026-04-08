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
