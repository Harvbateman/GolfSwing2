import os, uuid, random
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import stripe

# =========================
#  Environment / Database
# =========================
load_dotenv()

DB_PATH = "sqlite:///./golf_swing.db"
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    handicap = Column(Integer, nullable=True)
    style_choice = Column(String, default="classic")
    is_premium = Column(Boolean, default=False)
    subscription_plan = Column(String, nullable=True)

class Swing(Base):
    __tablename__ = "swings"
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String)
    s3_video_path = Column(String)
    processed = Column(Boolean, default=False)
    overall_score = Column(Integer, nullable=True)
    power = Column(Integer, nullable=True)
    accuracy = Column(Integer, nullable=True)
    consistency = Column(Integer, nullable=True)
    balance = Column(Integer, nullable=True)
    style_score = Column(Integer, nullable=True)
    style_label = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# Stripe test key (or real key). Put these in a .env file for convenience.
# .env example:
# STRIPE_SECRET_KEY=sk_test_...
# STRIPE_PRICE_ID=price_...
# STRIPE_WEBHOOK_SECRET=whsec_...
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_replace_me")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "price_replace_me")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# =========================
#  Simple scoring (placeholder)
# =========================
def compute_base_attributes():
    # Placeholder values so your app works end-to-end.
    return {
        "power":       random.randint(55, 90),
        "accuracy":    random.randint(50, 90),
        "consistency": random.randint(45, 90),
        "balance":     random.randint(55, 95),
        "style":       random.randint(50, 95),
    }

def apply_style_bias(attrs, style_choice):
    mods = {
        "classic":   {"accuracy": 1.05, "consistency": 1.06, "style": 1.05},
        "power":     {"power": 1.12, "accuracy": 0.98},
        "flashy":    {"style": 1.20, "consistency": 0.95},
        "minimalist":{"consistency": 1.10, "balance": 1.05, "style": 0.95},
    }.get(style_choice, {})
    for k, m in mods.items():
        if k in attrs:
            attrs[k] = int(min(100, attrs[k] * m))
    return attrs

def overall_from_attrs(attrs):
    return int(round(sum(attrs.values()) / len(attrs)))

def process_swing(video_path, style_choice="classic"):
    attrs = compute_base_attributes()
    attrs = apply_style_bias(attrs, style_choice)
    overall = overall_from_attrs(attrs)
    return attrs, overall

# =========================
#  FastAPI app + HTML UI
# =========================
app = FastAPI(title="Golf Swing Analyzer (Single File, with Subscriptions)")

