from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict, Tuple
import uuid
from datetime import datetime
import speech_recognition as sr
import tempfile
import os
import pyttsx3
import base64
import binascii
import hmac
import re
from urllib.parse import quote
import httpx
from pathlib import Path
import json
import sqlite3

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# Optional OpenAI client (may not be installed/configured)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# Optional Gemini client (google-genai)
try:
    from google import genai
except Exception:
    genai = None

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize OpenAI/Gemini/DeepSeek clients (with error handling)
client = None
gemini_client = None
deepseek_client = None
openai_disabled_reason: Optional[str] = None
if OpenAI is not None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            client = OpenAI(api_key=api_key)
        except Exception:
            client = None

# Gemini client
gemini_api_key = os.getenv("GEMINI_API_KEY")
if genai is not None and gemini_api_key:
    try:
        gemini_client = genai.Client(api_key=gemini_api_key)
    except Exception:
        gemini_client = None

# DeepSeek client (OpenAI-compatible)
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
if OpenAI is not None and deepseek_api_key:
    try:
        deepseek_client = OpenAI(api_key=deepseek_api_key, base_url="https://api.deepseek.com")
    except Exception:
        deepseek_client = None

app = FastAPI(
    title="AI Teacher Assistant",
    version="4.0.0",
    description="Enhanced AI teacher with voice interaction and school registration"
)

INVIDIOUS_INSTANCES = [
    "https://invidious.flokinet.to",
    "https://invidious.privacyredirect.com",
    "https://invidious.jing.rocks",
]

EDUCATIONAL_VIDEO_KEYWORDS = [
    "lesson", "tutorial", "explained", "lecture", "course", "cbse", "icse",
    "ncert", "jee", "neet", "physics wallah", "khan academy", "crash course",
    "education", "class", "study", "learn"
]

# CORS configuration
raw_cors_origins = os.getenv("CORS_ORIGINS", "*").strip()
if raw_cors_origins == "*":
    cors_origins = ["*"]
    cors_allow_credentials = False
else:
    cors_origins = [origin.strip() for origin in raw_cors_origins.split(",") if origin.strip()]
    cors_allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _has_valid_basic_auth(auth_header: str, expected_user: str, expected_password: str) -> bool:
    if not auth_header.startswith("Basic "):
        return False
    encoded = auth_header.split(" ", 1)[1].strip()
    if not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    username, password = decoded.split(":", 1)
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)


@app.middleware("http")
async def access_control_middleware(request, call_next):
    # Optional app lock: set APP_USERNAME and APP_PASSWORD in .env
    expected_user = (os.getenv("APP_USERNAME") or "").strip()
    expected_password = (os.getenv("APP_PASSWORD") or "").strip()
    if not expected_user or not expected_password:
        return await call_next(request)

    if request.url.path == "/health":
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if _has_valid_basic_auth(auth_header, expected_user, expected_password):
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
        headers={"WWW-Authenticate": "Basic realm=\"AI Teacher Assistant\""},
    )

# ========== ENHANCED MODELS ==========
class StudentCreate(BaseModel):
    name: str
    school_name: str
    class_grade: str
    mobile: str
    email: Optional[str] = None
    language: str = "english"
    subjects: List[str] = Field(default_factory=list)

class ChatRequest(BaseModel):
    message: str
    language: str = "english"
    student_id: Optional[str] = None
    subject: Optional[str] = None
    use_docs: bool = True
    doc_ids: Optional[List[str]] = None
    step_by_step: bool = True
    model_provider: Optional[str] = None
    model_name: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    language: str
    explanation: str
    suggestions: List[str]
    student_id: Optional[str] = None

class VoiceResponse(BaseModel):
    text: str
    audio_base64: Optional[str] = None
    language: str

class QuizQuestion(BaseModel):
    question: str
    options: List[str]
    correct_answer: int
    subject: str
    difficulty: str
    hint: str
    explanation: str

class Assignment(BaseModel):
    assignment_id: str
    title: str
    subject: str
    class_grade: str
    description: str
    pdf_url: str
    due_date: str
    total_marks: int

class PracticePaper(BaseModel):
    paper_id: str
    title: str
    subject: str
    year: str
    pdf_url: str
    solutions_url: Optional[str]
    time_limit: int

class TTSRequest(BaseModel):
    text: str
    language: str = "en"

class ProgressUpdate(BaseModel):
    """Model for progress update data"""
    topic: str
    score: Optional[float] = None
    completed: bool = False
    notes: Optional[str] = None
    timestamp: str = ""


class SubjectSuggestion(BaseModel):
    subject: str
    category: str
    reason: str
    starter_prompt: str
    priority: int


class FeedbackRequest(BaseModel):
    student_id: Optional[str] = None
    message: str
    rating: int = Field(ge=1, le=5)
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)

