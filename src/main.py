from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict, Tuple
import uuid
from datetime import datetime
try:
    import speech_recognition as sr
except Exception:
    sr = None
import tempfile
import os
try:
    import pyttsx3
except Exception:
    pyttsx3 = None
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

# Optional Gemini clients
try:
    from google import genai as genai_new
except Exception:
    genai_new = None

try:
    import google.generativeai as genai_legacy  # pyright: ignore[reportMissingImports]
except Exception:
    genai_legacy = None

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize OpenAI/Gemini/DeepSeek clients (with error handling)
client = None
gemini_client = None
gemini_client_type: Optional[str] = None
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
if gemini_api_key:
    if genai_new is not None:
        try:
            gemini_client = genai_new.Client(api_key=gemini_api_key)
            gemini_client_type = "new"
        except Exception:
            gemini_client = None
            gemini_client_type = None
    elif genai_legacy is not None:
        try:
            genai_legacy.configure(api_key=gemini_api_key)
            gemini_client = genai_legacy
            gemini_client_type = "legacy"
        except Exception:
            gemini_client = None
            gemini_client_type = None

# DeepSeek client (OpenAI-compatible)
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
if OpenAI is not None and deepseek_api_key:
    try:
        deepseek_client = OpenAI(api_key=deepseek_api_key, base_url="https://api.deepseek.com")
    except Exception:
        deepseek_client = None

