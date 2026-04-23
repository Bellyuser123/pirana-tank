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
    pitch_id = db.Column(db.Integer, db.ForeignKey("pitches.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    equity = db.Column(db.Float, nullable=False)
    is_merged = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20),
                       default="pending")
    involved_ids = db.Column(db.String(200), nullable=False)
    pending_ids = db.Column(db.String(200), default="")
    creator_id = db.Column(db.Integer, nullable=False)


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


def calc_valuation(amount, equity): return amount / (equity / 100) if equity > 0 else 0


def get_involved_names(ids_str):
    ids = [int(x) for x in ids_str.split(",") if x]
    piranas = Pirana.query.filter(Pirana.id.in_(ids)).all()
    return " & ".join([p.name for p in piranas])


@app.before_request
def protect_routes():
    path = request.path
    if path.startswith("/pirana") or path.startswith("/api/pirana"):
        if "pirana_id" not in session or not db.session.get(Pirana, session["pirana_id"]):
            session.pop("pirana_id", None)
            if path.startswith("/api/"):
                return jsonify({"error": "Session expired"}), 401
            return redirect(url_for("login"))
    if (path.startswith("/admin") or path.startswith("/api/admin")) and path != "/admin/login":
        if not session.get("admin_logged_in"):
            if path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
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
    return render_template("login.html", piranas=Pirana.query.all())


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("pin") == "1234":
        session["admin_logged_in"] = True
        session.pop("pirana_id", None)
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("login"))


@app.route("/pirana")
def pirana_dashboard():
    pirana = db.session.get(Pirana, session["pirana_id"])
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    others = Pirana.query.filter(Pirana.id != pirana.id).all()
    history = History.query.filter_by(pirana_id=pirana.id).order_by(History.created_at.desc()).all()
    return render_template("pirana.html", pirana=pirana, active_pitch=active_pitch, other_piranas=others,
                           history=history)


@app.route("/admin")
def admin_dashboard():
    # We must fetch the active pitch and pass it to the template so it renders the boxes!
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    return render_template("admin.html",
                           piranas=Pirana.query.all(),
                           history=History.query.order_by(History.created_at.desc()).limit(50).all(),
                           active_pitch=active_pitch,
                           calc_valuation=calc_valuation)


@app.route("/display")
def display():
    return render_template("display.html")


@app.route("/api/pirana/data")
def get_pirana_data():
    pid = session["pirana_id"]
    pirana = db.session.get(Pirana, pid)
    active_pitch = Pitch.query.filter_by(is_active=True).first()

    if not active_pitch:
        return jsonify({"balance": pirana.bank_balance, "pitch": None, "concluded": False})

    pitch_data = {"startup": active_pitch.startup_name, "founder": active_pitch.founder_name,
                  "amt": active_pitch.ask_amount, "eq": active_pitch.ask_equity}
    concluded = Offer.query.filter_by(pitch_id=active_pitch.id, status="accepted").first() is not None


    my_offers_db = Offer.query.filter(Offer.pitch_id == active_pitch.id, Offer.involved_ids.like(f"%,{pid},%"),
                                      Offer.status.in_(["pending", "revise_requested", "rejected"])).all()
    my_offers = [
        {"id": o.id, "names": get_involved_names(o.involved_ids), "amt": o.amount, "eq": o.equity, "status": o.status}
        for o in my_offers_db]


    invites_db = Offer.query.filter(Offer.pitch_id == active_pitch.id, Offer.status == "forming",
                                    Offer.pending_ids.like(f"%,{pid},%")).all()
    invites = [{"id": o.id, "creator": db.session.get(Pirana, o.creator_id).name, "amt": o.amount, "eq": o.equity,
                "names": get_involved_names(o.involved_ids)} for o in invites_db]

    return jsonify({"balance": pirana.bank_balance, "pitch": pitch_data, "concluded": concluded, "my_offers": my_offers,
                    "invites": invites})


@app.route("/api/pirana/offer", methods=["POST"])
def submit_offer():
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    if not active_pitch: return jsonify({"error": "No pitch"}), 400

    pid = session["pirana_id"]
    amt = float(request.form.get("amount", 0))
    eq = float(request.form.get("equity", 0))
    partner_ids = request.form.getlist("partners")  # Gets list of checked checkboxes

    if not partner_ids:
        # Create Solo Offer (Updates existing solo if one exists, otherwise creates)
        existing = Offer.query.filter_by(creator_id=pid, pitch_id=active_pitch.id, is_merged=False).first()
        if existing and existing.status in ["pending", "revise_requested", "rejected"]:
            existing.amount, existing.equity, existing.status = amt, eq, "pending"
        else:
            db.session.add(Offer(pitch_id=active_pitch.id, amount=amt, equity=eq, is_merged=False, status="pending",
                                 involved_ids=f",{pid},", creator_id=pid))
    else:
        # Create Merged Collab Offer
        inv_ids = [str(pid)] + partner_ids
        involved_str = "," + ",".join(inv_ids) + ","
        pending_str = "," + ",".join(partner_ids) + ","
        db.session.add(Offer(pitch_id=active_pitch.id, amount=amt, equity=eq, is_merged=True, status="forming",
                             involved_ids=involved_str, pending_ids=pending_str, creator_id=pid))

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/pirana/merge_respond/<int:oid>/<action>", methods=["POST"])
def merge_respond(oid, action):
    offer = db.session.get(Offer, oid)
    pid_str = f",{session['pirana_id']},"
    if offer and offer.status == "forming" and pid_str in offer.pending_ids:
        if action == "accept":
            offer.pending_ids = offer.pending_ids.replace(pid_str, ",")
            if offer.pending_ids.strip(",") == "": offer.status = "pending"  # Everyone agreed!
        else:
            offer.status = "withdrawn"  # Someone declined, kill the deal
        db.session.commit()
    return jsonify({"success": True})