# ========== IN-MEMORY DATABASE ==========
students_db = {}
assignments_db = {}
practice_papers_db = {}
quiz_questions_db = {}
documents_db = {}

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path("data/ai_teacher.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                school_name TEXT NOT NULL,
                class_grade TEXT NOT NULL,
                mobile TEXT NOT NULL,
                email TEXT,
                language TEXT NOT NULL,
                subjects_json TEXT NOT NULL,
                level TEXT NOT NULL,
                created_at TEXT NOT NULL,
                login_code TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                score REAL,
                completed INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                message TEXT NOT NULL,
                rating INTEGER NOT NULL,
                category TEXT,
                tags_json TEXT,
                context_json TEXT,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _db_execute(query: str, params: Tuple[Any, ...]) -> None:
    conn = _db_connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _db_query(query: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    conn = _db_connect()
    try:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _record_progress_event(
    student_id: str,
    topic: str,
    event_type: str,
    score: Optional[float] = None,
    completed: bool = False,
    notes: Optional[str] = None,
) -> None:
    _db_execute(
        """
        INSERT INTO progress_events (student_id, topic, score, completed, notes, event_type, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student_id,
            topic,
            score,
            1 if completed else 0,
            notes,
            event_type,
            datetime.now().isoformat(),
        ),
    )


def _progress_summary(student_id: str) -> Dict[str, Any]:
    rows = _db_query(
        """
        SELECT topic, AVG(score) as avg_score, MAX(timestamp) as last_activity,
               SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) as completed_count,
               COUNT(*) as total_events
        FROM progress_events
        WHERE student_id = ?
        GROUP BY topic
        ORDER BY last_activity DESC
        """,
        (student_id,),
    )
    last_activity_row = _db_query(
        "SELECT MAX(timestamp) as last_activity FROM progress_events WHERE student_id = ?",
        (student_id,),
    )
    last_activity = last_activity_row[0]["last_activity"] if last_activity_row else None
    return {
        "student_id": student_id,
        "last_activity": last_activity,
        "topics": rows,
    }


def _record_feedback(payload: FeedbackRequest) -> None:
    _db_execute(
        """
        INSERT INTO feedback_events (student_id, message, rating, category, tags_json, context_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.student_id,
            payload.message,
            payload.rating,
            payload.category,
            json.dumps(payload.tags),
            json.dumps(payload.context),
            datetime.now().isoformat(),
        ),
    )


def _feedback_summary(limit: int = 20) -> Dict[str, Any]:
    ratings = _db_query(
        "SELECT rating, COUNT(*) as count FROM feedback_events GROUP BY rating ORDER BY rating",
        tuple(),
    )
    recent = _db_query(
        """
        SELECT student_id, message, rating, category, tags_json, context_json, timestamp
        FROM feedback_events
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )
    return {
        "ratings": ratings,
        "recent": recent,
    }


def _load_student(student_id: str) -> Optional[Dict[str, Any]]:
    rows = _db_query(
        """
        SELECT student_id, name, school_name, class_grade, mobile, email, language,
               subjects_json, level, created_at, login_code
        FROM students WHERE student_id = ?
        """,
        (student_id,),
    )
    if not rows:
        return None
    row = rows[0]
    subjects = []
    try:
        subjects = json.loads(row.get("subjects_json") or "[]")
    except Exception:
        subjects = []
    student = {
        "student_id": row["student_id"],
        "name": row["name"],
        "school_name": row["school_name"],
        "class_grade": row["class_grade"],
        "mobile": row["mobile"],
        "email": row["email"],
        "language": row["language"],
        "subjects": subjects,
        "level": row["level"],
        "created_at": row["created_at"],
        "login_code": row["login_code"],
        "progress": {},
        "assignments_completed": [],
        "quiz_scores": [],
    }
    students_db[student_id] = student
    return student


_init_db()

FRONTEND_DIR = Path("frontend").resolve()
if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

# ========== HOME ENDPOINT ==========
@app.get("/")
async def home():
    if client and not openai_disabled_reason:
        openai_status = "configured"
    elif openai_disabled_reason:
        openai_status = f"disabled ({openai_disabled_reason})"
    else:
        openai_status = "not configured (using fallback)"

    return {
        "app": "AI Teacher Assistant",
        "version": "4.0.0",
        "status": "running",
        "web_app": "/app",
        "openai_status": openai_status,
        "features": [
            "Voice interaction system",
            "Speech-to-text & Text-to-speech",
            "School/College registration system",
            "Multi-language support (7 Indian languages)",
            "Assignment and practice papers",
            "Quiz system with hints",
            "Progress tracking"
        ],
        "endpoints": {
            "home": "GET /",
            "web_app": "GET /app",
            "health": "GET /health",
            "register": "POST /register-enhanced",
            "chat": "POST /chat",
            "voice_to_text": "POST /voice-to-text",
            "text_to_speech": "POST /text-to-speech",
            "voice_chat": "POST /voice-chat",
            "video_recommendations": "GET /video-recommendations?query=photosynthesis",
            "assignments": "GET /assignments/{class_grade}",
            "practice-papers": "GET /practice-papers",
            "quiz": "GET /quiz/{subject}",
            "hint": "GET /hint/{question_id}",
            "student": "GET /students/{student_id}",
            "update_progress": "POST /students/{student_id}/progress",
            "progress_events": "GET /students/{student_id}/progress/events",
            "progress_summary": "GET /students/{student_id}/progress/summary",
            "quiz_submit": "POST /quiz/submit",
            "upload": "POST /upload",
            "documents": "GET /documents",
            "feedback": "POST /feedback",
            "feedback_summary": "GET /feedback/summary",
            "diagram_spec": "GET /diagram-spec?topic=photosynthesis"
        }
    }


@app.get("/app")
async def web_app():
    if not FRONTEND_DIR.exists():
        raise HTTPException(status_code=404, detail="frontend directory not found")
    return FileResponse(FRONTEND_DIR / "index.html")

# ========== HEALTH CHECK ==========
@app.get("/health")
async def health_check():
    if client and not openai_disabled_reason:
        openai_status = "configured"
    elif openai_disabled_reason:
        openai_status = f"disabled ({openai_disabled_reason})"
    else:
        openai_status = "not configured"

    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "openai_status": openai_status
    }

# ========== VOICE INTERACTION SYSTEM ==========
@app.post("/voice-to-text")
async def voice_to_text(audio_file: UploadFile = File(...), language: str = "en-IN"):
    """
    Convert speech to text
    Supported languages:
    - en-IN: English (India)
    - hi-IN: Hindi (India)
    - ta-IN: Tamil (India)
    - te-IN: Telugu (India)
    - mr-IN: Marathi (India)
    - gu-IN: Gujarati (India)
    - bn-IN: Bengali (India)
    """
    
    temp_path = ""
    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
        content = await audio_file.read()
        temp_audio.write(content)
        temp_path = temp_audio.name
    
    try:
        # Initialize recognizer
        recognizer = sr.Recognizer()
        
        # Load audio file
        with sr.AudioFile(temp_path) as source:
            # Adjust for ambient noise
            recognizer.adjust_for_ambient_noise(source, duration=0.5)  # type: ignore[arg-type]
            audio_data = recognizer.record(source)
        
        # Convert speech to text - ignore type checking for this line
        text = recognizer.recognize_google(audio_data, language=language)  # type: ignore
        
        return {
            "success": True,
            "text": text,
            "language": language,
            "message": "Speech converted successfully"
        }
        
    except sr.UnknownValueError:
        return {
            "success": False,
            "text": "",
            "error": "Could not understand audio. Please speak clearly."
        }
    except sr.RequestError as e:
        return {
            "success": False,
            "text": "",
            "error": f"Speech recognition service error: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "text": "",
            "error": f"Error processing audio: {str(e)}"
        }
    finally:
        # Clean up temp file
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

@app.post("/text-to-speech")
async def text_to_speech(request: TTSRequest):
    """
    Convert text to speech
    Returns base64 encoded audio
    """
    text = request.text
    language = request.language
    
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    temp_path = ""
    try:
        # Initialize text-to-speech engine
        engine = pyttsx3.init()
        
        # Get available voices - ignore type checking for pyttsx3
        voices = engine.getProperty('voices')  # type: ignore
        
        # Safely check if voices is a list and has items
        if voices and hasattr(voices, '__len__') and len(voices) > 0:  # type: ignore
            if language.startswith("hi") and len(voices) > 1:  # type: ignore
                engine.setProperty('voice', voices[1].id)  # type: ignore
            else:
                engine.setProperty('voice', voices[0].id)  # type: ignore
        
        # Set properties
        engine.setProperty('rate', 150)  # Speed of speech
        engine.setProperty('volume', 0.9)  # Volume 0-1
        
        # pyttsx3 generally outputs WAV/AIFF depending on OS driver.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_path = temp_file.name
        
        # Save speech to file
        engine.save_to_file(text, temp_path)  # type: ignore
        engine.runAndWait()  # type: ignore
        
        # Read file and convert to base64
        with open(temp_path, 'rb') as audio_file:
            audio_bytes = audio_file.read()
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        return {
            "success": True,
            "text": text,
            "language": language,
            "audio_base64": audio_base64,
            "audio_format": "wav",
            "message": "Text converted to speech successfully"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Text-to-speech error: {str(e)}"
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

@app.post("/voice-chat")
async def voice_chat(audio_file: UploadFile = File(...), language: str = "en-IN"):
    """
    Complete voice chat: Speech → Text → AI Response → Speech
    """
    # Step 1: Convert speech to text
    voice_result = await voice_to_text(audio_file, language)
    
    if not voice_result["success"]:
        return voice_result
    
    text = voice_result["text"]
    
    # Step 2: Get AI response
    lang_map = {
        "en-IN": "english",
        "hi-IN": "hindi",
        "ta-IN": "tamil",
        "te-IN": "telugu",
        "mr-IN": "marathi",
        "gu-IN": "gujarati",
        "bn-IN": "bengali"
    }
    
    chat_language = lang_map.get(language, "english")
    
    # Get AI response
    ai_response = await get_ai_response(text, chat_language)
    
    # Step 3: Convert AI response to speech
    tts_request = TTSRequest(text=ai_response, language=language)
    tts_result = await text_to_speech(tts_request)

    if not tts_result.get("success"):
        return {
            "success": False,
            "user_speech": text,
            "ai_response": ai_response,
            "language": language,
            "audio_base64": None,
            "error": tts_result.get("error", "Text-to-speech failed"),
            "message": "Voice chat partially completed (speech + AI response worked, TTS failed)"
        }
    
    return {
        "success": True,
        "user_speech": text,
        "ai_response": ai_response,
        "language": language,
        "audio_base64": tts_result.get("audio_base64") if tts_result["success"] else None,
        "message": "Voice chat completed successfully"
    }

async def get_ai_response(message: str, language: str = "english"):
    """
    Helper function to generate AI response
    """
    # Multi-language responses
    responses = {
        "english": {
            "greeting": "Hello! I'm your AI teacher. How can I help you learn today?",
            "math_help": "I can help you with mathematics. Let's solve problems step by step.",
            "science_help": "Science is fascinating! What topic would you like to explore?",
            "ml_help": "Machine Learning (ML) is a part of AI where computers learn patterns from data to make predictions or decisions without being explicitly programmed for every rule.",
            "default": "That's interesting! Let me help you understand that better."
        },
        "hindi": {
            "greeting": "नमस्ते! मैं आपका AI शिक्षक हूं। आज आप क्या सीखना चाहेंगे?",
            "math_help": "मैं आपकी गणित में मदद कर सकता हूं। चलिए कदम दर कदम समस्याएं हल करते हैं।",
            "science_help": "विज्ञान बहुत रोचक है! आप कौन सा विषय जानना चाहेंगे?",
            "ml_help": "मशीन लर्निंग (ML), AI का एक भाग है जिसमें कंप्यूटर डेटा से पैटर्न सीखकर निर्णय या भविष्यवाणी करना सीखते हैं।",
            "default": "यह दिलचस्प है! मुझे आपको इसे बेहतर समझने में मदद करने दें।"
        },
        "tamil": {
            "greeting": "வணக்கம்! நான் உங்கள் AI ஆசிரியர். இன்று நீங்கள் என்ன கற்க விரும்புகிறீர்கள்?",
            "math_help": "நான் கணிதத்தில் உங்களுக்கு உதவ முடியும். படிப்படியாக சிக்கல்களை தீர்ப்போம்.",
            "science_help": "அறிவியல் மிகவும் சுவாரஸ்யமானது! நீங்கள் எந்த தலைப்பை ஆராய விரும்புகிறீர்கள்?",
            "ml_help": "மெஷின் லெர்னிங் (ML) என்பது AI-யின் ஒரு பகுதி. இதில் கணினி தரவிலிருந்து வடிவங்களை கற்று முடிவுகள் அல்லது கணிப்புகள் செய்கிறது.",
            "default": "அது சுவாரஸ்யமானது! அதை நன்றாக புரிந்துகொள்ள நான் உங்களுக்கு உதவுகிறேன்."
        },
        "telugu": {
            "greeting": "నమస్కారం! నేను మీ AI టీచర్ని. ఈరోజు మీరు ఏమి నేర్చుకోవాలనుకుంటున్నారు?",
            "math_help": "నేను మీకు గణితంలో సహాయం చేయగలను. దశలవారీగా సమస్యలను పరిష్కరిద్దాం.",
            "science_help": "సైన్స్ చాలా ఆసక్తికరంగా ఉంటుంది! మీరు ఏ అంశాన్ని అన్వేషించాలనుకుంటున్నారు?",
            "ml_help": "మెషిన్ లెర్నింగ్ (ML) అనేది AIలోని భాగం. డేటా నుండి నమూనాలను నేర్చుకుని కంప్యూటర్ నిర్ణయాలు లేదా అంచనాలు వేస్తుంది.",
            "default": "ఇది ఆసక్తికరంగా ఉంది! దాన్ని బాగా అర్థం చేసుకోవడానికి నేను మీకు సహాయం చేస్తాను."
        },
        "marathi": {
            "greeting": "नमस्कार! मी तुमचा AI शिक्षक आहे. आज तुम्हाला काय शिकायचे आहे?",
            "math_help": "मी गणितात मदत करू शकतो. चला, प्रश्न पायरीपायरीने सोडवूया.",
            "science_help": "विज्ञान खूप रोचक आहे! तुम्हाला कोणता विषय शिकायचा आहे?",
            "ml_help": "मशीन लर्निंग (ML) हे AI चे एक शाखा आहे. यात संगणक डेटा मधील नमुने शिकून निर्णय किंवा अंदाज करतो.",
            "default": "छान प्रश्न आहे! मी तुम्हाला हे अधिक स्पष्टपणे समजावतो."
        },
        "gujarati": {
            "greeting": "નમસ્તે! હું તમારો AI શિક્ષક છું. આજે તમે શું શીખવા માંગો છો?",
            "math_help": "હું તમને ગણિતમાં મદદ કરી શકું છું. ચાલો પગલુંદર પગલું પ્રશ્નો ઉકેલીએ.",
            "science_help": "વિજ્ઞાન ખૂબ રસપ્રદ છે! તમે કયો વિષય સમજવા માંગો છો?",
            "ml_help": "મશીન લર્નિંગ (ML) એ AI નો ભાગ છે જેમાં કમ્પ્યુટર ડેટામાંથી પેટર્ન શીખીને આગાહી અથવા નિર્ણય લે છે.",
            "default": "સારો પ્રશ્ન છે! હું તમને આ વધુ સારી રીતે સમજાવીશ."
        },
        "bengali": {
            "greeting": "নমস্কার! আমি তোমার AI শিক্ষক। আজ তুমি কী শিখতে চাও?",
            "math_help": "আমি গণিতে সাহায্য করতে পারি। ধাপে ধাপে সমস্যা সমাধান করি।",
            "science_help": "বিজ্ঞান খুবই আকর্ষণীয়! তুমি কোন বিষয় শিখতে চাও?",
            "ml_help": "মেশিন লার্নিং (ML) হল AI-এর একটি অংশ যেখানে কম্পিউটার ডেটা থেকে প্যাটার্ন শিখে সিদ্ধান্ত বা পূর্বাভাস দেয়।",
            "default": "চমৎকার প্রশ্ন! আমি এটা আরও ভালোভাবে বুঝিয়ে দিচ্ছি।"
        }
    }
    
    message_lower = message.lower()
    lang = language if language in responses else "english"
    lang_responses = responses[lang]
    
    # Check for greetings
    greetings = ["hello", "hi", "hey", "नमस्ते", "வணக்கம்", "నమస్కారం", "नमस्कार", "નમસ્તે", "নমস্কার"]
    if any(greeting in message_lower for greeting in greetings):
        return lang_responses["greeting"]
    
    # Check for math-related queries
    math_words = ["math", "गणित", "கணிதம்", "గణితం", "गणित", "ગણિત", "গণিত", "calculate", "addition", "subtraction",
                  "multiplication", "division", "algebra", "geometry"]
    if any(word in message_lower for word in math_words):
        return lang_responses["math_help"]
    
    # Check for science-related queries
    science_words = ["science", "विज्ञान", "அறிவியல்", "సైన్స్", "विज्ञान", "વિજ્ઞાન", "বিজ্ঞান", "physics", "chemistry",
                     "biology", "experiment", "reaction"]
    if any(word in message_lower for word in science_words):
        return lang_responses["science_help"]

    # Check for AI/ML-related queries
    ml_words = ["ml", "machine learning", "artificial intelligence", "ai", "neural network", "model training"]
    if any(word in message_lower for word in ml_words):
        return lang_responses.get("ml_help", lang_responses["default"])
    
    return lang_responses["default"]


def _step_by_step_prompt(language: str) -> str:
    prompts = {
        "english": "Explain step-by-step using numbered steps. Keep each step short and clear. End with a brief summary and 2 practice questions.",
        "hindi": "क्रमबद्ध चरणों में समझाएँ, हर चरण छोटा और स्पष्ट रखें। अंत में संक्षिप्त सार और 2 अभ्यास प्रश्न दें।",
        "tamil": "அடிக்கடி எண்ணப்பட்ட படிகளாக விளக்கவும். ஒவ்வொரு படியும் சுருக்கமாகவும் தெளிவாகவும் இருக்கட்டும். இறுதியில் சுருக்கமும் 2 பயிற்சி கேள்விகளும் சேர்க்கவும்.",
        "telugu": "దశల వారీగా నంబర్లతో వివరించండి. ప్రతి దశ చిన్నగా స్పష్టంగా ఉండాలి. చివరలో సంక్షిప్త సారాంశం మరియు 2 అభ్యాస ప్రశ్నలు ఇవ్వండి.",
        "marathi": "क्रमांकित पायऱ्यांमध्ये समजावून सांगा. प्रत्येक पायरी लहान आणि स्पष्ट ठेवा. शेवटी थोडक्यात सारांश आणि 2 सराव प्रश्न द्या.",
        "gujarati": "ક્રમબદ્ધ પગલાંમાં સમજાવો. દરેક પગલું ટૂંકું અને સ્પષ્ટ રાખો. અંતે ટૂંકો સારાંશ અને 2 પ્રેક્ટિસ પ્રશ્નો આપો.",
        "bengali": "ধাপে ধাপে নম্বরযুক্তভাবে ব্যাখ্যা করুন। প্রতিটি ধাপ ছোট ও পরিষ্কার রাখুন। শেষে সংক্ষিপ্ত সারাংশ ও 2টি অনুশীলন প্রশ্ন দিন।",
    }
    return prompts.get(language, prompts["english"])


def _provider_order(requested: Optional[str]) -> List[str]:
    all_providers = ["openai", "gemini", "deepseek"]
    order: List[str] = []

    if requested:
        order.append(requested)
    else:
        env_default = (os.getenv("DEFAULT_MODEL_PROVIDER") or "").strip().lower()
        if env_default:
            order.append(env_default)

    # Keep preferred provider first, then fall back to others.
    order.extend(all_providers)

    deduped: List[str] = []
    for provider in order:
        if provider in all_providers and provider not in deduped:
            deduped.append(provider)
    return deduped


def _provider_available(provider: str) -> bool:
    if provider == "openai":
        return client is not None and not openai_disabled_reason
    if provider == "gemini":
        return gemini_client is not None
    if provider == "deepseek":
        return deepseek_client is not None
    return False


def _default_model(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    return ""


async def get_free_api_response(message: str) -> Optional[str]:
    """
    Try free public APIs for short factual answers.
    Priority:
    1) DuckDuckGo Instant Answer API (no key)
    2) Wikipedia REST summary API (no key)
    """
    query = (message or "").strip()
    if not query:
        return None

    timeout = httpx.Timeout(8.0)
    headers = {"User-Agent": "ai-teacher-assistant/1.0"}

    # 1) DuckDuckGo Instant Answer API
    try:
        ddg_url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
            ddg_resp = await http.get(ddg_url)
            if ddg_resp.status_code == 200:
                ddg_data = ddg_resp.json()
                abstract = (ddg_data.get("AbstractText") or "").strip()
                if abstract:
                    return abstract

                # Some results appear in RelatedTopics as short definitions
                related = ddg_data.get("RelatedTopics") or []
                if isinstance(related, list):
                    for item in related:
                        if isinstance(item, dict):
                            text = (item.get("Text") or "").strip()
                            if text:
                                return text
    except Exception:
        pass

    # 2) Wikipedia Summary API
    try:
        # Extract likely topic from "what is X", "define X", etc.
        normalized = query.lower()
        topic = query
        patterns = [
            r"^\s*what is\s+(.+?)\s*\??\s*$",
            r"^\s*who is\s+(.+?)\s*\??\s*$",
            r"^\s*define\s+(.+?)\s*$",
            r"^\s*explain\s+(.+?)\s*$",
        ]
        for pattern in patterns:
            match = re.match(pattern, normalized)
            if match:
                # Slice from original query to preserve capitalization
                raw_group = match.group(1)
                start_idx = normalized.find(raw_group)
                if start_idx >= 0:
                    topic = query[start_idx:start_idx + len(raw_group)]
                break

        topic = topic.strip(" ?.")
        if topic:
            wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(topic)}"
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
                wiki_resp = await http.get(wiki_url)
                if wiki_resp.status_code == 200:
                    wiki_data = wiki_resp.json()
                    extract = (wiki_data.get("extract") or "").strip()
                    if extract:
                        return extract
    except Exception:
        pass

    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9]+", (text or "").lower())


def _chunk_text(text: str, max_chars: int = 800) -> List[str]:
    cleaned = _normalize_text(text)
    if not cleaned:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + max_chars, len(cleaned))
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _score_chunk(query_tokens: List[str], chunk: str) -> int:
    if not query_tokens or not chunk:
        return 0
    chunk_tokens = set(_tokenize(chunk))
    return sum(1 for token in query_tokens if token in chunk_tokens)


def _retrieve_context(query: str, doc_ids: Optional[List[str]] = None, top_k: int = 4) -> Tuple[str, List[str]]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return "", []

    candidates = []
    source_ids = set(doc_ids or [])
    for doc_id, doc in documents_db.items():
        if source_ids and doc_id not in source_ids:
            continue
        for chunk in doc.get("chunks", []):
            score = _score_chunk(query_tokens, chunk)
            if score > 0:
                candidates.append((score, doc_id, chunk))

    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:max(1, min(top_k, 8))]
    context_chunks = [item[2] for item in top]
    used_doc_ids = list({item[1] for item in top})
    return "\n\n".join(context_chunks), used_doc_ids


def _extract_text_from_pdf(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("PDF support not available. Install pypdf.")
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            parts.append(page_text)
    return _normalize_text("\n".join(parts))


def _save_upload_file(upload_file: UploadFile) -> Path:
    suffix = Path(upload_file.filename or "").suffix or ".bin"
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    path = UPLOAD_DIR / safe_name
    with open(path, "wb") as out_file:
        out_file.write(upload_file.file.read())
    return path


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (ValueError, TypeError):
        return default


def _duration_label(seconds: int) -> str:
    if seconds <= 0:
        return "N/A"
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _rank_video(title: str, channel: str, seconds: int, views: int) -> float:
    score = 0.0
    combined = f"{title} {channel}".lower()

    for keyword in EDUCATIONAL_VIDEO_KEYWORDS:
        if keyword in combined:
            score += 2.0

    if "shorts" in combined or (0 < seconds < 120):
        score -= 3.0

    if 300 <= seconds <= 2700:
        score += 1.0

    if views > 0:
        score += min(views / 1_000_000, 2.0)

    return score


async def _search_invidious(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    timeout = httpx.Timeout(8.0)
    headers = {"User-Agent": "ai-teacher-assistant/1.0"}

    for base_url in INVIDIOUS_INSTANCES:
        try:
            endpoint = f"{base_url}/api/v1/search"
            params = {"q": query, "type": "video", "sort_by": "relevance", "page": 1}

            async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
                resp = await http.get(endpoint, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if not isinstance(data, list):
                    continue

            ranked: List[Dict[str, Any]] = []
            seen_ids = set()

            for item in data:
                if not isinstance(item, dict):
                    continue
                if item.get("type") not in (None, "video"):
                    continue

                video_id = item.get("videoId")
                if not video_id or video_id in seen_ids:
                    continue

                title = (item.get("title") or "").strip()
                channel = (item.get("author") or "").strip() or "Unknown Channel"
                seconds = _safe_int(item.get("lengthSeconds"))
                views = _safe_int(item.get("viewCount"))

                if not title:
                    continue

                seen_ids.add(video_id)
                ranked.append(
                    {
                        "video_id": video_id,
                        "title": title,
                        "channel": channel,
                        "duration_seconds": seconds,
                        "duration": _duration_label(seconds),
                        "views": views,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                        "score": _rank_video(title, channel, seconds, views),
                        "source": "invidious",
                    }
                )

            ranked.sort(key=lambda x: (x["score"], x["views"]), reverse=True)
            if ranked:
                return ranked[:max(1, min(limit, 12))]
        except Exception:
            continue

    return []


@app.get("/video-recommendations")
async def get_video_recommendations(query: str, limit: int = 6):
    """
    Return ranked YouTube recommendations using free public APIs (no API key required).
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        raise HTTPException(status_code=400, detail="Query is required")

    limit = max(1, min(limit, 12))
    videos = await _search_invidious(cleaned_query, limit=limit)

    if not videos:
        fallback_url = f"https://www.youtube.com/results?search_query={quote(cleaned_query + ' tutorial')}"
        return {
            "query": cleaned_query,
            "count": 0,
            "source": "fallback",
            "message": "Live video API unavailable. Open YouTube search results directly.",
            "videos": [],
            "youtube_search_url": fallback_url,
        }

    return {
        "query": cleaned_query,
        "count": len(videos),
        "source": "invidious",
        "videos": videos,
    }

