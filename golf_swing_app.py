import os, uuid, random, json
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import stripe

# ---------------------------
# Database setup
# ---------------------------
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

# ---------------------------
# Scoring logic
# ---------------------------
def compute_base_attributes():
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

# ---------------------------
# App setup
# ---------------------------
load_dotenv()
app = FastAPI(title="Golf Swing Analyzer (Single File)")

os.makedirs("uploads", exist_ok=True)
os.makedirs("jobs", exist_ok=True)

# Stripe config
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_replace_me")

# ---------------------------
# Endpoints
# ---------------------------
@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Golf Swing Analyzer</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 2rem; background: #f4f4f9; }
        h1 { color: #2a5d84; }
        #upload-box { margin-bottom: 1.5rem; display: flex; gap: .5rem; flex-wrap: wrap; }
        button { padding: 0.6rem 1rem; background: #2a5d84; color: white; border: none; cursor: pointer; border-radius: 6px; }
        button:hover { background: #1c415a; }
        .card { background: white; padding: 1rem; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.12); width: 320px; }
        .hidden { display: none; }
        ul { list-style: none; padding-left: 0; }
        li { padding: .2rem 0; }
        #status { margin: .75rem 0; color: #444; }
      </style>
    </head>
    <body>
      <h1>🏌️ Golf Swing Analyzer</h1>
      <div id="upload-box">
        <input type="file" id="swing-file" accept="video/*">
        <input type="text" id="user-id" placeholder="User ID (leave empty for new)">
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
        <p><strong>User:</strong> <span id="player-name"></span></p>
        <p><strong>Subscription:</strong> <span id="subscription"></span></p>
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
        async function uploadSwing() {
          const fileInput = document.getElementById('swing-file');
          const userId = document.getElementById('user-id').value;
          const style = document.getElementById('style').value;
          if (!fileInput.files.length) { alert('Please choose a swing video!'); return; }
          const formData = new FormData();
          formData.append('file', fileInput.files[0]);
          if (userId) formData.append('user_id', userId);
          formData.append('style', style);
          document.getElementById('status').innerText = 'Uploading...';
          const res = await fetch('/upload-swing/', { method: 'POST', body: formData });
          const data = await res.json();
          if (!res.ok) { document.getElementById('status').innerText = 'Error: ' + (data.detail || 'Upload failed'); return; }
          document.getElementById('status').innerText = 'Analysis complete!';
          showPlayerCard(data);
        }
        function showPlayerCard(data) {
          document.getElementById('player-card').classList.remove('hidden');
          document.getElementById('player-name').innerText = 'User ' + (data.swing_id || '').slice(0,6);
          document.getElementById('subscription').innerText = data.is_premium ? 'Premium' : 'Free';
          document.getElementById('overall').innerText = data.overall_score;
          document.getElementById('power').innerText = data.attributes.power;
          document.getElementById('accuracy').innerText = data.attributes.accuracy;
          document.getElementById('consistency').innerText = data.attributes.consistency;
          document.getElementById('balance').innerText = data.attributes.balance;
          document.getElementById('style-stat').innerText = data.attributes.style;
        }
      </script>
    </body>
    </html>
    """

@app.post("/upload-swing/")
async def upload_swing(file: UploadFile = File(...), user_id: str = Form(None), style: str = Form("classic")):
    db = SessionLocal()

    # Get or create user
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if not user:
        user = User(id=str(uuid.uuid4()), name="Guest", style_choice=style)
        db.add(user); db.commit(); db.refresh(user)

    # Free plan: 3 swings / 30 days
    if not user.is_premium:
        cutoff = datetime.utcnow() - timedelta(days=30)
        swing_count = db.query(Swing).filter(Swing.user_id == user.id, Swing.created_at >= cutoff).count()
        if swing_count >= 3:
            raise HTTPException(status_code=403, detail="Free plan limit reached. Upgrade to Premium for unlimited uploads.")

    # Save file
    if not file.filename.lower().endswith((".mp4", ".mov", ".mkv", ".avi")):
        raise HTTPException(status_code=400, detail="Please upload a video file (mp4/mov/mkv/avi).")
    path = os.path.join("uploads", f"{uuid.uuid4()}_{file.filename}")
    with open(path, "wb") as f:
        f.write(await file.read())

    # Record + score
    swing = Swing(id=str(uuid.uuid4()), user_id=user.id, s3_video_path=path, processed=False, style_label=style)
    db.add(swing); db.commit(); db.refresh(swing)

    attrs, overall = process_swing(path, style_choice=style)
    result = {
        "swing_id": swing.id,
        "attributes": attrs,
        "overall_score": overall,
        "is_premium": user.is_premium
    }
    return JSONResponse(result)

@app.get("/health")
def health():
    return {"status": "ok"}