huggingface_api_key = os.getenv("HUGGINGFACE_API_KEY")
ollama_base_url = (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _is_ollama_reachable() -> bool:
    try:
        with httpx.Client(timeout=1.0) as http:
            response = http.get(f"{ollama_base_url}/api/tags")
        return response.status_code == 200
    except Exception:
        return False


ollama_available = _is_ollama_reachable()


def _log_ai_error(message: str) -> None:
    if (os.getenv("DEBUG_LOG_AI_ERRORS") or "").strip().lower() in {"1", "true", "yes", "on"}:
        print(message)

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
    explanation: str = ""
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
            "Assignments and study practice",
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
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

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
    
    if sr is None:
        raise HTTPException(status_code=503, detail="SpeechRecognition package is not installed")

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
        
    except Exception as e:
        if sr is not None and isinstance(e, sr.UnknownValueError):
            return {
                "success": False,
                "text": "",
                "error": "Could not understand audio. Please speak clearly."
            }
        if sr is not None and isinstance(e, sr.RequestError):
            return {
                "success": False,
                "text": "",
                "error": f"Speech recognition service error: {str(e)}"
            }
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

    if pyttsx3 is None:
        raise HTTPException(status_code=503, detail="pyttsx3 package is not installed")
    
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
    Offline helper that gives structured educational responses when no live provider is available.
    """
    message_lower = (message or "").lower()
    
    # 1. Handle JSON/array/outline formatting requests in offline fallback
    if "json" in message_lower or "array" in message_lower:
        topic_match = re.search(r"about\s+([^.\n]+)", message, re.IGNORECASE) or \
                      re.search(r"topic\s+([^.\n]+)", message, re.IGNORECASE) or \
                      re.search(r"for\s+([^.\n]+)", message, re.IGNORECASE)
        topic = topic_match.group(1).strip(" '\"`{}[]") if topic_match else "General Science"
        topic_title = topic.title()
        
        # 1.a Flashcards
        if "flashcard" in message_lower:
            return json.dumps([
                {
                    "front": f"What is the main definition of {topic_title}?",
                    "back": f"The core scientific study or process involving {topic_title} in educational contexts."
                },
                {
                    "front": f"Key component of {topic_title}",
                    "back": f"An essential element required for {topic_title} to function or exist properly."
                },
                {
                    "front": f"Primary application of {topic_title}",
                    "back": f"Where and how {topic_title} is observed, applied, or utilized in the real world."
                },
                {
                    "front": f"Why is {topic_title} important?",
                    "back": "It provides fundamental structure, energy, or logical clarity to the system."
                }
            ])
            
        # 1.b Quiz
        elif "quiz" in message_lower or "multiple choice" in message_lower:
            return json.dumps([
                {
                    "question": f"Which of the following best defines {topic_title}?",
                    "options": [
                        f"A fundamental system or process representing {topic_title}.",
                        "A chemical compound used as a catalyst.",
                        "An auxiliary source of light waves.",
                        "A mechanical unit for gravitational force."
                    ],
                    "answer": f"A fundamental system or process representing {topic_title}.",
                    "explanation": f"{topic_title} is historically and practically defined as a core concept or process."
                },
                {
                    "question": f"Where is {topic_title} primarily observed or studied?",
                    "options": [
                        "In outer space vacuums only.",
                        "Under ocean hydrothermal vents.",
                        "In schools, labs, and natural ecosystems.",
                        "Only in micro-electronic devices."
                    ],
                    "answer": "In schools, labs, and natural ecosystems.",
                    "explanation": f"As a general learning concept, {topic_title} is analyzed in nature and science classrooms."
                },
                {
                    "question": f"What is an essential component required for {topic_title}?",
                    "options": [
                        "A liquid nitrogen cooling chamber.",
                        "Proper inputs, structure, or energy sources.",
                        "An advanced quantum computer chip.",
                        "A helium gas enclosure."
                    ],
                    "answer": "Proper inputs, structure, or energy sources.",
                    "explanation": f"Most natural or logical systems like {topic_title} depend on essential energy or structured inputs."
                },
                {
                    "question": f"Which of the following is a classic example of {topic_title} in action?",
                    "options": [
                        f"Standard systems demonstrating {topic_title}.",
                        "A copper coil conducting heat.",
                        "A simple iron nail rusting in water.",
                        "An inert neon bulb glowing."
                    ],
                    "answer": f"Standard systems demonstrating {topic_title}.",
                    "explanation": f"A textbook demonstration provides the perfect practical model for {topic_title}."
                },
                {
                    "question": f"Why is {topic_title} crucial to understand?",
                    "options": [
                        "It has no practical importance.",
                        "It explains vital principles of science or logic.",
                        "It is required to operate a basic vehicle.",
                        "It is a prerequisite for writing binary code."
                    ],
                    "answer": "It explains vital principles of science or logic.",
                    "explanation": f"Understanding {topic_title} opens paths to advanced scientific and analytical disciplines."
                }
            ])
            
        # 1.c Assignments
        elif "assignment" in message_lower:
            return json.dumps({
                "assignments": [
                    {
                        "title": f"Mastering {topic_title} - Part 1",
                        "subject": "General Studies",
                        "class_grade": "All Classes",
                        "due_date": "Next Monday",
                        "total_marks": 50,
                        "description": f"Write a comprehensive 300-word essay detailing the definition, key stages, and direct impacts of {topic_title}."
                    },
                    {
                        "title": f"Practical Models of {topic_title}",
                        "subject": "Lab Session",
                        "class_grade": "All Classes",
                        "due_date": "Next Friday",
                        "total_marks": 30,
                        "description": f"Identify a real-life scenario or textbook experiment demonstrating {topic_title} and sketch a clean diagram."
                    },
                    {
                        "title": f"Comparing {topic_title} and Allied Concepts",
                        "subject": "Research Task",
                        "class_grade": "All Classes",
                        "due_date": "In two weeks",
                        "total_marks": 20,
                        "description": f"Compare {topic_title} with another relevant concept in a structured tabular format showing 3 key differences."
                    }
                ]
            })
            
        # 1.d Practice Papers
        elif "paper" in message_lower:
            return json.dumps({
                "papers": [
                    {
                        "title": f"{topic_title} Comprehensive Practice Paper",
                        "subject": "General Exam",
                        "year": "2026",
                        "time_limit": 60,
                        "description": f"A full-length mock paper covering definition, core concepts, and multiple-choice questions about {topic_title}."
                    },
                    {
                        "title": f"{topic_title} Advanced Conceptual Exam",
                        "subject": "Advanced Science",
                        "year": "2026",
                        "time_limit": 120,
                        "description": f"Deeper theoretical test focusing on advanced applications, formulas, and structural processes of {topic_title}."
                    }
                ]
            })

    # 2. Handle Mind Maps (markdown outline format) in offline fallback
    if "mind map" in message_lower or "mindmap" in message_lower:
        topic_match = re.search(r"concept:\s*\"([^\"]+)\"", message, re.IGNORECASE) or \
                      re.search(r"concept\s+([^.\n]+)", message, re.IGNORECASE) or \
                      re.search(r"for\s+([^.\n]+)", message, re.IGNORECASE)
        topic = topic_match.group(1).strip(" '\"`{}[]") if topic_match else "Science Topic"
        topic_title = topic.title()
        return (
            f"* {topic_title}\n"
            f"  * Core Definition\n"
            f"    * Primary meaning of {topic_title}\n"
            f"    * Fundamental components and scope\n"
            f"  * Essential Requirements\n"
            f"    * Primary input parameters\n"
            f"    * Required environmental conditions\n"
            f"  * Key Subtopics & Branches\n"
            f"    * Primary sub-systems\n"
            f"    * Secondary attributes\n"
            f"  * Real-world Applications\n"
            f"    * Daily life examples\n"
            f"    * Advanced technological usecases"
        )

    def _normalize_common_topic_variants(text: str) -> str:
        normalized = (text or "")
        replacements = [
            (r"\bunsuper\s*vised\b", "unsupervised"),
            (r"\bsuper\s*vised\b", "supervised"),
            (r"\breinforce\s*ment\b", "reinforcement"),
            (r"\bmachine\s*learning\b", "machine learning"),
        ]
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        return normalized

    def _extract_topic(text: str) -> str:
        cleaned_text = re.sub(r"\s+", " ", (text or "").strip(" ?."))
        patterns = [
            r"^(?:what is|what are|define|explain)\s+(.+)$",
            r"^(?:who is|who was)\s+(.+)$",
            r"^(?:how does|how do|how can)\s+(.+)$",
            r"^(?:solve|calculate|find)\s+(.+)$",
        ]
        lowered_text = cleaned_text.lower()
        for pattern in patterns:
            match = re.match(pattern, lowered_text)
            if match:
                extracted = match.group(1).strip()
                if extracted:
                    return extracted
        return cleaned_text or "this topic"

    def _normalize_topic_label(text: str) -> str:
        topic = _extract_topic(text)
        topic = re.sub(r"\bfor class\s+\d+\b", "", topic, flags=re.IGNORECASE)
        topic = re.sub(r"\bclass\s+\d+\b", "", topic, flags=re.IGNORECASE)
        topic = re.sub(r"\bfor students\b", "", topic, flags=re.IGNORECASE)
        topic = re.sub(r"\bstep by step\b", "", topic, flags=re.IGNORECASE)
        topic = re.sub(r"\s+", " ", topic).strip(" ,.-")
        return topic or "this topic"

    def _build_generic_offline_response(topic: str, topic_title: str, lowered_message: str) -> str:
        domain_rules = [
            (
                {"learning", "algorithm", "data", "computer", "program", "network", "database", "coding", "software", "ai"},
                f"{topic_title} is a concept in computer science or technology.",
                f"It is usually studied to understand how {topic} works, what parts it uses, and where it is applied.",
                f"In practice, {topic} is connected to problem-solving, systems, or digital tools.",
            ),
            (
                {"cell", "plant", "animal", "body", "heart", "dna", "gene", "blood", "photosynthesis", "digestion"},
                f"{topic_title} is a concept in biology or life science.",
                f"It helps explain a natural process, body function, living structure, or scientific idea related to {topic}.",
                f"In real life, {topic} is understood by connecting it to organisms, health, or the environment.",
            ),
            (
                {"force", "energy", "motion", "electricity", "sound", "light", "gravity", "atom", "quantum"},
                f"{topic_title} is a concept in physics.",
                f"It explains how matter, energy, forces, or motion are related to {topic}.",
                f"In real life, {topic} can be observed in machines, movement, electricity, or natural events.",
            ),
            (
                {"fraction", "algebra", "equation", "number", "geometry", "ratio", "percentage", "probability"},
                f"{topic_title} is a mathematics topic.",
                f"It is learned by understanding the rule or formula behind {topic} and then practicing questions.",
                f"In real life, {topic} is useful in measurement, calculation, comparison, and problem-solving.",
            ),
        ]

        topic_tokens = set(re.findall(r"[a-zA-Z]+", f"{topic} {lowered_message}".lower()))
        intro = f"{topic_title} is an important topic."
        concept_line = f"It is studied to understand what {topic} means, how it works, and where it is used."
        example_line = f"A good way to learn {topic} is to connect the definition with one simple real-life example."

        for keywords, domain_intro, domain_concept, domain_example in domain_rules:
            if topic_tokens.intersection(keywords):
                intro = domain_intro
                concept_line = domain_concept
                example_line = domain_example
                break

        return (
            f"{intro}\n"
            f"1. Start with the definition: understand the basic meaning of {topic}.\n"
            f"2. Focus on the main idea: {concept_line}\n"
            f"3. Learn the key parts, rules, steps, or components related to {topic}.\n"
            f"4. Use one example to see how {topic} works in a real situation.\n"
            f"5. Remember why it matters and where it is commonly applied.\n\n"
            f"Summary: {example_line}"
        )

    lang = language if language in {
        "english", "hindi", "tamil", "telugu", "marathi", "gujarati", "bengali"
    } else "english"
    normalized_message = _normalize_common_topic_variants(message or "")
    message_lower = normalized_message.lower().strip()

    localized_defaults = {
        "english": {
            "greeting": "Hello! I can still help in offline mode. Ask me a topic like fractions, photosynthesis, gravity, or machine learning.",
            "generic": "Here is a simple explanation of the topic."
        },
        "hindi": {
            "greeting": "नमस्ते! मैं ऑफलाइन मोड में भी मदद कर सकता हूं। fractions, photosynthesis, gravity या machine learning जैसे विषय पूछें।",
            "generic": "यहाँ विषय की आसान व्याख्या दी गई है।"
        },
        "tamil": {
            "greeting": "வணக்கம்! ஆஃப்லைன் முறையிலும் நான் உதவலாம். fractions, photosynthesis, gravity அல்லது machine learning போன்ற தலைப்புகளை கேளுங்கள்.",
            "generic": "இதோ தலைப்பின் எளிய விளக்கம்."
        },
        "telugu": {
            "greeting": "నమస్కారం! ఆఫ్లైన్ మోడ్‌లో కూడా నేను సహాయం చేయగలను. fractions, photosynthesis, gravity లేదా machine learning వంటి విషయాలు అడగండి.",
            "generic": "ఇది ఆ విషయానికి సరళమైన వివరణ."
        },
        "marathi": {
            "greeting": "नमस्कार! ऑफलाइन मोडमध्येही मी मदत करू शकतो. fractions, photosynthesis, gravity किंवा machine learning सारखे विषय विचारा.",
            "generic": "या विषयाचे सोपे स्पष्टीकरण येथे दिले आहे."
        },
        "gujarati": {
            "greeting": "નમસ્તે! ઑફલાઇન મોડમાં પણ હું મદદ કરી શકું છું. fractions, photosynthesis, gravity અથવા machine learning જેવા વિષયો પૂછો.",
            "generic": "અહીં વિષયનું સરળ સમજૂતી આપવામાં આવ્યું છે."
        },
        "bengali": {
            "greeting": "নমস্কার! অফলাইন মোডেও আমি সাহায্য করতে পারি। fractions, photosynthesis, gravity বা machine learning-এর মতো বিষয় জিজ্ঞাসা করো।",
            "generic": "এখানে বিষয়টির সহজ ব্যাখ্যা দেওয়া হলো।"
        },
    }

    if any(greeting in message_lower for greeting in ["hello", "hi", "hey", "नमस्ते", "வணக்கம்", "నమస్కారం", "नमस्कार", "નમસ્તે", "নমস্কার"]):
        return localized_defaults[lang]["greeting"]

    topic_map = {
        "photosynthesis": {
            "keywords": ["photosynthesis", "plant food", "chlorophyll"],
            "response": (
                "Photosynthesis is how green plants make their own food.\n"
                "1. Roots absorb water from the soil.\n"
                "2. Leaves take in carbon dioxide from the air.\n"
                "3. Chlorophyll traps sunlight.\n"
                "4. The plant uses sunlight to turn water and carbon dioxide into glucose, which is food.\n"
                "5. Oxygen is released into the air.\n\n"
                "Summary: sunlight + water + carbon dioxide -> food + oxygen.\n"
                "Practice: Why are leaves important in photosynthesis?"
            ),
        },
        "gravity": {
            "keywords": ["gravity", "gravitational", "falling objects", "newton law of gravitation"],
            "response": (
                "Gravity is the force that pulls objects toward each other.\n"
                "1. Earth pulls everything toward its center.\n"
                "2. That is why objects fall down when dropped.\n"
                "3. Gravity keeps us on the ground.\n"
                "4. Gravity also keeps the Moon around Earth and planets around the Sun.\n\n"
                "Summary: gravity is an invisible pulling force.\n"
                "Practice: Why does a ball fall back to the ground after being thrown upward?"
            ),
        },
        "fractions": {
            "keywords": ["fraction", "fractions", "numerator", "denominator", "half", "quarter"],
            "response": (
                "A fraction shows equal parts of a whole.\n"
                "1. The top number is the numerator. It tells how many parts we have.\n"
                "2. The bottom number is the denominator. It tells how many equal parts make the whole.\n"
                "3. In 3/4, 3 is the numerator and 4 is the denominator.\n"
                "4. Fractions can represent parts of pizza, chocolate, time, and distance.\n\n"
                "Summary: fractions describe parts of one complete thing.\n"
                "Practice: In 2/5, which number is the denominator?"
            ),
        },
        "algebra": {
            "keywords": ["algebra", "equation", "variable", "solve for x"],
            "response": (
                "Algebra uses letters to represent unknown numbers.\n"
                "1. A variable like x stands for a value we need to find.\n"
                "2. An equation shows two expressions are equal.\n"
                "3. To solve, do the same operation on both sides.\n"
                "4. Example: x + 3 = 7, so subtract 3 from both sides and get x = 4.\n\n"
                "Summary: algebra helps us find unknown values logically.\n"
                "Practice: Solve x + 5 = 11."
            ),
        },
        "neural_networks": {
            "keywords": ["neural network", "neural networks", "artificial neuron", "deep learning model"],
            "response": (
                "A neural network is a machine learning model inspired by the way brain cells connect and pass signals.\n"
                "1. It is made of layers of small units called neurons.\n"
                "2. The input layer receives data, hidden layers process patterns, and the output layer gives the result.\n"
                "3. During training, the network adjusts weights so it can make better predictions.\n"
                "4. Neural networks are used in image recognition, speech recognition, translation, and recommendation systems.\n\n"
                "Summary: a neural network learns patterns by passing data through connected layers.\n"
                "Practice: What do hidden layers do in a neural network?"
            ),
        },
        "machine_learning": {
            "keywords": ["machine learning", "ml", "artificial intelligence", "ai model", " ai ", "what is ai", "ai?"],
            "response": (
                "Artificial intelligence, or AI, is when computers do tasks that usually need human thinking.\n"
                "1. AI can learn from data, follow rules, and find patterns.\n"
                "2. It is used to answer questions, translate languages, recognize images, and recommend videos.\n"
                "3. Machine learning is one important part of AI.\n"
                "4. In real life, AI appears in chatbots, maps, voice assistants, and spam filters.\n\n"
                "Summary: AI helps computers act in smart ways to solve problems.\n"
                "Practice: Name one app or device where you have seen AI."
            ),
        },
        "unsupervised_learning": {
            "keywords": ["unsupervised learning", "clustering", "k means", "k-means"],
            "response": (
                "Unsupervised learning is a type of machine learning where the computer learns patterns from data without being given correct answers.\n"
                "1. The data is not labeled, so the computer must find groups or patterns on its own.\n"
                "2. It is often used for clustering similar items together.\n"
                "3. For example, it can group customers with similar buying habits.\n"
                "4. Common methods include clustering and dimensionality reduction.\n\n"
                "Summary: unsupervised learning finds hidden patterns in unlabeled data.\n"
                "Practice: What is the main difference between labeled and unlabeled data?"
            ),
        },
        "supervised_learning": {
            "keywords": ["supervised learning", "labeled data", "classification", "regression"],
            "response": (
                "Supervised learning is a type of machine learning where the computer learns from labeled examples.\n"
                "1. Each training example includes the correct answer.\n"
                "2. The model learns the relationship between input and output.\n"
                "3. It is used in tasks like classification and prediction.\n"
                "4. For example, it can learn to identify spam emails using labeled spam and non-spam examples.\n\n"
                "Summary: supervised learning uses labeled data to learn correct outputs.\n"
                "Practice: Why are labels important in supervised learning?"
            ),
        },
        "reinforcement_learning": {
            "keywords": ["reinforcement learning", "agent", "reward", "penalty"],
            "response": (
                "Reinforcement learning is a type of machine learning where an agent learns by trial and error.\n"
                "1. The agent takes actions in an environment.\n"
                "2. It receives rewards for good actions and penalties for bad ones.\n"
                "3. Over time, it learns which actions give the best result.\n"
                "4. It is used in robotics, games, and decision-making systems.\n\n"
                "Summary: reinforcement learning improves behavior by learning from rewards and penalties.\n"
                "Practice: What helps the agent know which action is better?"
            ),
        },
        "water_cycle": {
            "keywords": ["water cycle", "evaporation", "condensation", "precipitation"],
            "response": (
                "The water cycle shows how water moves around Earth.\n"
                "1. Heat from the Sun causes evaporation.\n"
                "2. Water vapor rises and cools.\n"
                "3. It condenses into clouds.\n"
                "4. When droplets become heavy, precipitation falls as rain or snow.\n"
                "5. Water collects in rivers, lakes, and oceans, and the cycle repeats.\n\n"
                "Summary: evaporation, condensation, and precipitation are the key stages.\n"
                "Practice: What role does the Sun play in the water cycle?"
            ),
        },
        "cell": {
            "keywords": ["cell", "cells", "plant cell", "animal cell", "nucleus"],
            "response": (
                "A cell is the basic unit of life.\n"
                "1. All living things are made of cells.\n"
                "2. The nucleus controls cell activities.\n"
                "3. Cytoplasm holds the parts of the cell.\n"
                "4. The cell membrane controls what goes in and out.\n"
                "5. Plant cells also have a cell wall and chloroplasts.\n\n"
                "Summary: cells are the tiny building blocks of organisms.\n"
                "Practice: Name one part found in plant cells but not in animal cells."
            ),
        },
        "solar_system": {
            "keywords": ["solar system", "planets", "sun", "earth and planets"],
            "response": (
                "The solar system includes the Sun and the objects that move around it.\n"
                "1. The Sun is a star and the center of the solar system.\n"
                "2. Eight planets revolve around the Sun.\n"
                "3. Earth is the third planet from the Sun.\n"
                "4. Gravity keeps planets in orbit.\n\n"
                "Summary: the Sun is central, and planets move around it because of gravity.\n"
                "Practice: Which planet do we live on?"
            ),
        },
        "blockchain": {
            "keywords": ["blockchain", "bitcoin", "crypto ledger", "distributed ledger"],
            "response": (
                "Blockchain is a digital record book that stores information in connected blocks.\n"
                "1. Each block contains a group of records or transactions.\n"
                "2. When one block is filled, it links to the previous block, forming a chain.\n"
                "3. Copies of the record can be stored on many computers, so it is hard to change secretly.\n"
                "4. This helps people keep records that are transparent and secure.\n"
                "5. Blockchain is used in cryptocurrencies, tracking goods, and storing verified records.\n\n"
                "Summary: blockchain is a secure chain of digital records shared across many computers.\n"
                "Practice: Why is it difficult to secretly change information in a blockchain?"
            ),
        },
        "internet": {
            "keywords": ["internet", "www", "world wide web", "website", "web browser"],
            "response": (
                "The internet is a huge network that connects computers around the world.\n"
                "1. It allows devices to send and receive information.\n"
                "2. Websites, emails, videos, and online games all use the internet.\n"
                "3. A browser helps you open websites on the web.\n"
                "4. Data travels in small packets from one place to another.\n\n"
                "Summary: the internet helps people and devices share information globally.\n"
                "Practice: Name two things you can do using the internet."
            ),
        },
        "computer": {
            "keywords": ["computer", "cpu", "keyboard", "monitor", "hardware", "software"],
            "response": (
                "A computer is an electronic machine that takes input, processes it, and gives output.\n"
                "1. Input devices include keyboard and mouse.\n"
                "2. The CPU processes instructions.\n"
                "3. Memory stores data while work is being done.\n"
                "4. Output devices include monitor and printer.\n"
                "5. Software tells the computer what to do.\n\n"
                "Summary: a computer follows instructions to solve tasks and show results.\n"
                "Practice: What is the job of the CPU in a computer?"
            ),
        },
        "dsa": {
            "keywords": ["dsa", "data structures", "algorithms", "array", "linked list", "stack", "queue"],
            "response": (
                "DSA means Data Structures and Algorithms.\n"
                "1. Data structures are ways to organize data, like arrays, stacks, queues, trees, and graphs.\n"
                "2. Algorithms are step-by-step methods used to solve problems.\n"
                "3. We study DSA to write programs that are faster, cleaner, and easier to improve.\n"
                "4. For example, searching for a name in a phone list can be done in smarter ways using algorithms.\n"
                "5. DSA is important for coding interviews and strong programming skills.\n\n"
                "Summary: DSA helps us store data well and solve problems efficiently.\n"
                "Practice: Can you name one data structure and one algorithm?"
            ),
        },
        "osmosis": {
            "keywords": ["osmosis", "selectively permeable membrane", "water movement"],
            "response": (
                "Osmosis is the movement of water through a semipermeable membrane from a region with more water to a region with less water.\n"
                "1. Water moves to balance the concentration on both sides.\n"
                "2. The membrane allows water to pass but blocks some dissolved substances.\n"
                "3. In plants, osmosis helps roots absorb water from the soil.\n"
                "4. In cells, it helps maintain the right amount of water.\n\n"
                "Summary: osmosis is how water moves across a membrane to balance concentration.\n"
                "Practice: Why do plant roots need osmosis?"
            ),
        },
        "volcano": {
            "keywords": ["volcano", "magma", "lava", "eruption"],
            "response": (
                "A volcano is an opening in the Earth's crust through which hot melted rock, gases, and ash can come out.\n"
                "1. Hot melted rock inside the Earth is called magma.\n"
                "2. When magma reaches the surface, it is called lava.\n"
                "3. Pressure builds inside the Earth and can cause an eruption.\n"
                "4. Volcanoes can form mountains and change the land around them.\n\n"
                "Summary: volcanoes release magma, ash, and gases from inside the Earth.\n"
                "Practice: What is the difference between magma and lava?"
            ),
        },
    }

    padded_message = f" {message_lower} "
    for topic in topic_map.values():
        if any(keyword in padded_message or keyword in message_lower for keyword in topic["keywords"]):
            return topic["response"]

    if any(word in message_lower for word in ["math", "गणित", "கணிதம்", "గణితం", "calculate", "addition", "subtraction", "multiplication", "division", "algebra", "geometry"]):
        return (
            "Math becomes easier when we break it into steps.\n"
            "1. Identify what is given.\n"
            "2. Identify what you must find.\n"
            "3. Choose the correct operation or formula.\n"
            "4. Solve carefully and check the answer.\n\n"
            "Send the exact question, and I will work it out step by step."
        )

    if any(word in message_lower for word in ["science", "विज्ञान", "அறிவியல்", "సైన్స్", "physics", "chemistry", "biology", "experiment", "reaction"]):
        return (
            "Science explains how the world works through observation and evidence.\n"
            "1. Observe what happens.\n"
            "2. Ask why it happens.\n"
            "3. Test ideas with experiments or examples.\n"
            "4. Use the result to build understanding.\n\n"
            "Tell me the exact topic, such as force, atoms, digestion, or electricity."
        )

    clean_topic = _normalize_topic_label(normalized_message)[:80]
    topic_title = clean_topic[0].upper() + clean_topic[1:] if clean_topic else "This topic"
    return _build_generic_offline_response(clean_topic, topic_title, message_lower)


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
    all_providers = ["openai", "gemini", "deepseek", "ollama", "huggingface"]
    order: List[str] = []

    alias_map = {
        "hf": "huggingface",
        "hugging_face": "huggingface",
        "allama": "ollama",
    }

    def _normalize(name: str) -> str:
        return alias_map.get(name, name)

    if requested:
        normalized_requested = _normalize(requested)
        if normalized_requested == "offline":
            return []
        if normalized_requested != "auto":
            order.append(normalized_requested)
    else:
        env_default = (os.getenv("DEFAULT_MODEL_PROVIDER") or "").strip().lower()
        normalized_default = _normalize(env_default) if env_default else ""
        if normalized_default in all_providers and _provider_available(normalized_default):
            order.append(normalized_default)

    available_first = [provider for provider in all_providers if _provider_available(provider)]
    unavailable_after = [provider for provider in all_providers if provider not in available_first]
    order.extend(available_first)
    order.extend(unavailable_after)

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
    if provider == "huggingface":
        return bool(huggingface_api_key)
    if provider == "ollama":
        # Check reachability dynamically if it was not detected at startup
        # to support cases where Ollama is started after the backend
        global ollama_available
        if not ollama_available:
            ollama_available = _is_ollama_reachable()
        return ollama_available
    return False


def _default_model(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    if provider == "huggingface":
        return os.getenv("HUGGINGFACE_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "llama3.2")
    return ""


async def _invoke_provider(provider: str, model_name: str, system_prompt: str, user_message: str) -> str:
    if provider == "openai":
        assert client is not None
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=500
        )
        return completion.choices[0].message.content or ""

    if provider == "gemini":
        prompt = f"{system_prompt}\n\nUser: {user_message}"
        if gemini_client_type == "new":
            assert gemini_client is not None
            result = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return getattr(result, "text", None) or ""

        if genai_legacy is None:
            raise RuntimeError("Gemini legacy SDK is not available")
        model = genai_legacy.GenerativeModel(model_name)
        result = model.generate_content(prompt)
        return getattr(result, "text", None) or ""

    if provider == "deepseek":
        assert deepseek_client is not None
        completion = deepseek_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=500
        )
        return completion.choices[0].message.content or ""

    if provider == "huggingface":
        if not huggingface_api_key:
            raise RuntimeError("HUGGINGFACE_API_KEY is not configured")
        endpoint = f"https://api-inference.huggingface.co/models/{quote(model_name, safe='/:')}"
        headers = {
            "Authorization": f"Bearer {huggingface_api_key}",
            "Content-Type": "application/json",
        }
        prompt = f"System: {system_prompt}\n\nUser: {user_message}\n\nAssistant:"
        payload = {
            "inputs": prompt,
            "parameters": {"temperature": 0.7, "max_new_tokens": 500, "return_full_text": False},
            "options": {"wait_for_model": True}
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as http:
            response = await http.post(endpoint, headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"Hugging Face HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                return (first.get("generated_text") or "").strip()
        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(str(data.get("error")))
            return (data.get("generated_text") or "").strip()
        return ""

    if provider == "ollama":
        endpoint = f"{ollama_base_url}/api/chat"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "stream": False,
            "options": {"temperature": 0.7}
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as http:
            response = await http.post(endpoint, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if isinstance(data, dict):
            message = data.get("message") or {}
            if isinstance(message, dict):
                return (message.get("content") or "").strip()
        return ""

    raise RuntimeError(f"Unknown provider: {provider}")


async def get_free_api_response(message: str) -> Optional[str]:
    """
    Try free public APIs for short factual answers.
    Priority:
    1) DuckDuckGo Instant Answer API (no key)
    2) Wikipedia REST summary API (no key)
    """
    query = (message or "").strip()
    query = re.sub(r"\bunsuper\s*vised\b", "unsupervised", query, flags=re.IGNORECASE)
    query = re.sub(r"\bsuper\s*vised\b", "supervised", query, flags=re.IGNORECASE)
    query = re.sub(r"\breinforce\s*ment\b", "reinforcement", query, flags=re.IGNORECASE)
    if not query:
        return None
    normalized_query = query.lower().strip(" ?.")
    if normalized_query in {
        "ai",
        "what is ai",
        "define ai",
        "explain ai",
        "artificial intelligence",
        "what is artificial intelligence",
        "dsa",
        "what is dsa",
        "define dsa",
        "explain dsa",
        "data structures and algorithms",
        "what is data structures and algorithms",
    }:
        return None

    timeout = httpx.Timeout(2.0)
    headers = {"User-Agent": "ai-teacher-assistant/1.0"}

    lookup_topic = _extract_learning_topic(query)
    lookup_topic = re.sub(r"\bfor class\s+\d+\b", "", lookup_topic, flags=re.IGNORECASE)
    lookup_topic = re.sub(r"\bclass\s+\d+\b", "", lookup_topic, flags=re.IGNORECASE)
    lookup_topic = re.sub(r"\bfor students\b", "", lookup_topic, flags=re.IGNORECASE)
    lookup_topic = re.sub(r"\bstep by step\b", "", lookup_topic, flags=re.IGNORECASE)
    lookup_topic = re.sub(r"\s+", " ", lookup_topic).strip(" ?.,-")

    # 1) DuckDuckGo Instant Answer API
    try:
        ddg_queries = [query]
        if lookup_topic and lookup_topic.lower() != query.lower():
            ddg_queries.insert(0, lookup_topic)

        async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
            for ddg_query in ddg_queries:
                ddg_url = f"https://api.duckduckgo.com/?q={quote(ddg_query)}&format=json&no_html=1&skip_disambig=1"
                ddg_resp = await http.get(ddg_url)
                if ddg_resp.status_code != 200:
                    continue
                ddg_data = ddg_resp.json()
                abstract = (ddg_data.get("AbstractText") or "").strip()
                if abstract:
                    return abstract

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
        topic = (lookup_topic or query).strip(" ?.")
        if topic:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
                wiki_targets = [topic]
                wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(topic)}"
                wiki_resp = await http.get(wiki_url)
                if wiki_resp.status_code == 200:
                    wiki_data = wiki_resp.json()
                    extract = (wiki_data.get("extract") or "").strip()
                    if extract:
                        return extract

                search_url = "https://en.wikipedia.org/w/api.php"
                search_params = {
                    "action": "query",
                    "list": "search",
                    "srsearch": topic,
                    "format": "json",
                    "utf8": 1,
                    "srlimit": 1,
                }
                search_resp = await http.get(search_url, params=search_params)
                if search_resp.status_code == 200:
                    search_data = search_resp.json()
                    search_hits = (((search_data.get("query") or {}).get("search")) or [])
                    if search_hits and isinstance(search_hits[0], dict):
                        title = (search_hits[0].get("title") or "").strip()
                        if title and title not in wiki_targets:
                            wiki_targets.append(title)

                for target in wiki_targets[1:]:
                    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(target)}"
                    summary_resp = await http.get(summary_url)
                    if summary_resp.status_code != 200:
                        continue
                    summary_data = summary_resp.json()
                    extract = (summary_data.get("extract") or "").strip()
                    if extract:
                        return extract
    except Exception:
        pass

    return None


OFFLINE_TOPIC_ALIASES: Dict[str, List[str]] = {
    "photosynthesis": ["photosynthesis", "chlorophyll", "plant food"],
    "quantum": ["quantum", "atom", "electron", "uncertainty"],
    "heart": ["heart", "circulation", "blood flow", "cardiac"],
    "dna": ["dna", "gene", "genes", "double helix", "genetics"],
    "newton": ["newton", "force", "motion", "laws of motion", "gravity"],
    "python": ["python", "programming", "loops", "functions", "coding"],
}


OFFLINE_NOTES: Dict[str, List[str]] = {
    "photosynthesis": [
        "Process: Light energy becomes chemical energy.",
        "Equation: 6CO2 + 6H2O -> C6H12O6 + 6O2.",
        "Location: Chloroplasts contain chlorophyll.",
        "Main stages: Light reaction and Calvin cycle.",
        "Importance: Makes food and releases oxygen.",
    ],
    "quantum": [
        "Matter can behave like both particles and waves.",
        "Superposition means multiple possible states can exist together.",
        "Entanglement links particles in surprising ways.",
        "The uncertainty principle limits exact measurement.",
        "Applications include lasers, MRI, and quantum computing.",
    ],
    "heart": [
        "The heart has four chambers.",
        "Blood moves body -> right heart -> lungs -> left heart -> body.",
        "Valves keep blood flowing in one direction.",
        "The SA node helps control heartbeat rhythm.",
        "The heart works like a strong muscular pump.",
    ],
    "dna": [
        "DNA stores genetic instructions.",
        "Its shape is a double helix.",
        "Base pairs are A-T and G-C.",
        "Genes are sections of DNA.",
        "DNA passes traits from parents to children.",
    ],
    "newton": [
        "First law explains inertia.",
        "Second law is F = ma.",
        "Third law says every action has an equal and opposite reaction.",
        "These laws explain motion in sports, vehicles, and rockets.",
        "Force changes how objects move.",
    ],
    "python": [
        "Python uses clean, readable syntax.",
        "It is beginner-friendly and widely used.",
        "Variables, loops, and functions are core basics.",
        "Python is common in web, data, and automation work.",
        "Indentation is important in Python code.",
    ],
}


def _extract_learning_topic(message: str) -> str:
    cleaned_message = re.sub(r"\s+", " ", (message or "").strip(" ?."))
    patterns = [
        r"^(?:what is|what are|define|explain|teach me|tell me about)\s+(.+)$",
        r"^(?:how does|how do|how can)\s+(.+)$",
        r"^(?:solve|calculate|find)\s+(.+)$",
    ]
    lowered = cleaned_message.lower()
    for pattern in patterns:
        match = re.match(pattern, lowered)
        if match:
            extracted = match.group(1).strip()
            if extracted:
                return extracted
    return cleaned_message or "this topic"


def _detect_offline_topic(message: str) -> Optional[str]:
    lowered = (message or "").lower()
    for topic, aliases in OFFLINE_TOPIC_ALIASES.items():
        if topic in lowered or any(alias in lowered for alias in aliases):
            return topic
    return None


def _format_offline_notes(topic: Optional[str]) -> str:
    if not topic:
        return ""
    notes = OFFLINE_NOTES.get(topic) or []
    if not notes:
        return ""
    lines = "\n".join(f"- {note}" for note in notes[:5])
    return f"Quick Notes:\n{lines}"


def _format_generic_notes(message: str) -> str:
    topic = _extract_learning_topic(message)
    return (
        "Quick Notes:\n"
        f"- Topic: {topic.capitalize()}.\n"
        "- Meaning: Start with the main definition in simple words.\n"
        "- Key points: Identify the rules, parts, formula, or process.\n"
        "- Real life use: Connect it to one practical example.\n"
        "- Revision: End with one short self-check question."
    )


def _format_offline_diagram(topic: Optional[str]) -> str:
    if not topic:
        return ""
    spec = DIAGRAM_SPECS.get(topic)
    if not spec:
        return ""
    labels = spec.get("chart", {}).get("labels") or []
    label_flow = " -> ".join(labels[:5]) if labels else "Key steps"
    explanation = spec.get("explanation") or "Diagram overview unavailable."
    return (
        "Simple Diagram:\n"
        f"{label_flow}\n"
        f"Diagram idea: {explanation}"
    )


def _format_generic_diagram(message: str) -> str:
    topic = _extract_learning_topic(message)
    return (
        "Simple Diagram:\n"
        f"Definition -> Key Parts -> Example -> Practice\n"
        f"Diagram idea: Show {topic} as a simple flow from meaning to use."
    )


def _build_video_query(message: str, topic: Optional[str]) -> str:
    extracted_topic = _extract_learning_topic(message)
    if topic and topic not in extracted_topic.lower():
        base_query = f"{extracted_topic} {topic}"
    else:
        base_query = extracted_topic
    return f"{base_query} explained for students"


async def _format_offline_videos(message: str, topic: Optional[str]) -> str:
    query = _build_video_query(message, topic)
    videos = await _search_invidious(query, limit=3)
    if videos:
        lines = []
        for index, video in enumerate(videos[:3], start=1):
            title = video.get("title") or "Untitled"
            channel = video.get("channel") or "Unknown Channel"
            url = video.get("url") or ""
            lines.append(f"{index}. {title} - {channel} - {url}")
        return "Best YouTube Videos:\n" + "\n".join(lines)

    fallback_url = f"https://www.youtube.com/results?search_query={quote(query + ' tutorial')}"
    return (
        "Best YouTube Videos:\n"
        f"No live ranked results right now. Open this free search: {fallback_url}"
    )


async def _build_offline_learning_pack(message: str) -> str:
    topic = _detect_offline_topic(message)
    sections = [
        _format_offline_notes(topic) or _format_generic_notes(message),
        _format_offline_diagram(topic) or _format_generic_diagram(message),
        await _format_offline_videos(message, topic),
    ]
    sections = [section for section in sections if section]
    return "\n\n".join(sections)


def _provider_reply_usable(reply: str) -> bool:
    cleaned = (reply or "").strip()
    if len(cleaned) < 20:
        return False
    lowered = cleaned.lower()
    failure_markers = [
        "i can't answer",
        "i cannot answer",
        "i do not know",
        "i'm not sure",
        "i am not sure",
        "unable to answer",
        "no information available",
    ]
    return not any(marker in lowered for marker in failure_markers)


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


def _query_relevance_score(query: str, title: str, channel: str) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    combined_tokens = set(_tokenize(f"{title} {channel}"))
    overlap = len(query_tokens.intersection(combined_tokens))
    return float(overlap) * 1.5


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
                        "score": _rank_video(title, channel, seconds, views) + _query_relevance_score(query, title, channel),
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
    
    requested_provider = (request.model_provider or "").strip().lower()
    if requested_provider == "offline":
        free_api_reply = await get_free_api_response(request.message)
        ai_reply = free_api_reply or await get_ai_response(request.message, request.language)
        if context_text:
            ai_reply = f"{ai_reply}\n\nFrom your notes:\n{context_text[:1200]}"
        return ChatResponse(
            reply=ai_reply,
            language=request.language,
            explanation="",
            suggestions=[
                "Switch to Auto to try configured AI providers",
                "Ask about math or science",
                "Open the Notes tab for notes",
                "Open the Videos tab for YouTube results",
                "Upload notes for context"
            ],
            student_id=request.student_id
        )

    provider_candidates = _provider_order(requested_provider)
    configured_providers = [p for p in provider_candidates if _provider_available(p)]

    if not configured_providers:
        free_api_reply = await get_free_api_response(request.message)
        ai_reply = free_api_reply or await get_ai_response(request.message, request.language)
        if context_text:
            ai_reply = f"{ai_reply}\n\nFrom your notes:\n{context_text[:1200]}"
        return ChatResponse(
            reply=ai_reply,
            language=request.language,
            explanation="",
            suggestions=[
                "Ask another question on the same topic",
                "Open the Notes tab for notes",
                "Open the Videos tab for YouTube results",
                "Upload notes for context"
            ],
            student_id=request.student_id
        )

    try:
        system_prompts = {
            "english": "You are a helpful AI teacher for Indian students. Always provide answers in a pointwise format. Do not use blocks of text. There MUST be an empty line (blank line space) between every single point or bullet point. Structure your answer with: 1) A clear Definition, 2) Key Points using bullet points, and 3) Real-world Examples.",
            "hindi": "आप भारतीय छात्रों के लिए एक सहायक AI शिक्षक हैं। हमेशा बिंदुवार (pointwise) प्रारूप में उत्तर प्रदान करें। पाठ के बड़े ब्लॉक का उपयोग न करें। हर बिंदु या बुलेट पॉइंट के बीच एक खाली लाइन का स्थान (empty line space) होना अनिवार्य है। अपने उत्तर को इस प्रकार व्यवस्थित करें: 1) एक स्पष्ट परिभाषा, 2) बुलेट पॉइंट्स का उपयोग करते हुए मुख्य बिंदु, और 3) वास्तविक दुनिया के उदाहरण।",
            "tamil": "நீங்கள் இந்திய மாணவர்களுக்கான உதவிகரமான AI ஆசிரியர். எப்போதும் புள்ளிவாரியாக (pointwise) பதில்களை வழங்கவும். உரையின் பெரிய தொகுதிகளைப் பயன்படுத்த வேண்டாம். ஒவ்வொரு புள்ளி அல்லது புல்லட் புள்ளிக்கும் இடையே ஒரு வெற்று வரி இடைவெளி (empty line space) இருக்க வேண்டும். உங்கள் பதிலை இவ்வாறு கட்டமைக்கவும்: 1) ஒரு தெளிவான வரையறை, 2) புல்லட் புள்ளிகளைப் பயன்படுத்தும் முக்கிய குறிப்புகள், மற்றும் 3) நிஜ உலக உதாரணங்கள்.",
            "telugu": "మీరు భారతీయ విద్యార్థుల కోసం సహాయక AI ఉపాధ్యాయులు. ఎల్లప్పుడూ పాయింట్ల వారీగా (pointwise) సమాధానాలను అందించండి. టెక్స్ట్ యొక్క పెద్ద బ్లాక్‌లను ఉపయోగించవద్దు. ప్రతి పాయింట్ లేదా బుల్లెట్ పాయింట్ మధ్య ఖచ్చితంగా ఒక ఖాళీ లైన్ స్థలం (empty line space) ఉండాలి. మీ సమాధానాన్ని ఇలా రూపొందించండి: 1) స్పష్టమైన నిర్వచనం, 2) బుల్లెట్ పాయింట్లను ఉపయోగించే ముఖ్యమైన అంశాలు, మరియు 3) నిజ-ప్రపంచ ఉదాహరణలు.",
            "marathi": "तुम्ही भारतीय विद्यार्थ्यांसाठी मदत करणारे AI शिक्षक आहात. नेहमी मुद्द्यांच्या स्वरूपात (pointwise) उत्तरे द्या. मजकुराचे मोठे ब्लॉक वापरू नका. प्रत्येक मुद्दा किंवा बुलेट पॉईंटच्या दरम्यान एक रिकामी ओळ (empty line space) असणे अनिवार्य आहे. तुमचे उत्तर खालीलप्रमाणे व्यवस्थित करा: १) स्पष्ट व्याख्या, २) बुलेट पॉइंट्स वापरून मुख्य मुद्दे, आणि ३) वास्तविक जगातील उदाहरणे.",
            "gujarati": "તમે ભારતીય વિદ્યાર્થીઓ માટે સહાયક AI શિક્ષક છો. હંમેશા મુદ્દાસર (pointwise) જવાબો આપો. લખાણના મોટા બ્લોક્સનો ઉપયોગ કરશો નહીં. દરેક મુદ્દા કે બુલેટ પોઇન્ટની વચ્ચે એક ખાલી લાઇન (empty line space) હોવી અનિવાર્ય છે. તમારા જવાબને આ રીતે ગોઠવો: ૧) સ્પષ્ટ વ્યાખ્યા, ૨) બુલેટ પોઇન્ટ્સનો ઉપયોગ કરીને મુખ્ય મુદ્દાઓ, અને ૩) વાસ્તવિક દુનિયાના ઉદાહરણો.",
            "bengali": "আপনি ভারতীয় ছাত্রদের জন্য একজন সহায়ক AI শিক্ষক। সর্বদা পয়েন্ট আকারে (pointwise) উত্তর প্রদান করুন। টেক্সটের বড় ব্লক ব্যবহার করবেন না। প্রতিটি পয়েন্ট বা বুলেট পয়েন্টের মধ্যে অবশ্যই একটি খালি লাইন (empty line space) থাকতে হবে। আপনার উত্তরটি এইভাবে সাজান: ১) একটি স্পষ্ট সংজ্ঞা, ২) বুলেট পয়েন্ট ব্যবহার করে মূল পয়েন্টগুলি, এবং ৩) বাস্তব জগতের উদাহরণ।"
        }

        system_prompt = system_prompts.get(request.language, system_prompts["english"])
        if request.step_by_step:
            system_prompt += " " + _step_by_step_prompt(request.language)
        if context_text:
            system_prompt += "\n\nUse the following student notes as trusted context:\n" + context_text[:2000]
        if request.subject:
            system_prompt += f" Focus on {request.subject} subject."

        provider = "offline"
        ai_reply = ""
        provider_errors: List[str] = []
        for candidate in configured_providers:
            model_name = request.model_name or _default_model(candidate)
            try:
                ai_reply = await _invoke_provider(candidate, model_name, system_prompt, request.message)
                if _provider_reply_usable(ai_reply):
                    provider = candidate
                    break
                provider_errors.append(f"{candidate}: empty or low-confidence response")
            except Exception as provider_error:
                provider_error_text = str(provider_error)
                provider_errors.append(f"{candidate}: {provider_error_text}")
                _log_ai_error(f"{candidate} API error: {provider_error_text}")
                if candidate == "openai":
                    if "insufficient_quota" in provider_error_text or "429" in provider_error_text:
                        openai_disabled_reason = "quota exceeded"
                    elif "invalid_api_key" in provider_error_text or "401" in provider_error_text:
                        openai_disabled_reason = "invalid API key"

        if not ai_reply:
            # Final attempt: Ollama fallback if not already tried in the loop
            # This ensures that even if it wasn't in configured_providers initially,
            # we try it before falling back to rule-based offline mode.
            if "ollama" not in configured_providers:
                try:
                    if _provider_available("ollama"):
                        model_name = request.model_name or _default_model("ollama")
                        ai_reply = await _invoke_provider("ollama", model_name, system_prompt, request.message)
                        if _provider_reply_usable(ai_reply):
                            provider = "ollama"
                except Exception as ollama_err:
                    _log_ai_error(f"Ollama fallback attempt failed: {ollama_err}")

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
            explanation="",
            suggestions=suggestions,
            student_id=request.student_id
        )

    except Exception as e:
        # Fallback to rule-based response if provider fails
        error_text = str(e)
        _log_ai_error(f"chat fallback error: {error_text}")
        free_api_reply = await get_free_api_response(request.message)
        ai_reply = free_api_reply or await get_ai_response(request.message, request.language)
        if context_text:
            ai_reply = f"{ai_reply}\n\nFrom your notes:\n{context_text[:1200]}"
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
            explanation="",
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

def _normalize_class_grade(value: str) -> str:
    normalized = (value or "").strip().lower()
    normalized = normalized.replace('class ', '').replace('st year', 'th').replace('nd year', 'th').replace('rd year', 'th').replace('th year', 'th')
    normalized = normalized.replace(' ', '')
    if normalized.endswith('th'):
        return normalized
    if normalized.isdigit():
        return f"{normalized}th"
    return normalized

@app.get("/assignments/{class_grade}")
async def get_assignments(class_grade: str, subject: Optional[str] = None):
    """Get assignments for a specific class"""
    normalized_class = _normalize_class_grade(class_grade)
    assignments = [a for a in SAMPLE_ASSIGNMENTS if _normalize_class_grade(a["class_grade"]) == normalized_class]
    
    if subject:
        assignments = [a for a in assignments if a["subject"] == subject.lower()]
    
    return {
        "class": class_grade,
        "total_assignments": len(assignments),
        "assignments": assignments
    }




class DiagramGenerateRequest(BaseModel):
    topic: str
    model_provider: Optional[str] = "auto"
    model_name: Optional[str] = None


@app.post("/generate-diagram")
async def generate_scientific_diagram(request: DiagramGenerateRequest):
    """
    Generate custom interactive scientific SVG diagram + coordinates using AI,
    or fallback to prebuilt local key mappings for instant offline reliability.
    """
    topic = request.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    lower_topic = topic.lower()
    
    # Offline Local Fallback Mapping
    fallback_key = None
    if "photosynthesis" in lower_topic:
        fallback_key = "photosynthesis"
    elif "quantum" in lower_topic:
        fallback_key = "quantum"
    elif "heart" in lower_topic:
        fallback_key = "heart"
    elif "dna" in lower_topic or "helix" in lower_topic:
        fallback_key = "dna"
    elif "newton" in lower_topic or "second law" in lower_topic:
        fallback_key = "newton"
    elif "atom" in lower_topic or "electron" in lower_topic or "orbital" in lower_topic or "nucleus" in lower_topic:
        fallback_key = "atom"
    elif "water" in lower_topic or "rain" in lower_topic or "cycle" in lower_topic or "evaporat" in lower_topic:
        fallback_key = "water"
    elif "cell" in lower_topic or "organelle" in lower_topic:
        fallback_key = "cell"
    elif "volcano" in lower_topic or "magma" in lower_topic or "lava" in lower_topic:
        fallback_key = "volcano"
    elif "mitosis" in lower_topic or "division" in lower_topic or "split" in lower_topic:
        fallback_key = "mitosis"

    system_prompt = (
        "You are an expert scientific diagram designer.\n"
        "Your task is to generate a beautifully structured, modern, and detailed scientific vector diagram in SVG format based on the user's topic.\n"
        "Return the output strictly as a valid JSON object. Do NOT wrap the JSON in markdown code blocks like ```json ... ```. Output ONLY the raw JSON string.\n"
        "The JSON MUST have the following structure:\n"
        "{\n"
        "  \"title\": \"Capitalized Topic Name\",\n"
        "  \"desc\": \"Detailed scientific explanation. It MUST be in pointwise format with exactly one empty line (blank space) between every single point or bullet point.\",\n"
        "  \"svg\": \"A beautiful, valid, fully-formed SVG string (wrapped in <svg viewBox=\\\"0 0 400 320\\\" xmlns=\\\"http://www.w3.org/2000/svg\\\" id=\\\"dynamicSvg\\\">). Use modern space-themed colors (e.g. background circle fill rgba(99,102,241,0.03) with filter blur, glassmorphic strokes, and glowing highlights). Draw detailed shapes, paths, lines, and layers instead of a simple square or circle.\",\n"
        "  \"coords\": [\n"
        "    {\"name\": \"1. Part Name\", \"cx\": integer_x, \"cy\": integer_y, \"r\": 8, \"info\": \"Details about what this part does\"},\n"
        "    {\"name\": \"2. Part Name\", \"cx\": integer_x, \"cy\": integer_y, \"r\": 8, \"info\": \"Details about what this part does\"},\n"
        "    {\"name\": \"3. Part Name\", \"cx\": integer_x, \"cy\": integer_y, \"r\": 8, \"info\": \"Details about what this part does\"},\n"
        "    {\"name\": \"4. Part Name\", \"cx\": integer_x, \"cy\": integer_y, \"r\": 8, \"info\": \"Details about what this part does\"}\n"
        "  ]\n"
        "}\n\n"
        "Make sure the coordinates (cx, cy) correspond exactly to the coordinate elements drawn inside your generated SVG string so they display in correct layers!"
    )

    requested_provider = (request.model_provider or "").strip().lower()
    provider_candidates = _provider_order(requested_provider)
    configured_providers = [p for p in provider_candidates if _provider_available(p)]

    ai_reply = ""
    if configured_providers:
        for candidate in configured_providers:
            model_name = request.model_name or _default_model(candidate)
            try:
                # Custom direct client call to ensure high token length generation (2000 tokens)
                if candidate == "openai" and client is not None:
                    completion = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Generate a diagram for the topic: {topic}"}
                        ],
                        temperature=0.7,
                        max_tokens=2000
                    )
                    ai_reply = completion.choices[0].message.content or ""
                    
                elif candidate == "gemini" and gemini_client is not None:
                    prompt = f"{system_prompt}\n\nUser: Generate a diagram for the topic: {topic}"
                    if gemini_client_type == "new":
                        result = gemini_client.models.generate_content(
                            model=model_name,
                            contents=prompt
                        )
                        ai_reply = getattr(result, "text", None) or ""
                    else:
                        model = gemini_client.GenerativeModel(model_name)
                        result = model.generate_content(prompt)
                        ai_reply = getattr(result, "text", None) or ""
                        
                elif candidate == "deepseek" and deepseek_client is not None:
                    completion = deepseek_client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Generate a diagram for the topic: {topic}"}
                        ],
                        temperature=0.7,
                        max_tokens=2000
                    )
                    ai_reply = completion.choices[0].message.content or ""
                    
                elif candidate == "ollama":
                    endpoint = f"{ollama_base_url}/api/chat"
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Generate a diagram for the topic: {topic}"}
                        ],
                        "stream": False,
                        "options": {"temperature": 0.7, "num_predict": 2048}
                    }
                    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as http:
                        response = await http.post(endpoint, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        message = data.get("message") or {}
                        ai_reply = (message.get("content") or "").strip()
                
                if ai_reply:
                    break
            except Exception as e:
                _log_ai_error(f"Generate diagram provider error ({candidate}): {e}")

    # Clean and parse response if generated by AI
    if ai_reply:
        try:
            clean_reply = ai_reply.strip()
            if clean_reply.startswith("```json"):
                clean_reply = clean_reply[7:]
            if clean_reply.startswith("```"):
                clean_reply = clean_reply[3:]
            if clean_reply.endswith("```"):
                clean_reply = clean_reply[:-3]
            clean_reply = clean_reply.strip()
            
            parsed_data = json.loads(clean_reply)
            # Verify parsed data has required keys
            if "title" in parsed_data and "svg" in parsed_data:
                return parsed_data
        except Exception as parse_err:
            _log_ai_error(f"Failed to parse generated scientific diagram JSON: {parse_err}")

    # Fallback to local keys if AI is unavailable, failed, or offline
    if fallback_key:
        return {
            "fallback_key": fallback_key,
            "title": topic.title(),
            "desc": f"Displaying prebuilt local model for {topic.title()}.",
            "svg": "",
            "coords": []
        }

    # Absolute fallback: generic diagram structure
    return {
        "title": topic.title(),
        "desc": f"Definition of {topic}.\n\nCore process stages.\n\nAllied scientific components.\n\nReal-world applications.",
        "svg": f"""
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg" id="dynamicSvg">
          <circle cx="200" cy="160" r="100" fill="rgba(99, 102, 241, 0.03)" filter="blur(25px)"></circle>
          <line x1="200" y1="160" x2="200" y2="70" stroke="rgba(255,255,255,0.2)" stroke-width="2" stroke-dasharray="4,4"></line>
          <line x1="200" y1="160" x2="200" y2="250" stroke="rgba(255,255,255,0.2)" stroke-width="2" stroke-dasharray="4,4"></line>
          <line x1="200" y1="160" x2="90" y2="160" stroke="rgba(255,255,255,0.2)" stroke-width="2" stroke-dasharray="4,4"></line>
          <line x1="200" y1="160" x2="310" y2="160" stroke="rgba(255,255,255,0.2)" stroke-width="2" stroke-dasharray="4,4"></line>
          <circle cx="200" cy="160" r="32" fill="rgba(217, 70, 239, 0.15)" stroke="var(--accent)" stroke-width="3" filter="drop-shadow(0 0 12px var(--accent-glow))"></circle>
          <text x="200" y="163" fill="#fff" font-size="8" font-weight="900" text-anchor="middle">{topic.upper()[:10]}</text>
        </svg>
        """,
        "coords": [
            {"name": "1. Definition", "cx": 200, "cy": 70, "r": 8, "info": f"Core identity and scientific explanation for {topic}."},
            {"name": "2. Process Mechanics", "cx": 310, "cy": 160, "r": 8, "info": f"Mechanical stages and action details for {topic}."},
            {"name": "3. Allied Concepts", "cx": 200, "cy": 250, "r": 8, "info": f"Governing physical laws or related topics matching {topic}."},
            {"name": "4. Real-world Uses", "cx": 90, "cy": 160, "r": 8, "info": f"Practical applications or natural systems demonstrating {topic}."}
        ]
    }


class NotesGenerateRequest(BaseModel):
    topic: str
    model_provider: Optional[str] = "auto"
    model_name: Optional[str] = None


@app.post("/generate-notes")
async def generate_notes_and_flashcards(request: NotesGenerateRequest):
    """
    Generate custom study notes and interactive flashcards using AI,
    or fallback to prebuilt local templates matching standard keys.
    """
    topic = request.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    lower_topic = topic.lower()
    
    # Offline Local Fallback Mapping
    fallback_key = None
    if "photosynthesis" in lower_topic:
        fallback_key = "photosynthesis"
    elif "quantum" in lower_topic:
        fallback_key = "quantum"
    elif "heart" in lower_topic:
        fallback_key = "heart"
    elif "dna" in lower_topic or "helix" in lower_topic:
        fallback_key = "dna"

    system_prompt = (
        "You are an expert educational content creator.\n"
        "Your task is to generate highly informative, beautifully structured study notes and matching flashcards based on the user's topic.\n"
        "Return the output strictly as a valid JSON object. Do NOT wrap the JSON in markdown code blocks like ```json ... ```. Output ONLY the raw JSON string.\n"
        "The JSON MUST have the following structure:\n"
        "{\n"
        "  \"title\": \"Capitalized Topic Name Notes\",\n"
        "  \"content\": \"<h3>🌿 Subtitle matching the topic</h3><p>Detailed explanation...</p><b>Key Concepts:</b><ul><li><b>Term 1:</b> Explanation...</li><li><b>Term 2:</b> Explanation...</li></ul>\",\n"
        "  \"flashcards\": [\n"
        "    {\"front\": \"Question 1?\", \"back\": \"Clear, concise answer matching the question.\"},\n"
        "    {\"front\": \"Question 2?\", \"back\": \"Clear, concise answer matching the question.\"},\n"
        "    {\"front\": \"Question 3?\", \"back\": \"Clear, concise answer matching the question.\"},\n"
        "    {\"front\": \"Question 4?\", \"back\": \"Clear, concise answer matching the question.\"}\n"
        "  ]\n"
        "}\n"
    )

    requested_provider = (request.model_provider or "").strip().lower()
    provider_candidates = _provider_order(requested_provider)
    configured_providers = [p for p in provider_candidates if _provider_available(p)]

    ai_reply = ""
    if configured_providers:
        for candidate in configured_providers:
            model_name = request.model_name or _default_model(candidate)
            try:
                if candidate == "openai" and client is not None:
                    completion = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Generate study notes and flashcards for the topic: {topic}"}
                        ],
                        temperature=0.7,
                        max_tokens=2000
                    )
                    ai_reply = completion.choices[0].message.content or ""
                    
                elif candidate == "gemini" and gemini_client is not None:
                    prompt = f"{system_prompt}\n\nUser: Generate study notes and flashcards for the topic: {topic}"
                    if gemini_client_type == "new":
                        result = gemini_client.models.generate_content(
                            model=model_name,
                            contents=prompt
                        )
                        ai_reply = getattr(result, "text", None) or ""
                    else:
                        model = gemini_client.GenerativeModel(model_name)
                        result = model.generate_content(prompt)
                        ai_reply = getattr(result, "text", None) or ""
                        
                elif candidate == "deepseek" and deepseek_client is not None:
                    completion = deepseek_client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Generate study notes and flashcards for the topic: {topic}"}
                        ],
                        temperature=0.7,
                        max_tokens=2000
                    )
                    ai_reply = completion.choices[0].message.content or ""
                    
                elif candidate == "ollama":
                    endpoint = f"{ollama_base_url}/api/chat"
                    payload = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Generate study notes and flashcards for the topic: {topic}"}
                        ],
                        "stream": False,
                        "options": {"temperature": 0.7, "num_predict": 2048}
                    }
                    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as http:
                        response = await http.post(endpoint, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        message = data.get("message") or {}
                        ai_reply = (message.get("content") or "").strip()
                
                if ai_reply:
                    break
            except Exception as e:
                _log_ai_error(f"Generate notes provider error ({candidate}): {e}")

    # Clean and parse response if generated by AI
    if ai_reply:
        try:
            clean_reply = ai_reply.strip()
            if clean_reply.startswith("```json"):
                clean_reply = clean_reply[7:]
            if clean_reply.startswith("```"):
                clean_reply = clean_reply[3:]
            if clean_reply.endswith("```"):
                clean_reply = clean_reply[:-3]
            clean_reply = clean_reply.strip()
            
            parsed_data = json.loads(clean_reply)
            if "title" in parsed_data and "content" in parsed_data and "flashcards" in parsed_data:
                return parsed_data
        except Exception as parse_err:
            _log_ai_error(f"Failed to parse generated notes JSON: {parse_err}")

    # Fallback to local keys if AI is unavailable, failed, or offline
    if fallback_key:
        return {
            "fallback_key": fallback_key,
            "title": topic.title() + " Notes",
            "content": "",
            "flashcards": []
        }

    # Absolute fallback: generic structured notes and flashcards
    return {
        "title": topic.title() + " Notes",
        "content": f"""
        <h3>📖 Overview of {topic.title()}</h3>
        <p>{topic.title()} is a key topic in science and educational curricula.</p>
        <p><b>Core Concepts:</b></p>
        <ul>
          <li><b>Concept Definition:</b> Fundamental definition and framework for {topic}.</li>
          <li><b>Important Mechanics:</b> The primary mechanics, processes, or laws that govern {topic}.</li>
          <li><b>Real-world Context:</b> Practical applications and daily life examples of {topic}.</li>
        </ul>
        """,
        "flashcards": [
            {"front": f"What is the definition of {topic}?", "back": f"A primary concept describing {topic} in an educational framework."},
            {"front": f"What are the main sub-components of {topic}?", "back": "The core concepts, mechanics, and practical applications."},
            {"front": f"Why is studying {topic} important?", "back": "It helps us analyze the natural laws and scientific frameworks governing the universe."}
        ]
    }


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


def _parse_ai_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    clean_text = raw.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    if clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    try:
        return json.loads(clean_text)
    except Exception:
        return None


def _sanitize_quiz_questions(parsed: dict, subject: str) -> List[dict]:
    questions = []
    for index, item in enumerate(parsed.get("questions", []), start=1):
        if not isinstance(item, dict):
            continue
        question_text = str(item.get("question", "")).strip()
        options = item.get("options") or []
        if not isinstance(options, list):
            continue
        options = [str(opt).strip() for opt in options if str(opt).strip()]
        if not question_text or len(options) < 2:
            continue
        correct_answer = item.get("correct_answer", 0)
        if isinstance(correct_answer, str) and correct_answer.isdigit():
            correct_answer = int(correct_answer)
        if not isinstance(correct_answer, int) or correct_answer < 0 or correct_answer >= len(options):
            correct_answer = 0
        questions.append({
            "question_id": int(item.get("question_id", index)) if str(item.get("question_id", index)).isdigit() else index,
            "question": question_text,
            "options": options,
            "correct_answer": correct_answer,
            "hint": str(item.get("hint", "Think about the key concept behind this question.")).strip(),
            "explanation": str(item.get("explanation", "")).strip()
        })
    return questions


@app.get("/quiz/ai/{subject}")
async def get_ai_quiz(subject: str):
    """Generate AI-based quiz questions for a subject"""
    topic = subject.strip() or "General Knowledge"
    subject_lower = topic.lower()

    provider_candidates = _provider_order(None)
    configured_providers = [p for p in provider_candidates if _provider_available(p)]

    ai_reply = ""
    if configured_providers:
        system_prompt = (
            "You are an expert educational content creator. Generate a short AI-powered multiple-choice quiz for school students. "
            "Output only valid JSON and do not wrap the response in markdown code fences. "
            "The JSON MUST have keys: subject, total_questions, questions. "
            "Each question must include: question_id, question, options, correct_answer, hint, explanation. "
            "Use simple language and keep every option plausible. "
        )
        user_message = (
            f"Generate 4 multiple-choice questions for the topic: {topic}. "
            "Provide exactly 4 answer options per question. "
            "Make the correct_answer a zero-based index. "
            "Return the answer strictly as JSON."
        )

        for candidate in configured_providers:
            model_name = _default_model(candidate)
            try:
                ai_reply = await _invoke_provider(candidate, model_name, system_prompt, user_message)
                parsed = _parse_ai_json(ai_reply)
                if parsed and isinstance(parsed, dict) and parsed.get("questions"):
                    questions = _sanitize_quiz_questions(parsed, topic)
                    if questions:
                        return {
                            "subject": topic,
                            "total_questions": len(questions),
                            "questions": questions,
                            "source": "ai"
                        }
            except Exception as e:
                _log_ai_error(f"AI quiz generation failed for {candidate}: {e}")

    if subject_lower in SAMPLE_QUIZZES:
        return {
            "subject": topic,
            "total_questions": len(SAMPLE_QUIZZES[subject_lower]),
            "questions": SAMPLE_QUIZZES[subject_lower],
            "source": "fallback"
        }

    raise HTTPException(status_code=503, detail="AI quiz generation is currently unavailable")


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
    print("📝 API Documentation: http://localhost:8000/docs")
    print("🔍 Health Check: http://localhost:8000/health")
    print("💬 Chat Endpoint: http://localhost:8000/chat")
    print("🎤 Voice Features: http://localhost:8000/voice-to-text")
    print("📚 Assignments: http://localhost:8000/assignments/10th")
    print("📝 Quiz: http://localhost:8000/quiz/math")
    print("\n" + "="*50)
    print(f"OpenAI Status: {'✅ Configured' if client else '❌ Not Configured'}")
    if not client:
        print("💡 Tip: Create a .env file with OPENAI_API_KEY=your_key_here")
    print("="*50 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