# ========== ENHANCED REGISTRATION ==========
@app.post("/register-enhanced")
async def register_student_enhanced(student: StudentCreate):
    """Enhanced registration with school details"""
    
    # Validate mobile number (basic validation)
    mobile_clean = student.mobile.strip()
    if not mobile_clean.isdigit() or len(mobile_clean) < 10:
        raise HTTPException(status_code=400, detail="Valid mobile number is required")
    
    # Generate student ID
    school_code = student.school_name[:3].upper() if len(student.school_name) >= 3 else student.school_name.upper()
    class_code = student.class_grade[:2] if len(student.class_grade) >= 2 else student.class_grade
    student_id = f"{school_code}{class_code}{str(uuid.uuid4())[:4]}"
    
    # Generate login code
    login_code = str(uuid.uuid4())[:6].upper()
    
    # Store student data
    students_db[student_id] = {
        "student_id": student_id,
        "name": student.name,
        "school_name": student.school_name,
        "class_grade": student.class_grade,
        "mobile": mobile_clean,
        "email": student.email,
        "language": student.language,
        "subjects": student.subjects,
        "level": "beginner",
        "created_at": datetime.now().isoformat(),
        "login_code": login_code,
        "progress": {},
        "assignments_completed": [],
        "quiz_scores": []
    }

    _db_execute(
        """
        INSERT OR REPLACE INTO students
        (student_id, name, school_name, class_grade, mobile, email, language, subjects_json, level, created_at, login_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student_id,
            student.name,
            student.school_name,
            student.class_grade,
            mobile_clean,
            student.email,
            student.language,
            json.dumps(student.subjects),
            "beginner",
            datetime.now().isoformat(),
            login_code,
        ),
    )
    
    return {
        "message": f"Welcome {student.name}! Registration successful",
        "student_id": student_id,
        "student_name": student.name,
        "school": student.school_name,
        "class": student.class_grade,
        "mobile": mobile_clean,
        "email": student.email,
        "subjects": student.subjects,
        "language": student.language,
        "login_code": login_code,
        "registration_date": datetime.now().isoformat()
    }

# ========== CHAT ENDPOINT ==========
@app.post("/chat", response_model=ChatResponse)
async def chat_with_teacher(request: ChatRequest):
    """
    Chat with AI teacher
    Uses OpenAI GPT if configured, otherwise falls back to rule-based responses
    """
    global openai_disabled_reason

    context_text = ""
    used_doc_ids: List[str] = []
    if request.use_docs:
        context_text, used_doc_ids = _retrieve_context(request.message, request.doc_ids)

    if request.student_id:
        if request.student_id not in students_db:
            _load_student(request.student_id)
        if request.student_id in students_db:
            topic = request.subject or (request.message[:60] + "..." if len(request.message) > 60 else request.message)
            _record_progress_event(
                request.student_id,
                topic=topic or "general",
                event_type="chat",
                notes="auto-tracked from chat",
            )
    
    # Choose provider
    provider = "offline"
    provider_candidates = _provider_order((request.model_provider or "").strip().lower())
    for candidate in provider_candidates:
        if _provider_available(candidate):
            provider = candidate
            break

    # If no provider is configured, use fallback responses
    if provider == "offline":
        free_api_reply = await get_free_api_response(request.message)
        ai_reply = free_api_reply or await get_ai_response(request.message, request.language)
        explanation = "Using offline response mode. Configure an API key for AI-powered responses."
        if free_api_reply:
            explanation = "Using free public API response mode (DuckDuckGo/Wikipedia)."
        if openai_disabled_reason:
            explanation = f"Using offline mode: {openai_disabled_reason}"
        if request.step_by_step:
            ai_reply = f"Step-by-step explanation:\n1. {ai_reply}"
        if context_text:
            ai_reply = f"{ai_reply}\n\nFrom your notes:\n{context_text[:1200]}"
        return ChatResponse(
            reply=ai_reply,
            language=request.language,
            explanation=explanation,
            suggestions=[
                "Set up OPENAI_API_KEY, GEMINI_API_KEY, or DEEPSEEK_API_KEY in .env",
                "Try voice features",
                "Ask about math or science",
                "Request practice papers"
            ],
            student_id=request.student_id
        )

    try:
        # System prompt based on language and subject
        system_prompts = {
            "english": "You are a helpful AI teacher for Indian students. Explain concepts clearly with examples. Be encouraging and patient.",
            "hindi": "आप भारतीय छात्रों के लिए एक सहायक AI शिक्षक हैं। उदाहरणों के साथ अवधारणाओं को स्पष्ट रूप से समझाएं। प्रोत्साहित करें और धैर्य रखें।",
            "tamil": "நீங்கள் இந்திய மாணவர்களுக்கான உதவிகரமான AI ஆசிரியர். எடுத்துக்காட்டுகளுடன் கருத்துகளை தெளிவாக விளக்குங்கள். ஊக்குவிக்கும் மற்றும் பொறுமையாக இருங்கள்."
        }

        system_prompt = system_prompts.get(request.language, system_prompts["english"])
        if request.step_by_step:
            system_prompt += " " + _step_by_step_prompt(request.language)
        if context_text:
            system_prompt += "\n\nUse the following student notes as trusted context:\n" + context_text[:2000]
        if request.subject:
            system_prompt += f" Focus on {request.subject} subject."

        model_name = request.model_name or _default_model(provider)

        if provider == "openai":
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request.message}
                ],
                temperature=0.7,
                max_tokens=500
            )
            ai_reply = completion.choices[0].message.content
        elif provider == "gemini":
            prompt = f"{system_prompt}\n\nUser: {request.message}"
            result = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            ai_reply = getattr(result, "text", None) or ""
        elif provider == "deepseek":
            completion = deepseek_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request.message}
                ],
                temperature=0.7,
                max_tokens=500
            )
            ai_reply = completion.choices[0].message.content
        else:
            ai_reply = ""

        if not ai_reply:
            ai_reply = await get_ai_response(request.message, request.language)

        # Generate suggestions based on subject
        suggestions = [
            f"Tell me more about {request.subject if request.subject else 'this topic'}",
            "Give me an example",
            "Suggest a practice question",
            "Explain in simpler terms"
        ]

        return ChatResponse(
            reply=ai_reply,
            language=request.language,
            explanation=f"AI-generated response via {provider}.",
            suggestions=suggestions,
            student_id=request.student_id
        )

    except Exception as e:
        # Fallback to rule-based response if provider fails
        error_text = str(e)
        print(f"{provider} API error: {error_text}")
        free_api_reply = await get_free_api_response(request.message)
        ai_reply = free_api_reply or await get_ai_response(request.message, request.language)
        if request.step_by_step:
            ai_reply = f"Step-by-step explanation:\n{ai_reply}"
        if context_text:
            ai_reply = f"{ai_reply}\n\nFrom your notes:\n{context_text[:1200]}"

        explanation = f"{provider} request failed. Using offline mode."
        suggestions = [
            "Try again later",
            "Use voice features",
            "Ask math/science/ML basics",
            "Check API setup"
        ]

        if provider == "openai":
            if "insufficient_quota" in error_text or "429" in error_text:
                explanation = "OpenAI quota exceeded for this API project. Using offline mode."
                openai_disabled_reason = "quota exceeded"
                suggestions = [
                    "Add credits in OpenAI billing",
                    "Use free public API mode (auto-enabled)",
                    "Use an API key from a funded project",
                    "Retry after quota is available",
                    "Continue with offline mode"
                ]
            elif "invalid_api_key" in error_text or "401" in error_text:
                explanation = "OpenAI API key is invalid or revoked. Using offline mode."
                openai_disabled_reason = "invalid API key"
                suggestions = [
                    "Generate a new API key",
                    "Update OPENAI_API_KEY in .env",
                    "Use free public API mode (auto-enabled)",
                    "Restart backend after updating key",
                    "Continue with offline mode"
                ]
            elif free_api_reply:
                explanation = "OpenAI request failed. Answer generated via free public API."

        return ChatResponse(
            reply=ai_reply,
            language=request.language,
            explanation=explanation,
            suggestions=suggestions,
            student_id=request.student_id
        )

# ========== DOCUMENT INGESTION ==========
@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: Optional[str] = Form(default=None),
    title: Optional[str] = Form(default=None),
    transcript: Optional[str] = Form(default=None),
):
    """
    Upload notes/PDF/video with optional transcript.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    saved_path = _save_upload_file(file)
    extension = saved_path.suffix.lower()
    detected_type = doc_type or {
        ".pdf": "pdf",
        ".txt": "text",
        ".md": "text",
        ".mp4": "video",
        ".mov": "video",
        ".mkv": "video",
    }.get(extension, "unknown")

    extracted_text = ""
    warning = None
    try:
        if detected_type == "pdf":
            extracted_text = _extract_text_from_pdf(saved_path)
        elif detected_type == "text":
            extracted_text = saved_path.read_text(encoding="utf-8", errors="ignore")
        elif detected_type == "video":
            if transcript:
                extracted_text = transcript
            else:
                warning = "Video uploaded without transcript. Add transcript to enable learning."
        else:
            if transcript:
                extracted_text = transcript
            else:
                warning = "Unsupported file type. Provide transcript to enable learning."
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process file: {str(e)}")

    doc_id = f"DOC{uuid.uuid4().hex[:8]}"
    chunks = _chunk_text(extracted_text, max_chars=800)
    documents_db[doc_id] = {
        "doc_id": doc_id,
        "title": title or file.filename,
        "doc_type": detected_type,
        "filename": saved_path.name,
        "created_at": datetime.now().isoformat(),
        "text_length": len(extracted_text),
        "chunks": chunks,
    }

    return {
        "doc_id": doc_id,
        "title": title or file.filename,
        "doc_type": detected_type,
        "text_length": len(extracted_text),
        "chunks": len(chunks),
        "warning": warning,
    }


@app.get("/documents")
async def list_documents():
    return {
        "total": len(documents_db),
        "documents": [
            {
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "doc_type": doc["doc_type"],
                "created_at": doc["created_at"],
                "text_length": doc["text_length"],
            }
            for doc in documents_db.values()
        ],
    }


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    doc = documents_db.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc

# ========== ASSIGNMENTS SYSTEM ==========
SAMPLE_ASSIGNMENTS = [
    {
        "assignment_id": "MATH001",
        "title": "Algebra Basics",
        "subject": "math",
        "class_grade": "10th",
        "description": "Practice algebraic expressions and equations",
        "pdf_url": "/assignments/math_algebra.pdf",
        "due_date": "2026-03-10",
        "total_marks": 20
    },
    {
        "assignment_id": "SCI001",
        "title": "Photosynthesis",
        "subject": "science",
        "class_grade": "10th",
        "description": "Study photosynthesis process in plants",
        "pdf_url": "/assignments/science_photosynthesis.pdf",
        "due_date": "2026-03-15",
        "total_marks": 15
    },
    {
        "assignment_id": "ENG001",
        "title": "Grammar Practice",
        "subject": "english",
        "class_grade": "9th",
        "description": "Practice tenses and sentence structure",
        "pdf_url": "/assignments/english_grammar.pdf",
        "due_date": "2026-03-08",
        "total_marks": 10
    },
    {
        "assignment_id": "PHY001",
        "title": "Laws of Motion",
        "subject": "physics",
        "class_grade": "11th",
        "description": "Newton's laws of motion problems",
        "pdf_url": "/assignments/physics_motion.pdf",
        "due_date": "2026-03-20",
        "total_marks": 25
    }
]

@app.get("/assignments/{class_grade}")
async def get_assignments(class_grade: str, subject: Optional[str] = None):
    """Get assignments for a specific class"""
    assignments = [a for a in SAMPLE_ASSIGNMENTS if a["class_grade"] == class_grade]
    
    if subject:
        assignments = [a for a in assignments if a["subject"] == subject.lower()]
    
    return {
        "class": class_grade,
        "total_assignments": len(assignments),
        "assignments": assignments
    }

# ========== PRACTICE PAPERS SYSTEM ==========
SAMPLE_PRACTICE_PAPERS = [
    {
        "paper_id": "CBSE2024",
        "title": "CBSE 10th Math 2024",
        "subject": "math",
        "year": "2024",
        "pdf_url": "/papers/cbse_math_2024.pdf",
        "solutions_url": "/solutions/cbse_math_2024_sol.pdf",
        "time_limit": 180
    },
    {
        "paper_id": "CBSE2023",
        "title": "CBSE 10th Science 2023",
        "subject": "science",
        "year": "2023",
        "pdf_url": "/papers/cbse_science_2023.pdf",
        "solutions_url": "/solutions/cbse_science_2023_sol.pdf",
        "time_limit": 180
    },
    {
        "paper_id": "ICSE2024",
        "title": "ICSE 9th English 2024",
        "subject": "english",
        "year": "2024",
        "pdf_url": "/papers/icse_english_2024.pdf",
        "solutions_url": None,
        "time_limit": 120
    },
    {
        "paper_id": "JEE2024",
        "title": "JEE Main 2024 Physics",
        "subject": "physics",
        "year": "2024",
        "pdf_url": "/papers/jee_physics_2024.pdf",
        "solutions_url": "/solutions/jee_physics_2024_sol.pdf",
        "time_limit": 180
    }
]

@app.get("/practice-papers")
async def get_practice_papers(subject: Optional[str] = None, year: Optional[str] = None):
    """Get practice papers"""
    papers = SAMPLE_PRACTICE_PAPERS
    
    if subject:
        papers = [p for p in papers if p["subject"] == subject.lower()]
    if year:
        papers = [p for p in papers if p["year"] == year]
    
    return {
        "total_papers": len(papers),
        "papers": papers
    }


@app.get("/diagram-spec")
async def get_diagram_spec(topic: str):
    """
    Return 2D/3D diagram spec for a topic.
    """
    key = (topic or "").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="Topic is required")
    spec = DIAGRAM_SPECS.get(key)
    if not spec:
        return {
            "topic": key,
            "available_topics": sorted(DIAGRAM_SPECS.keys()),
            "message": "No diagram spec found for this topic.",
        }
    return spec

