from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "pirana_tank.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = "pirana_tank_secret"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class Pirana(db.Model):
    __tablename__ = 'piranas'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    bank_balance = db.Column(db.Float, default=1000000.0)

class Pitch(db.Model):
    __tablename__ = 'pitches'
    id = db.Column(db.Integer, primary_key=True)
    startup_name = db.Column(db.String(200), nullable=False)
    founder_name = db.Column(db.String(100), nullable=False)
    ask_amount = db.Column(db.Float, nullable=False)
    ask_equity = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=False)

class Offer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pirana_id = db.Column(db.Integer, db.ForeignKey("piranas.id"), nullable=False)
    partner_id = db.Column(db.Integer, db.ForeignKey('piranas.id'))
    pitch_id = db.Column(db.Integer, db.ForeignKey("pitches.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    equity = db.Column(db.Float, nullable=False)
    is_merged = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default="pending")
    pirana = db.relationship("Pirana", foreign_keys=[pirana_id])
    partner = db.relationship("Pirana", foreign_keys=[partner_id])

class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pitch_id = db.Column(db.Integer, db.ForeignKey("pitches.id"), nullable=False)
    pirana_id = db.Column(db.Integer, db.ForeignKey("piranas.id"), nullable=False)
    amount_spent = db.Column(db.Float, nullable=False)
    equity_gained = db.Column(db.Float, nullable=False)
    result = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    pitch = db.relationship("Pitch", backref="history_entries")
    pirana = db.relationship("Pirana", backref="history_entries")

def calc_valuation(amount, equity):
    return amount / (equity / 100) if equity > 0 else 0

@app.before_request
def protect_routes():
    path = request.path
    if path.startswith("/pirana") and "pirana_id" not in session:
        return redirect(url_for("login"))
    if path.startswith("/admin") and path != "/admin/login" and not session.get("admin_logged_in"):
        return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pirana = Pirana.query.filter_by(name=name).first()
        if not pirana:
            pirana = Pirana(name=name, bank_balance=1000000.0)
            db.session.add(pirana)
            db.session.commit()
        session["pirana_id"] = pirana.id
        session.pop("admin_logged_in", None)
        return redirect(url_for("pirana_dashboard"))
    piranas = Pirana.query.all()
    return render_template("login.html", piranas=piranas)

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("pin") == "1234":
        session["admin_logged_in"] = True
        session.pop("pirana_id", None)
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("login") + "?error=Invalid+PIN")

@app.route("/pirana")
def pirana_dashboard():
    pirana = Pirana.query.get(session["pirana_id"])
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    others = Pirana.query.filter(Pirana.id != pirana.id).all()
    history = History.query.filter_by(pirana_id=pirana.id).order_by(History.created_at.desc()).all()
    return render_template("pirana.html", pirana=pirana, active_pitch=active_pitch, other_piranas=others, history=history, calc_valuation=calc_valuation)

@app.route("/api/pirana/offer", methods=["POST"])
def submit_offer():
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    if not active_pitch: return jsonify({"error": "No active pitch"}), 400
    pid = session["pirana_id"]
    amt = float(request.form.get("amount", 0))
    eq = float(request.form.get("equity", 0))
    partner = request.form.get("partner_id")
    merged = partner != "none"
    pid_partner = int(partner) if merged else None
    existing = Offer.query.filter_by(pirana_id=pid, pitch_id=active_pitch.id, status="pending").first()
    if existing:
        existing.amount, existing.equity, existing.is_merged, existing.partner_id = amt, eq, merged, pid_partner
    else:
        db.session.add(Offer(pirana_id=pid, pitch_id=active_pitch.id, amount=amt, equity=eq, is_merged=merged, partner_id=pid_partner))
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/pirana/withdraw", methods=["POST"])
def withdraw_offer():
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    offer = Offer.query.filter_by(pirana_id=session["pirana_id"], pitch_id=active_pitch.id, status="pending").first()
    if offer:
        offer.status = "withdrawn"
        db.session.commit()
    return jsonify({"success": True})

@app.route("/api/pirana/data")
def get_pirana_data():
    pirana = Pirana.query.get(session["pirana_id"])
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    pitch_data = None
    if active_pitch:
        pitch_data = {"startup": active_pitch.startup_name, "founder": active_pitch.founder_name, 
                      "amt": active_pitch.ask_amount, "eq": active_pitch.ask_equity, "val": calc_valuation(active_pitch.ask_amount, active_pitch.ask_equity)}
    return jsonify({"balance": pirana.bank_balance, "pitch": pitch_data})