@app.route("/api/pirana/withdraw/<int:oid>", methods=["POST"])
def withdraw_offer(oid):
    offer = db.session.get(Offer, oid)
    if offer and f",{session['pirana_id']}," in offer.involved_ids:
        offer.status = "withdrawn"
        db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/data")
def admin_data():
    active_pitch = Pitch.query.filter_by(is_active=True).first()
    offers = Offer.query.filter(Offer.pitch_id == active_pitch.id,
                                Offer.status != "forming").all() if active_pitch else []
    piranas = Pirana.query.all()
    hist = History.query.order_by(History.created_at.desc()).limit(50).all()

    o_data = [{"id": o.id, "name": get_involved_names(o.involved_ids), "merged": o.is_merged, "amount": o.amount,
               "equity": o.equity, "status": o.status, "val": calc_valuation(o.amount, o.equity)} for o in offers]
    p_data = [{"id": p.id, "name": p.name, "balance": p.bank_balance} for p in piranas]
    h_data = [
        {"startup": h.pitch.startup_name, "pirana": h.pirana.name, "amount": h.amount_spent, "equity": h.equity_gained,
         "result": h.result} for h in hist]
    return jsonify({"offers": o_data, "piranas": p_data, "history": h_data})


@app.route("/api/admin/pitch", methods=["POST"])
def start_pitch():
    Pitch.query.update({"is_active": False})
    p = Pitch(startup_name=request.form["startup_name"], founder_name=request.form["founder_name"],
              ask_amount=float(request.form["ask_amount"]), ask_equity=float(request.form["ask_equity"]),
              is_active=True)
    db.session.add(p)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/accept/<int:oid>", methods=["POST"])
def accept_deal(oid):
    offer = db.session.get(Offer, oid)
    if offer.status not in ["pending", "revise_requested"]: return jsonify({"error": "Invalid"}), 400

    ids = [int(x) for x in offer.involved_ids.split(",") if x]
    share_cost = offer.amount / len(ids)
    share_eq = offer.equity / len(ids)

    # Check balances first
    for p_id in ids:
        if db.session.get(Pirana, p_id).bank_balance < share_cost: return jsonify(
            {"error": "Someone has insufficient funds!"}), 400

    # Process Deal
    for p_id in ids:
        p = db.session.get(Pirana, p_id)
        p.bank_balance -= share_cost
        db.session.add(History(pitch_id=offer.pitch_id, pirana_id=p.id, amount_spent=share_cost, equity_gained=share_eq,
                               result="Won"))

    offer.status = "accepted"

    for o in Offer.query.filter(Offer.pitch_id == offer.pitch_id, Offer.id != oid,
                                Offer.status.in_(["pending", "revise_requested"])).all():
        o.status = "rejected"
        for rej_id in [int(x) for x in o.involved_ids.split(",") if x]:
            db.session.add(
                History(pitch_id=offer.pitch_id, pirana_id=rej_id, amount_spent=0, equity_gained=0, result="Rejected"))

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/revise_offer/<int:oid>", methods=["POST"])
def revise_specific(oid):
    o = db.session.get(Offer, oid)
    if o: o.status = "revise_requested"; db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/reject_offer/<int:oid>", methods=["POST"])
def reject_specific(oid):
    o = db.session.get(Offer, oid)
    if o: o.status = "rejected"; db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/walk-out", methods=["POST"])
def walk_out():
    pitch = Pitch.query.filter_by(is_active=True).first()
    for o in Offer.query.filter(Offer.pitch_id == pitch.id, Offer.status.in_(["pending", "revise_requested"])).all():
        o.status = "rejected"
        for rej_id in [int(x) for x in o.involved_ids.split(",") if x]:
            db.session.add(
                History(pitch_id=pitch.id, pirana_id=rej_id, amount_spent=0, equity_gained=0, result="Passed"))
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/reset", methods=["POST"])
def reset_balances():
    Pirana.query.update({"bank_balance": 1000000.0})
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/display/data")
def display_data():
    pitch = Pitch.query.filter_by(is_active=True).first()
    offers = Offer.query.filter(Offer.pitch_id == pitch.id, Offer.status != "forming").all() if pitch else []
    hist = History.query.filter_by(result="Won").order_by(History.created_at.desc()).limit(10).all()

    p_data = None
    if pitch: p_data = {"startup": pitch.startup_name, "founder": pitch.founder_name, "amt": pitch.ask_amount,
                        "eq": pitch.ask_equity, "val": calc_valuation(pitch.ask_amount, pitch.ask_equity)}
    o_data = [{"name": get_involved_names(o.involved_ids), "amt": o.amount, "eq": o.equity, "merged": o.is_merged,
               "status": o.status, "val": calc_valuation(o.amount, o.equity)} for o in offers]
    h_data = [{"startup": h.pitch.startup_name, "amt": h.amount_spent, "eq": h.equity_gained} for h in hist]
    return jsonify({"pitch": p_data, "offers": o_data, "funded": h_data})


if __name__ == "__main__":
    with app.app_context():
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        db.create_all()
    app.run(debug=True, port=5000)