# ========== DIAGRAM SPECS ==========
DIAGRAM_SPECS = {
    "photosynthesis": {
        "topic": "photosynthesis",
        "title": "Photosynthesis",
        "explanation": "Light energy is converted into chemical energy stored as glucose.",
        "chart": {
            "type": "line",
            "labels": ["Light", "Water Split", "ATP", "NADPH", "Glucose"],
            "datasets": [
                {
                    "label": "Energy Flow",
                    "data": [100, 85, 90, 75, 95],
                }
            ],
        },
        "model_topic": "photosynthesis",
    },
    "quantum": {
        "topic": "quantum",
        "title": "Quantum States",
        "explanation": "Probability distribution of electron positions across energy states.",
        "chart": {
            "type": "bar",
            "labels": ["State 1", "State 2", "State 3", "State 4"],
            "datasets": [
                {
                    "label": "Probability",
                    "data": [0.25, 0.4, 0.2, 0.15],
                }
            ],
        },
        "model_topic": "quantum",
    },
    "heart": {
        "topic": "heart",
        "title": "Heart Chambers",
        "explanation": "Blood pressure varies across chambers during a cardiac cycle.",
        "chart": {
            "type": "line",
            "labels": ["Atrium", "Ventricle", "Aorta", "Pulmonary"],
            "datasets": [
                {
                    "label": "Pressure (mmHg)",
                    "data": [10, 120, 100, 25],
                }
            ],
        },
        "model_topic": "heart",
    },
    "dna": {
        "topic": "dna",
        "title": "DNA Base Pairs",
        "explanation": "Bond strength differs between A-T and G-C pairs.",
        "chart": {
            "type": "bar",
            "labels": ["A-T", "G-C"],
            "datasets": [
                {
                    "label": "Bond Strength",
                    "data": [2, 3],
                }
            ],
        },
        "model_topic": "dna",
    },
    "newton": {
        "topic": "newton",
        "title": "Newton's Second Law",
        "explanation": "Force increases linearly with mass and acceleration.",
        "chart": {
            "type": "line",
            "labels": ["1", "2", "3", "4", "5"],
            "datasets": [
                {
                    "label": "Force (F=ma)",
                    "data": [2, 4, 6, 8, 10],
                }
            ],
        },
        "model_topic": "newton",
    },
    "python": {
        "topic": "python",
        "title": "Python Usage",
        "explanation": "Python is widely used across data, web, and automation.",
        "chart": {
            "type": "bar",
            "labels": ["Data", "Web", "Automation", "AI"],
            "datasets": [
                {
                    "label": "Usage Index",
                    "data": [80, 60, 70, 90],
                }
            ],
        },
        "model_topic": "python",
    },
}