@app.get("/", response_class=HTMLResponse)
def root():
    # Simple inline UI (upload + player card + upgrade button)
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Golf Swing Analyzer</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; background: #f4f4f9; }}
    h1 {{ color: #2a5d84; }}
    #upload-box {{ margin-bottom: 1.5rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }}
    button {{ padding: 0.6rem 1rem; background: #2a5d84; color: white; border: none; cursor: pointer; border-radius: 6px; }}
    button:hover {{ background: #1c415a; }}
    .card {{ background: white; padding: 1rem; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.12); width: 340px; }}
    .hidden {{ display: none; }}
    ul {{ list-style: none; padding-left: 0; }}
    li {{ padding: .2rem 0; }}
    #status {{ margin: .75rem 0; color: #444; }}
    .row {{ display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }}
    .muted {{ color: #777; font-size: 0.9rem; }}
    input[type="file"] {{ max-width: 240px; }}
    #user-pill {{ background:#eee; border-radius: 999px; padding: .25rem .6rem; }}
  </style>
</head>
<body>
  <h1>🏌️ Golf Swing Analyzer</h1>

  <div class="row">
    <span id="user-pill" class="muted">User: <span id="user-id-label">…creating…</span></span>
    <span class="muted">Subscription: <strong id="sub-status">Checking…</strong></span>
    <button id="upgrade-btn" onclick="upgrade()">Upgrade to Premium</button>
  </div>

  <div id="upload-box">
    <input type="file" id="swing-file" accept="video/*">
    <select id="style">
      <option value="classic">Classic</option>
      <option value="power">Power</option>
      <option value="flashy">Flashy</option>
      <option value="minimalist">Minimalist</option>
    </select>
    <button onclick="uploadSwing()">Upload Swing</button>
  </div>

  <div id="status"></div>

  <div id="player-card" class="hidden card">
    <h2>Player Card</h2>
    <p><strong>Overall Score:</strong> <span id="overall"></span>/100</p>
    <ul>
      <li>Power: <span id="power"></span></li>
      <li>Accuracy: <span id="accuracy"></span></li>
      <li>Consistency: <span id="consistency"></span></li>
      <li>Balance: <span id="balance"></span></li>
      <li>Style: <span id="style-stat"></span></li>
    </ul>
  </div>

  <script>
    const qs = new URLSearchParams(location.search);
    // Maintain a user id in localStorage
    async function ensureUser() {{
      let uid = localStorage.getItem('userId');
      if (!uid) {{
        const r = await fetch('/ensure-user', {{ method:'POST' }});
        const j = await r.json();
        uid = j.user_id;
        localStorage.setItem('userId', uid);
      }}
      document.getElementById('user-id-label').innerText = uid.slice(0,8);
      refreshUser();
    }}

    async function refreshUser() {{
      const uid = localStorage.getItem('userId');
      if (!uid) return;
      const r = await fetch('/user/' + uid);
      if (!r.ok) {{ document.getElementById('sub-status').innerText = 'Unknown'; return; }}
      const j = await r.json();
      document.getElementById('sub-status').innerText = j.is_premium ? 'Premium' : 'Free';
    }}

    async function uploadSwing() {{
      const fileInput = document.getElementById('swing-file');
      const style = document.getElementById('style').value;
      const userId = localStorage.getItem('userId');
      if (!fileInput.files.length) {{ alert('Please choose a swing video!'); return; }}
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);
      formData.append('user_id', userId);
      formData.append('style', style);
      document.getElementById('status').innerText = 'Uploading...';
      const res = await fetch('/upload-swing/', {{ method:'POST', body: formData }});
      const data = await res.json();
      if (!res.ok) {{ document.getElementById('status').innerText = 'Error: ' + (data.detail || 'Upload failed'); return; }}
      document.getElementById('status').innerText = 'Analysis complete!';
      showPlayerCard(data);
    }}

    function showPlayerCard(data) {{
      document.getElementById('player-card').classList.remove('hidden');
      document.getElementById('overall').innerText = data.overall_score;
      document.getElementById('power').innerText = data.attributes.power;
      document.getElementById('accuracy').innerText = data.attributes.accuracy;
      document.getElementById('consistency').innerText = data.attributes.consistency;
      document.getElementById('balance').innerText = data.attributes.balance;
      document.getElementById('style-stat').innerText = data.attributes.style;
      refreshUser();
    }}

    async function upgrade() {{
      const userId = localStorage.getItem('userId');
      const r = await fetch('/create-checkout-session/?user_id=' + encodeURIComponent(userId), {{ method:'POST' }});
      const j = await r.json();
      if (!r.ok || !j.checkout_url) {{ alert('Failed to create checkout session'); return; }}
      window.location.href = j.checkout_url;
    }}

    if (qs.get('success')) {{
      // Returned from Stripe success page
      setTimeout(refreshUser, 2000);
    }}

    ensureUser();
  </script>
</body>
</html>
    """

@app.post("/ensure-user")
def ensure_user():
    """Create and return a guest user ID (stored in localStorage on the client)."""
    db = SessionLocal()
    user = User(id=str(uuid.uuid4()), name="Guest")
    db.add(user); db.commit(); db.refresh(user)
    return {"user_id": user.id}

@app.get("/user/{user_id}")
def get_user(user_id: str):
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": user.id,
        "is_premium": user.is_premium,
        "subscription_plan": user.subscription_plan,
        "handicap": user.handicap,
        "style_choice": user.style_choice
    }

@app.post("/upload-swing/")
async def upload_swing(file: UploadFile = File(...), user_id: str = Form(None), style: str = Form("classic")):
    db = SessionLocal()

    # Get or create user if missing
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if not user:
        user = User(id=str(uuid.uuid4()), name="Guest", style_choice=style)
        db.add(user); db.commit(); db.refresh(user)

    # Free plan limit: 3 swings / 30 days
    if not user.is_premium:
        cutoff = datetime.utcnow() - timedelta(days=30)
        swings_in_window = db.query(Swing).filter(Swing.user_id == user.id, Swing.created_at >= cutoff).count()
        if swings_in_window >= 3:
            raise HTTPException(status_code=403, detail="Free plan limit reached. Upgrade to Premium for unlimited uploads.")

    # Basic file guard
    if not file.filename.lower().endswith((".mp4", ".mov", ".mkv", ".avi")):
        raise HTTPException(status_code=400, detail="Please upload a video file (mp4/mov/mkv/avi).")

    os.makedirs("uploads", exist_ok=True)
    save_path = os.path.join("uploads", f"{uuid.uuid4()}_{file.filename}")
    with open(save_path, "wb") as f:
        f.write(await file.read())

    # Record swing
    swing = Swing(id=str(uuid.uuid4()), user_id=user.id, s3_video_path=save_path, processed=False, style_label=style)
    db.add(swing); db.commit(); db.refresh(swing)

    # “Analyze” swing (placeholder)
    attrs, overall = process_swing(save_path, style_choice=style)

    return JSONResponse({
        "swing_id": swing.id,
        "attributes": attrs,
        "overall_score": overall,
        "is_premium": user.is_premium
    })

# ---------------------------
#  Stripe: create checkout + webhook
# ---------------------------
@app.post("/create-checkout-session/")
def create_checkout_session(user_id: str):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            client_reference_id=user_id,
            success_url="http://localhost:8000/?success=true",
            cancel_url="http://localhost:8000/?canceled=true",
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stripe-webhook/")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return {"error": str(e)}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        db = SessionLocal()
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_premium = True
            user.subscription_plan = "monthly"
            db.commit()
    return {"status": "success"}

@app.get("/health")
def health():
    return {"status": "ok"}