@app.route("/admin")
def admin_dashboard():
    piranas = Pirana.query.all()
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    offers = Offer.query.filter_by(pitch_id=active_pitch.id, status="pending").all() if active_pitch else []
    history = History.query.order_by(History.created_at.desc()).limit(50).all()
    return render_template("admin.html", piranas=piranas, active_pitch=active_pitch, offers=offers, history=history, calc_valuation=calc_valuation)

@app.route("/api/admin/pitch", methods=["POST"])
def start_pitch():
    Pitch.query.update({"is_active": False})
    db.session.commit()
    p = Pitch(startup_name=request.form["startup_name"], founder_name=request.form["founder_name"],
              ask_amount=float(request.form["ask_amount"]), ask_equity=float(request.form["ask_equity"]), is_active=True)
    db.session.add(p)
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/admin/accept/<int:oid>", methods=["POST"])
def accept_deal(oid):
    offer = Offer.query.get(oid)
    if offer.status != "pending": return jsonify({"error": "Invalid"}), 400
    main = Pirana.query.get(offer.pirana_id)
    share = offer.amount / 2 if offer.is_merged else offer.amount
    if main.bank_balance < share: return jsonify({"error": "Insufficient funds"}), 400
    main.bank_balance -= share
    if offer.is_merged:
        part = Pirana.query.get(offer.partner_id)
        if part.bank_balance < share: return jsonify({"error": "Partner insufficient funds"}), 400
        part.bank_balance -= share
        db.session.add(History(pitch_id=offer.pitch_id, pirana_id=part.id, amount_spent=share, equity_gained=offer.equity/2, result="Won"))
    db.session.add(History(pitch_id=offer.pitch_id, pirana_id=main.id, amount_spent=share, equity_gained=offer.equity if not offer.is_merged else offer.equity/2, result="Won"))
    offer.status = "accepted"
    Pitch.query.get(offer.pitch_id).is_active = False
    for o in Offer.query.filter(Offer.pitch_id==offer.pitch_id, Offer.id!=oid, Offer.status=="pending").all():
        o.status = "rejected"
        db.session.add(History(pitch_id=offer.pitch_id, pirana_id=o.pirana_id, amount_spent=0, equity_gained=0, result="Rejected"))
        if o.partner_id:
            db.session.add(History(pitch_id=offer.pitch_id, pirana_id=o.partner_id, amount_spent=0, equity_gained=0, result="Rejected"))
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/admin/walk-out", methods=["POST"])
def walk_out():
    pitch = Pitch.query.filter_by(is_active=True).first()
    if not pitch: return jsonify({"error": "None"}), 400
    for o in Offer.query.filter_by(pitch_id=pitch.id, status="pending").all():
        o.status = "rejected"
        db.session.add(History(pitch_id=pitch.id, pirana_id=o.pirana_id, amount_spent=0, equity_gained=0, result="Passed"))
    pitch.is_active = False
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/admin/reset", methods=["POST"])
def reset_balances():
    Pirana.query.update({"bank_balance": 1000000.0})
    db.session.commit()
    return jsonify({"success": True})

@app.route("/display")
def display():
    pitch = Pitch.query.filter_by(is_active=True).first()
    offers = Offer.query.filter_by(pitch_id=pitch.id, status="pending").all() if pitch else []
    hist = History.query.filter_by(result="Won").order_by(History.created_at.desc()).limit(10).all()
    return render_template("display.html", active_pitch=pitch, offers=offers, funded_history=hist, calc_valuation=calc_valuation)

@app.route("/api/display/data")
def display_data():
    pitch = Pitch.query.filter_by(is_active=True).first()
    offers = Offer.query.filter_by(pitch_id=pitch.id, status="pending").all() if pitch else []
    hist = History.query.filter_by(result="Won").order_by(History.created_at.desc()).limit(10).all()
    p_data = None
    if pitch:
        p_data = {"startup": pitch.startup_name, "founder": pitch.founder_name, "amt": pitch.ask_amount, "eq": pitch.ask_equity, "val": calc_valuation(pitch.ask_amount, pitch.ask_equity)}
    o_data = [{"name": o.pirana.name, "partner": o.partner.name if o.partner else None, "amt": o.amount, "eq": o.equity, "merged": o.is_merged, "val": calc_valuation(o.amount, o.equity)} for o in offers]
    h_data = [{"startup": h.pitch.startup_name, "amt": h.amount_spent, "eq": h.equity_gained} for h in hist]
    return jsonify({"pitch": p_data, "offers": o_data, "funded": h_data})

if __name__ == "__main__":
    with app.app_context():
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        db.create_all()
    app.run(debug=True, port=5000)