# ========== QUIZ SYSTEM WITH HINTS ==========
SAMPLE_QUIZZES = {
    "math": [
        {
            "question_id": 1,
            "question": "What is 5 + 3?",
            "options": ["7", "8", "9", "10"],
            "correct_answer": 1,
            "hint": "Count from 5, add 3 more",
            "explanation": "5 + 3 = 8. When you add 3 to 5, you get 8."
        },
        {
            "question_id": 2,
            "question": "What is 12 × 5?",
            "options": ["50", "55", "60", "65"],
            "correct_answer": 2,
            "hint": "12 × 5 is same as 10×5 + 2×5",
            "explanation": "12 × 5 = 60. You can break it down: 10×5=50, 2×5=10, 50+10=60"
        },
        {
            "question_id": 3,
            "question": "What is the square root of 144?",
            "options": ["10", "11", "12", "13"],
            "correct_answer": 2,
            "hint": "Which number multiplied by itself gives 144?",
            "explanation": "12 × 12 = 144, so √144 = 12"
        }
    ],
    "science": [
        {
            "question_id": 4,
            "question": "What do plants need for photosynthesis?",
            "options": ["Water only", "Sunlight only", "Carbon dioxide only", "All of the above"],
            "correct_answer": 3,
            "hint": "Plants need multiple things from environment",
            "explanation": "Plants need water, sunlight, and carbon dioxide for photosynthesis"
        }
    ],
    "physics": [
        {
            "question_id": 5,
            "question": "What is the SI unit of force?",
            "options": ["Joule", "Newton", "Watt", "Pascal"],
            "correct_answer": 1,
            "hint": "Named after Sir Isaac Newton",
            "explanation": "The SI unit of force is Newton (N)"
        }
    ]
}


def _class_band(class_grade: str) -> str:
    value = class_grade.strip().lower()
    if value.startswith("btech"):
        return "ug_engineering"
    if value.startswith("msc"):
        return "pg_science"

    digits = "".join(ch for ch in value if ch.isdigit())
    if digits:
        grade_num = int(digits)
        if 1 <= grade_num <= 5:
            return "school_primary"
        if 6 <= grade_num <= 8:
            return "school_middle"
        if 9 <= grade_num <= 10:
            return "school_secondary"
        if 11 <= grade_num <= 12:
            return "school_senior_secondary"

    return "general"


def _suggestions_for_band(band: str, class_grade: str) -> List[SubjectSuggestion]:
    prompt_suffix = f" for {class_grade}"

    suggestion_map = {
        "school_primary": [
            SubjectSuggestion(subject="English", category="Language", reason="Build reading, writing, and communication basics.", starter_prompt=f"Teach English grammar basics{prompt_suffix} with examples.", priority=1),
            SubjectSuggestion(subject="Mathematics", category="STEM", reason="Strong number sense helps in all future classes.", starter_prompt=f"Teach Maths fundamentals{prompt_suffix} step by step.", priority=2),
            SubjectSuggestion(subject="EVS", category="Science", reason="Creates curiosity about nature and surroundings.", starter_prompt=f"Explain EVS topics{prompt_suffix} with daily life examples.", priority=3),
            SubjectSuggestion(subject="Computer Basics", category="Technology", reason="Early digital literacy improves confidence.", starter_prompt=f"Teach computer basics{prompt_suffix} in simple language.", priority=4),
        ],
        "school_middle": [
            SubjectSuggestion(subject="Mathematics", category="STEM", reason="Critical for algebra, geometry, and higher science.", starter_prompt=f"Teach Mathematics{prompt_suffix} with practice questions.", priority=1),
            SubjectSuggestion(subject="Science", category="STEM", reason="Builds concept foundation for physics, chemistry, biology.", starter_prompt=f"Explain Science concepts{prompt_suffix} with diagrams.", priority=2),
            SubjectSuggestion(subject="English", category="Language", reason="Needed for comprehension and exam writing.", starter_prompt=f"Teach English comprehension{prompt_suffix} with examples.", priority=3),
            SubjectSuggestion(subject="Social Science", category="Humanities", reason="Develops civic awareness and analytical thinking.", starter_prompt=f"Teach Social Science{prompt_suffix} in easy points.", priority=4),
            SubjectSuggestion(subject="Computer Science", category="Technology", reason="Supports coding and logical reasoning early.", starter_prompt=f"Teach computer science basics{prompt_suffix}.", priority=5),
        ],
        "school_secondary": [
            SubjectSuggestion(subject="Mathematics", category="STEM", reason="Core scoring subject and base for competitive exams.", starter_prompt=f"Create a Maths study plan{prompt_suffix} with daily targets.", priority=1),
            SubjectSuggestion(subject="Science", category="STEM", reason="Strong concepts are essential for board and entrance prep.", starter_prompt=f"Teach Science chapter wise{prompt_suffix} with key formulas.", priority=2),
            SubjectSuggestion(subject="English", category="Language", reason="Improves writing marks and interview readiness.", starter_prompt=f"Improve English writing{prompt_suffix} with sample answers.", priority=3),
            SubjectSuggestion(subject="Social Science", category="Humanities", reason="High scoring with structured revision strategy.", starter_prompt=f"Give Social Science revision notes{prompt_suffix}.", priority=4),
            SubjectSuggestion(subject="Computer Applications", category="Technology", reason="Useful practical subject for digital skills.", starter_prompt=f"Teach computer applications{prompt_suffix} with practical tasks.", priority=5),
        ],
        "school_senior_secondary": [
            SubjectSuggestion(subject="Physics", category="STEM", reason="Foundation for engineering and many science streams.", starter_prompt=f"Explain Physics concepts{prompt_suffix} with numericals.", priority=1),
            SubjectSuggestion(subject="Chemistry", category="STEM", reason="Important for medical, engineering, and board scores.", starter_prompt=f"Teach Chemistry{prompt_suffix} with reaction shortcuts.", priority=2),
            SubjectSuggestion(subject="Biology", category="STEM", reason="Key for NEET and life-science pathways.", starter_prompt=f"Teach Biology{prompt_suffix} with memory tricks.", priority=3),
            SubjectSuggestion(subject="Mathematics", category="STEM", reason="Essential for JEE, analytics, and technical fields.", starter_prompt=f"Build Maths problem-solving plan{prompt_suffix}.", priority=4),
            SubjectSuggestion(subject="Computer Science", category="Technology", reason="Strong career value in software and AI tracks.", starter_prompt=f"Teach computer science{prompt_suffix} with coding examples.", priority=5),
        ],
        "ug_engineering": [
            SubjectSuggestion(subject="Data Structures & Algorithms", category="Core CS", reason="Required for placements and coding tests.", starter_prompt=f"Teach DSA roadmap{prompt_suffix} with problem list.", priority=1),
            SubjectSuggestion(subject="Programming (Python/Java)", category="Core CS", reason="Core development skill for projects and internships.", starter_prompt=f"Create coding practice plan{prompt_suffix}.", priority=2),
            SubjectSuggestion(subject="Database Management Systems", category="Core CS", reason="Needed in almost every software role.", starter_prompt=f"Explain DBMS concepts{prompt_suffix} with SQL examples.", priority=3),
            SubjectSuggestion(subject="Operating Systems", category="Core CS", reason="Important for interviews and systems understanding.", starter_prompt=f"Teach OS concepts{prompt_suffix} with real scenarios.", priority=4),
            SubjectSuggestion(subject="Computer Networks", category="Core CS", reason="Useful for backend, cloud, and cybersecurity roles.", starter_prompt=f"Explain computer networks{prompt_suffix} simply.", priority=5),
            SubjectSuggestion(subject="AI & Machine Learning", category="Advanced", reason="High demand skill for modern tech careers.", starter_prompt=f"Start an AI/ML learning path{prompt_suffix}.", priority=6),
        ],
        "pg_science": [
            SubjectSuggestion(subject="Advanced Statistics", category="Research", reason="Essential for research quality and data analysis.", starter_prompt=f"Teach advanced statistics{prompt_suffix} with examples.", priority=1),
            SubjectSuggestion(subject="Research Methodology", category="Research", reason="Improves thesis and publication outcomes.", starter_prompt=f"Explain research methodology{prompt_suffix}.", priority=2),
            SubjectSuggestion(subject="Domain Core Papers", category="Specialization", reason="Directly impacts semester and specialization depth.", starter_prompt=f"Make core paper revision plan{prompt_suffix}.", priority=3),
            SubjectSuggestion(subject="Scientific Writing", category="Communication", reason="Critical for papers, reports, and presentations.", starter_prompt=f"Teach scientific writing basics{prompt_suffix}.", priority=4),
            SubjectSuggestion(subject="Data Analysis Tools", category="Technology", reason="Boosts employability and research efficiency.", starter_prompt=f"Teach data analysis tools{prompt_suffix}.", priority=5),
        ],
        "general": [
            SubjectSuggestion(subject="Mathematics", category="STEM", reason="Foundational subject for logical thinking.", starter_prompt=f"Teach maths basics{prompt_suffix} clearly.", priority=1),
            SubjectSuggestion(subject="Science", category="STEM", reason="Core understanding of natural world and applications.", starter_prompt=f"Explain science topics{prompt_suffix} in simple words.", priority=2),
            SubjectSuggestion(subject="English", category="Language", reason="Helps learning across all subjects.", starter_prompt=f"Teach English communication{prompt_suffix}.", priority=3),
            SubjectSuggestion(subject="Computer Science", category="Technology", reason="Important digital and career skill.", starter_prompt=f"Start computer science learning{prompt_suffix}.", priority=4),
        ],
    }

    return suggestion_map.get(band, suggestion_map["general"])


@app.get("/subject-suggestions/{class_grade}")
async def get_subject_suggestions(class_grade: str, language: str = "english"):
    """
    AI-style subject suggestions based on selected class.
    """
    band = _class_band(class_grade)
    suggestions = _suggestions_for_band(band, class_grade)

    return {
        "class_grade": class_grade,
        "language": language,
        "band": band,
        "recommended_subjects": [s.model_dump() for s in suggestions],
        "learning_tip": "Pick 3 priority subjects first, then study 45-60 minutes daily with short revision.",
        "next_action": "Select suggested subjects and click Learn Now to start guided explanations."
    }

@app.get("/quiz/{subject}")
async def get_quiz(subject: str):
    """Get quiz questions for a subject"""
    subject_lower = subject.lower()
    if subject_lower not in SAMPLE_QUIZZES:
        raise HTTPException(status_code=404, detail=f"No quiz available for {subject}")
    
    return {
        "subject": subject,
        "total_questions": len(SAMPLE_QUIZZES[subject_lower]),
        "questions": SAMPLE_QUIZZES[subject_lower]
    }


class QuizSubmit(BaseModel):
    student_id: str
    subject: str
    question_id: int
    selected_answer: int


@app.post("/quiz/submit")
async def submit_quiz_answer(payload: QuizSubmit):
    subject_lower = payload.subject.lower()
    if subject_lower not in SAMPLE_QUIZZES:
        raise HTTPException(status_code=404, detail="No quiz available for subject")

    question = None
    for item in SAMPLE_QUIZZES[subject_lower]:
        if item["question_id"] == payload.question_id:
            question = item
            break
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    is_correct = payload.selected_answer == question["correct_answer"]
    score = 1.0 if is_correct else 0.0

    if payload.student_id not in students_db:
        _load_student(payload.student_id)
    if payload.student_id in students_db:
        topic = f"{payload.subject} quiz Q{payload.question_id}"
        _record_progress_event(
            payload.student_id,
            topic=topic,
            event_type="quiz",
            score=score,
            completed=True,
            notes="auto-tracked from quiz",
        )

    return {
        "correct": is_correct,
        "score": score,
        "explanation": question["explanation"],
        "correct_answer": question["correct_answer"],
    }

@app.get("/hint/{question_id}")
async def get_hint(question_id: int):
    """Get hint for a specific question"""
    # Search for hint in all quizzes
    for subject, questions in SAMPLE_QUIZZES.items():
        for question in questions:
            if question["question_id"] == question_id:
                return {
                    "question_id": question_id,
                    "hint": question["hint"],
                    "explanation": question["explanation"]
                }
    
    return {
        "question_id": question_id,
        "hint": "Try breaking the problem into smaller steps",
        "explanation": "Think about what you know and work step by step"
    }

# ========== STUDENT PROGRESS ==========
@app.get("/students/{student_id}")
async def get_student(student_id: str):
    """Get student information"""
    if student_id not in students_db:
        student = _load_student(student_id)
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        return student
    return students_db[student_id]

@app.post("/students/{student_id}/progress")
async def update_student_progress(student_id: str, progress: ProgressUpdate):
    """Update student progress using Pydantic model"""
    if student_id not in students_db:
        if not _load_student(student_id):
            raise HTTPException(status_code=404, detail="Student not found")
    
    # Get the topic as a string (it's guaranteed to be a string from the model)
    topic_key: str = progress.topic
    
    # Convert Pydantic model to dict
    progress_dict = progress.model_dump()
    
    # Update the progress
    students_db[student_id]["progress"][topic_key] = progress_dict

    _record_progress_event(
        student_id,
        topic=topic_key,
        event_type="manual",
        score=progress.score,
        completed=progress.completed,
        notes=progress.notes,
    )
    
    return {
        "message": "Progress updated successfully",
        "student_id": student_id,
        "topic": topic_key,
        "progress": students_db[student_id]["progress"]
    }


@app.get("/students/{student_id}/progress/events")
async def get_progress_events(student_id: str, limit: int = 50):
    if student_id not in students_db:
        if not _load_student(student_id):
            raise HTTPException(status_code=404, detail="Student not found")
    limit = max(1, min(limit, 200))
    rows = _db_query(
        """
        SELECT id, topic, score, completed, notes, event_type, timestamp
        FROM progress_events
        WHERE student_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (student_id, limit),
    )
    return {"student_id": student_id, "events": rows}


@app.get("/students/{student_id}/progress/summary")
async def get_progress_summary(student_id: str):
    if student_id not in students_db:
        if not _load_student(student_id):
            raise HTTPException(status_code=404, detail="Student not found")
    return _progress_summary(student_id)

# ========== ADDITIONAL UTILITY ENDPOINTS ==========
@app.get("/languages")
async def get_supported_languages():
    """Get list of supported languages"""
    return {
        "languages": [
            {"code": "en-IN", "name": "English (India)"},
            {"code": "hi-IN", "name": "हिन्दी (Hindi)"},
            {"code": "ta-IN", "name": "தமிழ் (Tamil)"},
            {"code": "te-IN", "name": "తెలుగు (Telugu)"},
            {"code": "mr-IN", "name": "मराठी (Marathi)"},
            {"code": "gu-IN", "name": "ગુજરાતી (Gujarati)"},
            {"code": "bn-IN", "name": "বাংলা (Bengali)"}
        ]
    }

@app.get("/subjects")
async def get_subjects():
    """Get list of available subjects"""
    return {
        "subjects": ["math", "science", "physics", "chemistry", "biology", "english", "history", "geography"]
    }


# ========== FEEDBACK ==========
@app.post("/feedback")
async def submit_feedback(payload: FeedbackRequest):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Feedback message is required")
    _record_feedback(payload)
    return {"message": "Feedback received. Thank you!", "rating": payload.rating}


@app.get("/feedback/summary")
async def get_feedback_summary(limit: int = 20):
    limit = max(1, min(limit, 100))
    return _feedback_summary(limit=limit)

# ========== RUN SERVER ==========
if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting AI Teacher Assistant Server...")
    print("📝 API Documentation: http://localhost:8003/docs")
    print("🔍 Health Check: http://localhost:8003/health")
    print("💬 Chat Endpoint: http://localhost:8003/chat")
    print("🎤 Voice Features: http://localhost:8003/voice-to-text")
    print("📚 Assignments: http://localhost:8003/assignments/10th")
    print("📝 Quiz: http://localhost:8003/quiz/math")
    print("\n" + "="*50)
    print(f"OpenAI Status: {'✅ Configured' if client else '❌ Not Configured'}")
    if not client:
        print("💡 Tip: Create a .env file with OPENAI_API_KEY=your_key_here")
    print("="*50 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8003, reload=True)
