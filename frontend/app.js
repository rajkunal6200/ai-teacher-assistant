// ===== STATE =====
let student = null;
try {
  student = JSON.parse(localStorage.getItem('atis_student') || 'null');
  if (student && typeof student !== 'object') {
    student = null;
    localStorage.removeItem('atis_student');
  }
} catch (e) {
  localStorage.removeItem('atis_student');
  student = null;
}

let chatCount = parseInt(localStorage.getItem('atis_chats') || '0');
let selectedModel = localStorage.getItem('atis_model') || 'auto';
let selectedModelName = localStorage.getItem('atis_modelName') || '';
let streakCount = parseInt(localStorage.getItem('atis_streak') || '1');
let lastActiveDate = localStorage.getItem('atis_lastActive') || '';

// Constants & API
const API_BASE = (() => {
  const saved = (localStorage.getItem('apiBaseUrl') || '').trim().replace(/\/$/, '');
  if (saved) return saved;
  if (location.protocol === 'file:')
    return 'http://' + (location.hostname || '127.0.0.1') + ':8000';
  return location.origin;
})();

// Active Study Tools State
let timerInterval = null;
let timerTimeLeft = 25 * 60;
let timerTotal = 25 * 60;
let isTimerRunning = false;

// Assignment search cache
let assignmentItems = [];

// Active Flashcard Deck State
let currentFlashcardIndex = 0;
let flashcardsDeck = [];

// Active Quiz State
let activeQuizQuestions = [];
let currentQuizIndex = 0;
let quizScore = 0;
let selectedOptionIndex = null;
let activeQuizSubject = "";
let hasSubmittedAnswer = false;

// Simulation variables (Newton Second Law)
let simMass = 5; // kg
let simForce = 20; // N
let simPos = 50; // SVG coordinate
let simSpeed = 0;
let simInterval = null;

// ===== INIT =====
window.addEventListener('DOMContentLoaded', () => {
  initStreak();
  initNotepad();
  
  if (student) {
    enterApp();
  } else {
    showScreen('welcome');
  }
});

// ===== STREAK SYSTEM =====
function initStreak() {
  const today = new Date().toDateString();
  if (lastActiveDate) {
    const lastDate = new Date(lastActiveDate);
    const timeDiff = new Date().getTime() - lastDate.getTime();
    const dayDiff = Math.floor(timeDiff / (1000 * 3600 * 24));
    
    if (dayDiff === 1) {
      streakCount++;
      localStorage.setItem('atis_streak', streakCount);
    } else if (dayDiff > 1) {
      streakCount = 1;
      localStorage.setItem('atis_streak', streakCount);
    }
  }
  localStorage.setItem('atis_lastActive', today);
  document.getElementById('kpiStreak').textContent = streakCount;
}

// ===== SCREEN MANAGEMENT =====
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.add('hidden'));
  const target = document.getElementById(id);
  if (target) target.classList.remove('hidden');
}

// ===== CURRICULUM SUGGESTIONS =====
async function updateClassSuggestions() {
  const cls = document.getElementById('regClass').value;
  if (!cls) return;
  
  try {
    const res = await fetch(API_BASE + `/subject-suggestions/${encodeURIComponent(cls)}`);
    if (!res.ok) return;
    const data = await res.json();
    
    const container = document.getElementById('dashboardSuggestions');
    if (!container) return;
    container.innerHTML = '';
    
    data.recommended_subjects.slice(0, 3).forEach(rec => {
      const div = document.createElement('div');
      div.className = 'rec-item';
      div.innerHTML = `
        <div class="rec-info">
          <span class="rec-tag">${rec.category}</span>
          <h4 class="rec-title">${rec.subject}</h4>
          <p class="rec-desc">${rec.reason}</p>
        </div>
        <button class="btn btn-sm" onclick="triggerStarterPrompt('${rec.starter_prompt}', '${rec.subject}')">
          <i class="fas fa-play"></i> Learn Now
        </button>
      `;
      container.appendChild(div);
    });
  } catch (e) {
    console.error("Suggestions fetch error:", e);
  }
}

function triggerStarterPrompt(promptText, subject) {
  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.value = promptText;
    sendChat(subject);
  }
}

// ===== REGISTRATION =====
async function doRegister() {
  const name = document.getElementById('regName').value.trim();
  const school = document.getElementById('regSchool').value.trim();
  const cls = document.getElementById('regClass').value;
  const mobile = document.getElementById('regMobile').value.trim();
  const email = document.getElementById('regEmail').value.trim();

  if (!name || !school || !cls || !mobile) {
    alert('Please fill all required fields.');
    return;
  }
  if (mobile.length < 10 || !/^\d+$/.test(mobile)) {
    alert('Please enter a valid mobile number.');
    return;
  }

  const payload = {
    name,
    school_name: school,
    class_grade: cls,
    mobile,
    email: email || null,
    language: 'english',
    subjects: ["math", "science", "physics"]
  };

  try {
    const res = await fetch(API_BASE + '/register-enhanced', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || 'Registration failed.');
      return;
    }
    
    student = {
      id: data.student_id,
      name: data.student_name,
      school: data.school,
      class: data.class,
      mobile: data.mobile,
      email: data.email,
      loginCode: data.login_code
    };
    
    localStorage.setItem('atis_student', JSON.stringify(student));
    enterApp();
  } catch (e) {
    alert('Could not connect to registration server. Registering locally...');
    student = {
      id: 'STU' + Math.floor(Math.random() * 90000 + 10000),
      name, school, class: cls, mobile, email,
      registered: Date.now()
    };
    localStorage.setItem('atis_student', JSON.stringify(student));
    enterApp();
  }
}

async function enterApp() {
  document.querySelectorAll('.screen').forEach(s => s.classList.add('hidden'));
  const app = document.getElementById('app');
  app.classList.add('visible');
  
  document.getElementById('topbarUser').textContent = student.name + ' • ' + student.id;
  document.getElementById('kpiClass').textContent = student.class;
  document.getElementById('kpiChats').textContent = chatCount;
  document.getElementById('kpiModel').textContent = selectedModel === 'auto' ? 'Auto' : selectedModel.toUpperCase();
  
  // Set values in settings pane
  const ms = document.getElementById('modelSelect');
  if (ms) ms.value = selectedModel;
  const mn = document.getElementById('modelName');
  if (mn) mn.value = selectedModelName;

  // Load dynamically suggestions
  if (document.getElementById('regClass')) {
    document.getElementById('regClass').value = student.class;
    updateClassSuggestions();
  }
  
  // Load initial notes & diagrams
  pickTopic('photosynthesis', document.querySelector('#pane-diagrams .topic-btn'));
  pickNotes('photosynthesis', document.querySelector('#pane-notes .topic-btn'));
  loadAssignmentsList();
}

function doLogout() {
  if (!confirm('Logout and clear your profile?')) return;
  localStorage.removeItem('atis_student');
  localStorage.removeItem('atis_chats');
  localStorage.removeItem('atis_streak');
  localStorage.removeItem('atis_lastActive');
  student = null;
  document.getElementById('app').classList.remove('visible');
  showScreen('welcome');
}

// ===== NAV / PANES =====
function switchPane(name, btn) {
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('pane-' + name);
  if (target) target.classList.add('active');
  
  if (btn) {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  
  // Custom updates per pane
  if (name === 'tools') {
    resetTimerCircle();
  }
}

// ===== CHAT =====
function addMsg(text, type) {
  const box = document.getElementById('chatBox');
  const div = document.createElement('div');
  div.className = 'msg ' + type;
  div.innerHTML = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

async function sendChat(subjectOverride) {
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  addMsg(msg, 'user');
  chatCount++;
  localStorage.setItem('atis_chats', chatCount);
  document.getElementById('kpiChats').textContent = chatCount;

  // Toggle visual thinking mode
  addMsg('<i class="fas fa-spinner fa-spin"></i> AI is thinking...', 'ai temp-loader');
  
  const langCode = document.getElementById('voiceLang')?.value || 'en-IN';
  const langMap = {
    'en-IN': 'english',
    'hi-IN': 'hindi',
    'ta-IN': 'tamil',
    'te-IN': 'telugu',
    'mr-IN': 'marathi',
    'gu-IN': 'gujarati',
    'bn-IN': 'bengali'
  };
  const activeLang = langMap[langCode] || 'english';

  const payload = {
    message: msg,
    language: activeLang,
    student_id: student ? student.id : null,
    subject: subjectOverride || 'general',
    model_provider: selectedModel !== 'auto' ? selectedModel : undefined,
    model_name: selectedModelName || undefined
  };

  try {
    const res = await fetch(API_BASE + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    
    // Remove spinner
    const loaders = document.querySelectorAll('.temp-loader');
    loaders.forEach(l => l.remove());
    
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    
    addMsg(data.reply, 'ai');
    
    // Handle speak synthesis if checked
    const toggle = document.getElementById('voiceSpeakToggle');
    if (toggle && toggle.checked) {
      speakText(data.reply);
    }
  } catch (e) {
    const loaders = document.querySelectorAll('.temp-loader');
    loaders.forEach(l => l.remove());
    addMsg('⚠️ ' + (e.message || 'Could not reach the AI server.'), 'ai');
  }
}

function clearChat() {
  document.getElementById('chatBox').innerHTML = '<div class="msg ai">👋 Chat cleared. Ask me anything!</div>';
}

// ===== VOICE MODULE =====
let recognition = null;
let isListening = false;

function toggleVoice() {
  const btn = document.getElementById('voiceBtn');
  const status = document.getElementById('voiceStatus');
  const wave = document.getElementById('voiceWave');
  const lang = document.getElementById('voiceLang').value;
  
  if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
    status.textContent = 'Voice recognition not supported in this browser. Please try Chrome/Safari.';
    return;
  }
  
  if (isListening) {
    recognition.stop();
    isListening = false;
    btn.textContent = 'Start Listening';
    btn.classList.remove('btn-outline');
    wave.classList.remove('active');
    return;
  }
  
  recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
  recognition.lang = lang;
  recognition.continuous = false;
  recognition.interimResults = false;
  
  recognition.onstart = () => {
    isListening = true;
    btn.textContent = '⏹ Stop Listening';
    btn.classList.add('btn-outline');
    wave.classList.add('active');
    status.textContent = 'Speak now — listening...';
  };
  
  recognition.onresult = (e) => {
    const text = e.results[0][0].transcript;
    status.textContent = 'You said: "' + text + '"';
    
    // Put in chat input and execute
    const chatInput = document.getElementById('chatInput');
    if (chatInput) {
      chatInput.value = text;
      sendChat();
    }
  };
  
  recognition.onerror = (e) => {
    status.textContent = 'Voice error: ' + e.error;
    isListening = false;
    btn.textContent = 'Start Listening';
    btn.classList.remove('btn-outline');
    wave.classList.remove('active');
  };
  
  recognition.onend = () => {
    isListening = false;
    btn.textContent = 'Start Listening';
    btn.classList.remove('btn-outline');
    wave.classList.remove('active');
  };
  
  recognition.start();
}

function speakText(text) {
  if ('speechSynthesis' in window) {
    // Cancel currently speaking
    window.speechSynthesis.cancel();
    
    // Clean text from emojis, special symbols for cleaner voice
    const cleanText = text.replace(/[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDC00-\uDFFF]/g, '')
                          .replace(/[#\*`_\-]/g, ' ')
                          .trim();
                          
    const utterance = new SpeechSynthesisUtterance(cleanText);
    const lang = document.getElementById('voiceLang').value;
    utterance.lang = lang;
    
    // Try to find a nice regional matching voice
    const voices = window.speechSynthesis.getVoices();
    const matches = voices.filter(v => v.lang.startsWith(lang.split('-')[0]));
    if (matches.length > 0) {
      utterance.voice = matches[0];
    }
    
    utterance.rate = 1.05;
    utterance.pitch = 1.0;
    
    // Show stop voice button
    const stopBtn = document.getElementById('stopSpeakBtn');
    if (stopBtn) stopBtn.style.display = 'inline-flex';
    
    utterance.onend = () => {
      if (stopBtn) stopBtn.style.display = 'none';
    };
    utterance.onerror = () => {
      if (stopBtn) stopBtn.style.display = 'none';
    };
    
    window.speechSynthesis.speak(utterance);
  }
}

function stopSpeaking() {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  const stopBtn = document.getElementById('stopSpeakBtn');
  if (stopBtn) stopBtn.style.display = 'none';
}

// ===== DIAGRAMS ENGINE =====
const topicData = {
  photosynthesis: {
    icon: '🌿',
    title: 'Photosynthesis',
    desc: 'Chlorophyll traps sunlight converting 6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂ inside chloroplasts. Driven by Light Reactions and the Calvin Cycle.',
    spec: {
      labels: ["Sunlight (Light Source)", "Stomata (Gas Exchange)", "Water Inlet (Roots)", "Chloroplast (Thylakoids)"],
      coords: [
        { name: "Sunlight Intake", cx: 160, cy: 90, r: 8, info: "Photons drive light reactions, split water, and excite electrons inside chloroplast thylakoids." },
        { name: "Stomata Openings", cx: 340, cy: 190, r: 8, info: "Microscopic leaf pores where CO2 enters and O2 is expelled during transpiration." },
        { name: "Vascular Water Transport", cx: 110, cy: 260, r: 8, info: "Roots pull water from soil. Xylem tubes move fluid up the plant stem directly to leaves." },
        { name: "Chloroplast Stroma", cx: 240, cy: 160, r: 8, info: "Chlorophyll-filled organelle where light reactions split water and Calvin cycle builds glucose." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Background glow -->
          <circle cx="200" cy="160" r="130" fill="rgba(20, 184, 166, 0.05)" filter="blur(30px)"></circle>
          <!-- Sun -->
          <circle cx="60" cy="50" r="30" fill="#f59e0b" filter="drop-shadow(0 0 16px rgba(245,158,11,0.5))"></circle>
          <line x1="60" y1="90" x2="60" y2="120" stroke="#f59e0b" stroke-width="3" stroke-linecap="round" stroke-dasharray="4,4"></line>
          <line x1="95" y1="85" x2="120" y2="110" stroke="#f59e0b" stroke-width="3" stroke-linecap="round" stroke-dasharray="4,4"></line>
          <!-- Leaf -->
          <path d="M 120 280 C 120 180, 240 100, 360 120 C 340 220, 240 320, 120 280 Z" fill="rgba(16, 185, 129, 0.2)" stroke="#10b981" stroke-width="3"></path>
          <path d="M 120 280 Q 240 200 360 120" fill="none" stroke="#10b981" stroke-width="2"></path>
          <!-- Stem -->
          <path d="M 70 320 Q 120 300 120 280" fill="none" stroke="#047857" stroke-width="5" stroke-linecap="round"></path>
          <!-- Water uptake arrow -->
          <path d="M 80 310 Q 110 290 120 270" fill="none" stroke="#3b82f6" stroke-width="3" marker-end="url(#arrow)" stroke-dasharray="5,5"></path>
          <!-- CO2 & O2 bubbles -->
          <circle cx="340" cy="190" r="14" fill="none" stroke="#8b5cf6" stroke-width="2" stroke-dasharray="3,3"></circle>
          <text x="325" y="170" fill="#a78bfa" font-size="10" font-weight="700">GASES</text>
        </svg>
      `
    }
  },
  quantum: {
    icon: '⚛️',
    title: 'Quantum States',
    desc: 'Probability distributions of electron energy orbits. Matter exhibits wave-particle duality and exists in superposition states.',
    spec: {
      labels: ["Nucleus Core", "Energy Level n=1", "Energy Level n=2", "Probability Clouds"],
      coords: [
        { name: "Proton/Neutron Nucleus", cx: 200, cy: 160, r: 8, info: "Dense center of the atom containing protons and neutrons bound by the strong nuclear force." },
        { name: "Ground State (n=1)", cx: 200, cy: 110, r: 8, info: "Lowest possible energy orbit. Extremely high probability density for electrons." },
        { name: "Superposition (n=2)", cx: 280, cy: 160, r: 8, info: "First excited orbital ring. Particles exist as wave packets across multiple coordinate states." },
        { name: "Quantum Tunneling Zone", cx: 120, cy: 210, r: 8, info: "Uncertainty margins where electrons can barrier-penetrate despite insufficient classical kinetic energy." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Background quantum waves -->
          <path d="M 50 160 Q 125 60 200 160 T 350 160" fill="none" stroke="rgba(139, 92, 246, 0.15)" stroke-width="2"></path>
          <path d="M 50 160 Q 125 260 200 160 T 350 160" fill="none" stroke="rgba(217, 70, 239, 0.1)" stroke-width="1.5"></path>
          <!-- Shells -->
          <circle cx="200" cy="160" r="50" fill="none" stroke="rgba(99, 102, 241, 0.3)" stroke-width="2" stroke-dasharray="6,4"></circle>
          <circle cx="200" cy="160" r="90" fill="none" stroke="rgba(217, 70, 239, 0.25)" stroke-width="2"></circle>
          <!-- Nucleus -->
          <circle cx="200" cy="160" r="14" fill="#ef4444" filter="drop-shadow(0 0 10px rgba(239,68,68,0.5))"></circle>
          <!-- Orbiting electrons -->
          <circle cx="200" cy="110" r="6" fill="#14b8a6" filter="drop-shadow(0 0 8px #14b8a6)"></circle>
          <circle cx="290" cy="160" r="6" fill="#d946ef" filter="drop-shadow(0 0 8px #d946ef)"></circle>
        </svg>
      `
    }
  },
  heart: {
    icon: '❤️',
    title: 'Heart Chambers',
    desc: 'The human heart pumps deoxygenated blood through systemic vena return and ejects high-pressure oxygenated blood via the Aorta.',
    spec: {
      labels: ["Vena Cava", "Right Atrium/Ventricle", "Left Atrium/Ventricle", "Aorta Pathway"],
      coords: [
        { name: "Vena Return Vena Cava", cx: 140, cy: 90, r: 8, info: "Drains deoxygenated venous return from head and upper body directly into the right heart chambers." },
        { name: "Right Ventricle Chamber", cx: 170, cy: 220, r: 8, info: "Pumps deoxygenated blood under low pressure to pulmonary capillary channels for re-oxygenation." },
        { name: "Left Ventricle Chamber", cx: 230, cy: 220, r: 8, info: "Thickest muscular chamber. Generates systemic systolic pressure to eject fluid throughout the vascular frame." },
        { name: "Aortic Valve Outlet", cx: 200, cy: 110, r: 8, info: "High-pressure arch conveying oxygen-rich blood directly to systemic arteries." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Background heart shape glow -->
          <path d="M 120 120 C 80 40, 200 40, 200 120 C 200 40, 320 40, 280 120 C 240 200, 200 240, 200 280 C 200 240, 160 200, 120 120 Z" fill="rgba(239, 68, 68, 0.05)" filter="blur(25px)"></path>
          <!-- Schematic Heart Outline -->
          <path d="M 140 100 C 110 50, 200 30, 200 110 C 200 30, 290 50, 260 100 C 230 150, 200 210, 200 260 C 200 210, 170 150, 140 100 Z" fill="rgba(239, 68, 68, 0.15)" stroke="#ef4444" stroke-width="3"></path>
          <!-- Division Septum -->
          <line x1="200" y1="110" x2="200" y2="255" stroke="rgba(255,255,255,0.2)" stroke-width="4"></line>
          <!-- Vena Cava -->
          <rect x="125" y="60" width="16" height="60" rx="4" fill="#3b82f6" opacity="0.8"></rect>
          <!-- Aorta -->
          <path d="M 190 110 Q 195 50 215 50 T 235 90" fill="none" stroke="#ef4444" stroke-width="12" stroke-linecap="round"></path>
        </svg>
      `
    }
  },
  dna: {
    icon: '🧬',
    title: 'DNA Structure',
    desc: 'Double helix holding structural genetic instructions. Base pairings bind Adenine-Thymine (2 H-bonds) and Guanine-Cytosine (3 H-bonds).',
    spec: {
      labels: ["Sugar-Phosphate Backbone", "Adenine-Thymine Pair", "Guanine-Cytosine Pair", "Helical Major Groove"],
      coords: [
        { name: "Phosphate Chain Backbone", cx: 120, cy: 110, r: 8, info: "Covalent linkages of alternating sugar-phosphate repeating polymers forming the structural rail." },
        { name: "A-T H-Bond Pairings", cx: 200, cy: 130, r: 8, info: "Adenine binds with Thymine via 2 weak hydrogen linkages." },
        { name: "G-C H-Bond Pairings", cx: 200, cy: 190, r: 8, info: "Guanine binds with Cytosine via 3 hydrogen bonds, providing slightly higher melting stability." },
        { name: "Major Groove Spacing", cx: 280, cy: 220, r: 8, info: "Wider spiral channel critical for transcription factor binding and genomic regulatory docking." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Background matrix -->
          <circle cx="200" cy="160" r="120" fill="rgba(99, 102, 241, 0.03)" filter="blur(30px)"></circle>
          <!-- Helical strand 1 -->
          <path d="M 120 60 Q 280 140 120 220 T 120 380" fill="none" stroke="#6366f1" stroke-width="4" stroke-linecap="round"></path>
          <!-- Helical strand 2 -->
          <path d="M 280 60 Q 120 140 280 220 T 280 380" fill="none" stroke="#d946ef" stroke-width="4" stroke-linecap="round"></path>
          <!-- Base pair connections -->
          <line x1="165" y1="100" x2="235" y2="100" stroke="#10b981" stroke-width="3"></line>
          <line x1="130" y1="140" x2="270" y2="140" stroke="#f59e0b" stroke-width="3"></line>
          <line x1="130" y1="180" x2="270" y2="180" stroke="#3b82f6" stroke-width="3"></line>
          <line x1="165" y1="220" x2="235" y2="220" stroke="#ef4444" stroke-width="3"></line>
        </svg>
      `
    }
  },
  newton: {
    icon: '⚡',
    title: "Newton's Second Law",
    desc: 'Force increases linearly with mass and acceleration: F = ma. Adjust variables below to observe vector calculations in real-time.',
    spec: {
      labels: ["Block Mass", "Applied Force Vector", "Frictional Opposing Force", "Resultant Acceleration"],
      coords: [
        { name: "Inertial Mass", cx: 160, cy: 190, r: 8, info: "Object mass (m) resists changes in motion. Higher mass requires greater net force to accelerate." },
        { name: "Active Force (F)", cx: 280, cy: 190, r: 8, info: "Applied force push (F) represented as a linear vector. Force is directly proportional to acceleration." },
        { name: "Friction Drag (f)", cx: 70, cy: 215, r: 8, info: "Opposing contact resistance. Net Force = Applied Force - Frictional Force." },
        { name: "Acceleration Output (a)", cx: 160, cy: 120, r: 8, info: "Rate of change of speed (a = F/m). Directed matching net force vector." }
      ],
      svg: `
        <svg class="svg-diagram" id="newtonSvg" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Floor -->
          <line x1="40" y1="220" x2="360" y2="220" stroke="#475569" stroke-width="4" stroke-linecap="round"></line>
          <!-- Block Mass -->
          <rect id="simBlock" x="110" y="160" width="100" height="60" rx="8" fill="rgba(99, 102, 241, 0.25)" stroke="#6366f1" stroke-width="3"></rect>
          <text id="simMassText" x="145" y="195" fill="#fff" font-size="14" font-weight="800">5 kg</text>
          <!-- Force Arrow (applied) -->
          <path id="simForceArrow" d="M 215 190 L 285 190" fill="none" stroke="#d946ef" stroke-width="4" marker-end="url(#arrow-pink)"></path>
          <text id="simForceText" x="235" y="175" fill="#f472b6" font-size="12" font-weight="700">20 N</text>
          <!-- Acceleration Vector -->
          <path id="simAccArrow" d="M 120 130 L 200 130" fill="none" stroke="#14b8a6" stroke-width="3" marker-end="url(#arrow-teal)"></path>
          <text id="simAccText" x="140" y="115" fill="#2dd4bf" font-size="12" font-weight="700">a = 4.0 m/s²</text>
          
          <!-- Definitions for markers -->
          <defs>
            <marker id="arrow-pink" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#d946ef"/>
            </marker>
            <marker id="arrow-teal" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#14b8a6"/>
            </marker>
          </defs>
      `
    }
  },
  atom: {
    icon: '⚛️',
    title: 'Atom Structure',
    desc: 'Dense central atomic nucleus containing positively charged protons and neutral neutrons, orbited by negative valence electrons.',
    spec: {
      labels: ["Nucleus Core", "Energy Orbitals", "Valence Electron", "Outer Shell"],
      coords: [
        { name: "Nucleus Core", cx: 200, cy: 160, r: 8, info: "Dense center of the atom containing protons (positive) and neutrons (neutral) bound by the strong nuclear force." },
        { name: "Energy Orbitals", cx: 200, cy: 90, r: 8, info: "Discreet mathematical path envelopes around the nucleus where electrons spin within specific probability bands." },
        { name: "Valence Electron", cx: 320, cy: 160, r: 8, info: "Positional boundary of valence shell electrons, which carry negative charge and govern chemical reactivity." },
        { name: "Outer Shell", cx: 200, cy: 250, r: 8, info: "The outermost electronic energy orbit. Filled valence shells provide chemical inertness." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Background quantum blur -->
          <circle cx="200" cy="160" r="130" fill="rgba(99, 102, 241, 0.04)" filter="blur(30px)"></circle>
          
          <!-- Orbits (Concentric Ellipses) -->
          <ellipse cx="200" cy="160" rx="90" ry="35" fill="none" stroke="rgba(99, 102, 241, 0.3)" stroke-width="2" stroke-dasharray="6,4" transform="rotate(30 200 160)"></ellipse>
          <ellipse cx="200" cy="160" rx="90" ry="35" fill="none" stroke="rgba(217, 70, 239, 0.3)" stroke-width="2" stroke-dasharray="6,4" transform="rotate(-30 200 160)"></ellipse>
          <circle cx="200" cy="160" r="120" fill="none" stroke="rgba(20, 184, 166, 0.3)" stroke-width="2" stroke-dasharray="8,4"></circle>
          
          <!-- Central Nucleus Cluster -->
          <!-- Protons (Red) -->
          <circle cx="194" cy="154" r="7" fill="#ef4444" filter="drop-shadow(0 0 4px rgba(239,68,68,0.5))"></circle>
          <circle cx="206" cy="166" r="7" fill="#ef4444" filter="drop-shadow(0 0 4px rgba(239,68,68,0.5))"></circle>
          <circle cx="195" cy="166" r="7" fill="#ef4444" filter="drop-shadow(0 0 4px rgba(239,68,68,0.5))"></circle>
          <!-- Neutrons (Blue) -->
          <circle cx="206" cy="154" r="7" fill="#3b82f6" filter="drop-shadow(0 0 4px rgba(59,130,246,0.5))"></circle>
          <circle cx="200" cy="160" r="7" fill="#3b82f6" filter="drop-shadow(0 0 4px rgba(59,130,246,0.5))"></circle>
          
          <!-- Orbiting Electrons -->
          <circle cx="122" cy="115" r="5" fill="#d946ef" filter="drop-shadow(0 0 6px #d946ef)"></circle>
          <circle cx="278" cy="205" r="5" fill="#6366f1" filter="drop-shadow(0 0 6px #6366f1)"></circle>
          <circle cx="320" cy="160" r="5" fill="#14b8a6" filter="drop-shadow(0 0 6px #14b8a6)"></circle>
        </svg>
      `
    }
  },
  water: {
    icon: '🌧️',
    title: 'Water Cycle',
    desc: 'The continuous movement of water on, above, and below the surface of the Earth driven by solar energy.',
    spec: {
      labels: ["Solar Heating", "Evaporation Stage", "Condensation Clouds", "Precipitation Rain"],
      coords: [
        { name: "Solar Heating", cx: 60, cy: 60, r: 8, info: "The Sun emits radiation that supplies energy to heat oceans, lakes, and soil water reservoirs." },
        { name: "Evaporation Stage", cx: 150, cy: 220, r: 8, info: "Thermal energy excites surface water molecules, breaking chemical bonds to transform liquid into gaseous vapor." },
        { name: "Condensation Clouds", cx: 260, cy: 70, r: 8, info: "Water vapor cools down as it rises into high altitude margins, condensing into liquid droplets to form clouds." },
        { name: "Precipitation Rain", cx: 330, cy: 190, r: 8, info: "Heavy condensates drop from clouds as gravity pulls rain, sleet, snow, or ice blocks back to Earth." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Sun glowing -->
          <circle cx="60" cy="60" r="22" fill="#f59e0b" filter="drop-shadow(0 0 16px rgba(245,158,11,0.6))"></circle>
          <line x1="60" y1="92" x2="60" y2="108" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round"></line>
          <line x1="92" y1="60" x2="108" y2="60" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round"></line>
          <line x1="82" y1="82" x2="94" y2="94" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round"></line>
          
          <!-- Mountains -->
          <path d="M 240 260 L 320 140 L 370 210 L 400 260 Z" fill="rgba(120, 113, 108, 0.3)" stroke="#78716c" stroke-width="2.5"></path>
          <path d="M 280 260 L 350 160 L 400 260 Z" fill="rgba(120, 113, 108, 0.2)" stroke="#78716c" stroke-width="1.5"></path>
          
          <!-- Water body / Ocean -->
          <path d="M 0 260 Q 100 245 200 260 T 400 260 L 400 320 L 0 320 Z" fill="rgba(59, 130, 246, 0.25)" stroke="#3b82f6" stroke-width="3"></path>
          
          <!-- Clouds -->
          <path d="M 220 80 Q 205 60 230 45 Q 255 30 280 45 Q 305 35 315 55 Q 335 70 315 90 Q 290 100 265 90 Q 240 100 220 80 Z" fill="rgba(255,255,255,0.15)" stroke="#e2e8f0" stroke-width="2" filter="drop-shadow(0 4px 6px rgba(255,255,255,0.05))"></path>
          
          <!-- Evaporation Arrows -->
          <path d="M 140 235 Q 155 170 210 110" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-dasharray="4,4" marker-end="url(#arrow)"></path>
          <path d="M 100 235 Q 115 160 180 100" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-dasharray="4,4" marker-end="url(#arrow)"></path>
          
          <!-- Precipitation drops -->
          <line x1="290" y1="100" x2="270" y2="140" stroke="#93c5fd" stroke-width="2" stroke-dasharray="4,4"></line>
          <line x1="315" y1="100" x2="295" y2="140" stroke="#93c5fd" stroke-width="2" stroke-dasharray="4,4"></line>
          <line x1="340" y1="100" x2="320" y2="140" stroke="#93c5fd" stroke-width="2" stroke-dasharray="4,4"></line>
        </svg>
      `
    }
  },
  cell: {
    icon: '🌿',
    title: 'Cell Organelles',
    desc: 'The basic biological building blocks containing protective boundaries, genetic nucleus cores, and mitochondria power plants.',
    spec: {
      labels: ["Outer Membrane", "Nucleus Center", "Mitochondria Powerhouse", "Cytoplasm Matrix"],
      coords: [
        { name: "Outer Membrane", cx: 80, cy: 160, r: 8, info: "The protective semi-permeable boundary controlling transport of proteins and water ions." },
        { name: "Nucleus Center", cx: 200, cy: 160, r: 8, info: "Genetic chamber storing double-stranded DNA and hosting mRNA replication processes." },
        { name: "Mitochondria Powerhouse", cx: 290, cy: 110, r: 8, info: "Powerhouse metabolizing pyruvate to generate active ATP energy vectors inside cristae folds." },
        { name: "Cytoplasm Matrix", cx: 230, cy: 240, r: 8, info: "The gelatinous interior matrix maintaining structural turgidity and protecting vital organelles." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Cell Wall / Membrane boundary -->
          <rect x="40" y="30" width="320" height="260" rx="60" fill="rgba(16, 185, 129, 0.04)" stroke="#10b981" stroke-width="4" stroke-dasharray="4,2"></rect>
          
          <!-- Vacuole -->
          <ellipse cx="110" cy="100" rx="30" ry="20" fill="rgba(59, 130, 246, 0.15)" stroke="#3b82f6" stroke-width="2"></ellipse>
          
          <!-- Nucleus Envelope -->
          <circle cx="200" cy="160" r="38" fill="rgba(139, 92, 246, 0.15)" stroke="#8b5cf6" stroke-width="2.5"></circle>
          <!-- Nucleolus Core -->
          <circle cx="200" cy="160" r="14" fill="#8b5cf6" filter="drop-shadow(0 0 6px #8b5cf6)"></circle>
          
          <!-- Mitochondria -->
          <path d="M 285 95 C 285 85, 315 90, 315 105 C 315 120, 285 115, 285 95 Z" fill="rgba(239, 68, 68, 0.2)" stroke="#ef4444" stroke-width="2"></path>
          <path d="M 290 98 Q 300 93 305 103" fill="none" stroke="#ef4444" stroke-width="1.5"></path>
          
          <!-- Endoplasmic Reticulum (wavy lines) -->
          <path d="M 152 145 Q 135 155 152 165 T 152 185" fill="none" stroke="#ec4899" stroke-width="3" stroke-linecap="round"></path>
          
          <!-- Chloroplast (Stroma layers) -->
          <ellipse cx="110" cy="220" rx="22" ry="14" fill="rgba(16, 185, 129, 0.25)" stroke="#10b981" stroke-width="2"></ellipse>
          <line x1="98" y1="220" x2="122" y2="220" stroke="#10b981" stroke-width="1.5"></line>
        </svg>
      `
    }
  },
  volcano: {
    icon: '🌋',
    title: 'Volcano Eruption',
    desc: 'Geological formation where tectonic activity forces high-pressure magma from the mantle to breach the Earth\'s crust.',
    spec: {
      labels: ["Magma Chamber", "Conduit Pipe", "Crater Vent", "Ash Cloud"],
      coords: [
        { name: "Magma Chamber", cx: 200, cy: 270, r: 8, info: "Subterranean pressure reservoir collecting liquid molten rock directly from the mantle." },
        { name: "Conduit Pipe", cx: 200, cy: 190, r: 8, info: "The primary inner vent path guiding volcanic fluid elements upward toward the crust surface." },
        { name: "Crater Vent", cx: 200, cy: 110, r: 8, info: "The bowl-shaped summit depression that triggers active lava ejecta and volcanic ash releases." },
        { name: "Ash Cloud", cx: 270, cy: 60, r: 8, info: "Pillar of pulverized dust rock, gaseous sulfur, and atmospheric vapor blown into high orbits." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Background sky glow -->
          <circle cx="200" cy="160" r="130" fill="rgba(239, 68, 68, 0.03)" filter="blur(30px)"></circle>
          
          <!-- Ash and smoke cloud -->
          <path d="M 120 80 Q 150 40 200 55 Q 250 30 280 70 Q 320 70 300 100 Q 250 120 200 105 Q 150 120 120 80 Z" fill="rgba(100, 116, 139, 0.45)" stroke="#64748b" stroke-width="2.5"></path>
          
          <!-- Volcano main mountain -->
          <path d="M 50 280 L 165 130 L 180 142 L 220 142 L 235 130 L 350 280 Z" fill="rgba(120, 113, 108, 0.35)" stroke="#78716c" stroke-width="3"></path>
          
          <!-- Magma Chamber pool -->
          <ellipse cx="200" cy="275" rx="45" ry="22" fill="rgba(239, 68, 68, 0.3)" stroke="#ef4444" stroke-width="2.5" filter="drop-shadow(0 0 8px #ef4444)"></ellipse>
          
          <!-- Central conduit conduit -->
          <rect x="193" y="138" width="14" height="114" fill="rgba(239, 68, 68, 0.35)" stroke="#ef4444" stroke-width="1.5"></rect>
          
          <!-- Active lava flow -->
          <path d="M 172 136 Q 140 180 110 220" fill="none" stroke="#f59e0b" stroke-width="4" stroke-linecap="round" filter="drop-shadow(0 0 4px #f59e0b)"></path>
          <path d="M 228 136 Q 260 180 290 225" fill="none" stroke="#f59e0b" stroke-width="4" stroke-linecap="round" filter="drop-shadow(0 0 4px #f59e0b)"></path>
        </svg>
      `
    }
  },
  mitosis: {
    icon: '🧬',
    title: 'Mitosis Division',
    desc: 'The division of somatic eukaryotic cell structures, aligning and pulling sister chromatids apart into two identical daughter cells.',
    spec: {
      labels: ["Centrioles", "Spindle Fibers", "Sister Chromatids", "Cleavage Furrow"],
      coords: [
        { name: "Centrioles", cx: 70, cy: 160, r: 8, info: "Anchoring centrosome bodies that organize microtubule fibers at opposite polar regions." },
        { name: "Spindle Fibers", cx: 140, cy: 120, r: 8, info: "Microtubule networks pulling individual chromatid halves toward opposing poles." },
        { name: "Sister Chromatids", cx: 200, cy: 160, r: 8, info: "Replicated chromosome bundles lined up along the central mitotic equatorial plane." },
        { name: "Cleavage Furrow", cx: 200, cy: 80, r: 8, info: "Cleavage contract ring pinching the membrane center during active cytokinesis." }
      ],
      svg: `
        <svg class="svg-diagram" viewBox="0 0 400 320" xmlns="http://www.w3.org/2000/svg">
          <!-- Dumbbell shaped dividing cell -->
          <path d="M 50 160 C 50 85, 145 85, 185 130 C 190 138, 210 138, 215 130 C 255 85, 350 85, 350 160 C 350 235, 255 235, 215 190 C 210 182, 190 182, 185 190 C 145 235, 50 235, 50 160 Z" fill="rgba(236, 72, 153, 0.05)" stroke="#ec4899" stroke-width="3.5" stroke-dasharray="4,2"></path>
          
          <!-- Centrioles at opposite poles -->
          <rect x="66" y="152" width="8" height="16" fill="#14b8a6" transform="rotate(45, 70, 160)"></rect>
          <rect x="326" y="152" width="8" height="16" fill="#14b8a6" transform="rotate(-45, 330, 160)"></rect>
          
          <!-- Chromatids splitting and moving -->
          <!-- Left group -->
          <path d="M 130 120 L 115 135 L 130 150" fill="none" stroke="#8b5cf6" stroke-width="3" stroke-linecap="round"></path>
          <path d="M 130 170 L 115 185 L 130 200" fill="none" stroke="#3b82f6" stroke-width="3" stroke-linecap="round"></path>
          <!-- Right group -->
          <path d="M 270 120 L 285 135 L 270 150" fill="none" stroke="#8b5cf6" stroke-width="3" stroke-linecap="round"></path>
          <path d="M 270 170 L 285 185 L 270 200" fill="none" stroke="#3b82f6" stroke-width="3" stroke-linecap="round"></path>
          
          <!-- Spindle Fiber Lines -->
          <line x1="70" y1="160" x2="115" y2="135" stroke="rgba(20, 184, 166, 0.35)" stroke-width="1.5" stroke-dasharray="3,3"></line>
          <line x1="70" y1="160" x2="115" y2="185" stroke="rgba(20, 184, 166, 0.35)" stroke-width="1.5" stroke-dasharray="3,3"></line>
          <line x1="330" y1="160" x2="285" y2="135" stroke="rgba(20, 184, 166, 0.35)" stroke-width="1.5" stroke-dasharray="3,3"></line>
          <line x1="330" y1="160" x2="285" y2="185" stroke="rgba(20, 184, 166, 0.35)" stroke-width="1.5" stroke-dasharray="3,3"></line>
        </svg>
      `
    }
  }
};

function pickTopic(key, btn) {
  document.querySelectorAll('#pane-diagrams .topic-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  
  const t = topicData[key];
  if (!t) return;
  
  document.getElementById('diagramTitle').textContent = t.title;
  document.getElementById('diagramDesc').textContent = t.desc;
  
  const viewport = document.getElementById('diagramViewport');
  viewport.innerHTML = t.spec.svg;
  
  // Add coordinate circles
  const svgEl = viewport.querySelector('svg');
  t.spec.coords.forEach((coord, idx) => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "hotspot");
    g.setAttribute("transform", `translate(0, 0)`);
    g.addEventListener('mouseenter', (e) => showHotspotTooltip(coord, e));
    g.addEventListener('mouseleave', hideHotspotTooltip);
    g.addEventListener('click', () => triggerHotspotChat(coord.name, t.title));
    
    // Pulse outer circle
    const pulse = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    pulse.setAttribute("cx", coord.cx);
    pulse.setAttribute("cy", coord.cy);
    pulse.setAttribute("r", coord.r + 6);
    pulse.setAttribute("fill", "rgba(99,102,241,0.2)");
    pulse.setAttribute("class", "hotspot-pulse");
    
    // Core center circle
    const core = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    core.setAttribute("cx", coord.cx);
    core.setAttribute("cy", coord.cy);
    core.setAttribute("r", coord.r);
    core.setAttribute("fill", "#6366f1");
    core.setAttribute("stroke", "#fff");
    core.setAttribute("stroke-width", "2");
    
    g.appendChild(pulse);
    g.appendChild(core);
    svgEl.appendChild(g);
  });
  
  // Custom Controls for Simulation
  const controlsContainer = document.getElementById('simControlsContainer');
  controlsContainer.innerHTML = '';
  
  if (key === 'newton') {
    controlsContainer.innerHTML = `
      <div class="sim-controls">
        <div class="sim-slider-group">
          <div class="sim-slider-label">Mass (m): <span id="valMass">5 kg</span></div>
          <input type="range" class="sim-slider" id="sliderMass" min="1" max="10" value="5" oninput="updateNewtonSim()">
        </div>
        <div class="sim-slider-group" style="margin-top:16px">
          <div class="sim-slider-label">Applied Force (F): <span id="valForce">20 N</span></div>
          <input type="range" class="sim-slider" id="sliderForce" min="5" max="50" value="20" oninput="updateNewtonSim()">
        </div>
        <button class="btn btn-sm btn-block" style="margin-top:20px" onclick="runNewtonSimulation()">
          <i class="fas fa-play"></i> Animate Block
        </button>
      </div>
    `;
    updateNewtonSim();
  } else {
    // Show a small curriculum stats chart
    controlsContainer.innerHTML = `
      <div style="margin-top:16px;padding:16px;background:rgba(255,255,255,0.02);border-radius:12px;border:1px solid var(--border)">
        <p style="color:var(--text);font-size:13px;font-weight:700;margin-bottom:10px">Interactive Lesson Mastery</p>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
          <span style="font-size:12px;width:100px;color:var(--muted)">Diagram hotspots:</span>
          <div style="flex:1;height:6px;background:rgba(255,255,255,0.1);border-radius:99px;overflow:hidden">
            <div style="width:100%;height:100%;background:var(--teal)"></div>
          </div>
          <span style="font-size:12px;font-weight:700">100%</span>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
          <span style="font-size:12px;width:100px;color:var(--muted)">Topic Quiz:</span>
          <div style="flex:1;height:6px;background:rgba(255,255,255,0.1);border-radius:99px;overflow:hidden">
            <div style="width:80%;height:100%;background:var(--accent)"></div>
          </div>
          <span style="font-size:12px;font-weight:700">80%</span>
        </div>
      </div>
    `;
  }

  // Update study tools Mind Map Outline based on current key
  updateMindMap(key);
}

function showHotspotTooltip(coord, e) {
  const tooltip = document.getElementById('diagramTooltip');
  const title = document.getElementById('tooltipTitle');
  const desc = document.getElementById('tooltipDesc');
  
  title.textContent = coord.name;
  desc.textContent = coord.info;
  
  tooltip.classList.remove('hidden');
}

function hideHotspotTooltip() {
  const tooltip = document.getElementById('diagramTooltip');
  if (tooltip) tooltip.classList.add('hidden');
}

function triggerHotspotChat(hotspotName, topicName) {
  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.value = `Explain the significance of "${hotspotName}" in ${topicName} process.`;
    sendChat(topicName);
  }
}

// Newton Simulation variables recalculation
function updateNewtonSim() {
  simMass = parseInt(document.getElementById('sliderMass').value);
  simForce = parseInt(document.getElementById('sliderForce').value);
  
  document.getElementById('valMass').textContent = simMass + ' kg';
  document.getElementById('valForce').textContent = simForce + ' N';
  
  const acc = (simForce / simMass).toFixed(1);
  
  // Update SVGs
  const simMassText = document.getElementById('simMassText');
  if (simMassText) simMassText.textContent = simMass + ' kg';
  
  const simForceText = document.getElementById('simForceText');
  if (simForceText) simForceText.textContent = simForce + ' N';
  
  const simAccText = document.getElementById('simAccText');
  if (simAccText) simAccText.textContent = `a = ${acc} m/s²`;
  
  // Scale applied force vector length
  const simForceArrow = document.getElementById('simForceArrow');
  if (simForceArrow) {
    const arrowLength = 70 + (simForce * 1.5);
    simForceArrow.setAttribute('d', `M 215 190 L ${215 + arrowLength} 190`);
  }
  
  // Scale acceleration vector length
  const simAccArrow = document.getElementById('simAccArrow');
  if (simAccArrow) {
    const arrowLength = 60 + (acc * 8);
    simAccArrow.setAttribute('d', `M 120 130 L ${120 + arrowLength} 130`);
  }
}

function runNewtonSimulation() {
  const block = document.getElementById('simBlock');
  if (!block) return;
  
  clearInterval(simInterval);
  simPos = 50;
  simSpeed = 0;
  const acc = (simForce / simMass) * 0.05;
  
  simInterval = setInterval(() => {
    simSpeed += acc;
    simPos += simSpeed;
    
    if (simPos > 240) {
      clearInterval(simInterval);
      simPos = 110; // reset center
    }
    
    // Move block
    block.setAttribute('x', simPos);
    
    // Move texts & arrows relative to block
    const simMassText = document.getElementById('simMassText');
    if (simMassText) simMassText.setAttribute('x', simPos + 35);
    
    const simForceArrow = document.getElementById('simForceArrow');
    if (simForceArrow) {
      const flen = 70 + (simForce * 1.5);
      simForceArrow.setAttribute('d', `M ${simPos + 105} 190 L ${simPos + 105 + flen} 190`);
    }
    const simForceText = document.getElementById('simForceText');
    if (simForceText) simForceText.setAttribute('x', simPos + 125);
    
  }, 30);
}

// ===== STUDY NOTES & FLASHCARDS MODULE =====
const notesData = {
  photosynthesis: {
    title: "Photosynthesis Notes",
    content: `
      <h3>🌿 Light Energy → Chemical Energy</h3>
      <p>Photosynthesis is the structural process whereby green plants, algae, and specific bacteria capture sunlight and lock it as high-energy carbohydrate molecules.</p>
      <p><b>Chemical Equation:</b> 6CO₂ + 6H₂O + light → C₆H₁₂O₆ + 6O₂.</p>
      <p><b>Critical Concepts:</b></p>
      <ul>
        <li><b>Light Reactions:</b> Occur in the thylakoid membranes where excited chlorophyll structures split water (photolysis), releasing free oxygen and producing ATP/NADPH energy blocks.</li>
        <li><b>Calvin Cycle (Dark Reactions):</b> Operates inside the chloroplast stroma. Fixes carbon dioxide (CO₂) using ATP/NADPH variables to synthesize high-energy G3P glucose precursors.</li>
      </ul>
    `,
    flashcards: [
      { front: "What is the primary chemical equation of photosynthesis?", back: "6CO₂ + 6H₂O + light → C₆H₁₂O₆ + 6O₂" },
      { front: "Where do the Light Reactions take place?", back: "Inside the thylakoid membranes of the chloroplasts." },
      { front: "What organic molecule is finalized in the Calvin Cycle?", back: "Glucose (via G3P carbohydrate precursors)." },
      { front: "What role does chlorophyll fill in photolysis?", back: "Traps sunlight photons to release high-energy electrons that split water." }
    ]
  },
  quantum: {
    title: "Quantum States Notes",
    content: `
      <h3>⚛️ Microscopic Wave Mechanics</h3>
      <p>Classical physical laws break down at sub-atomic dimensions, requiring wave-particle equations to interpret state probabilities.</p>
      <p><b>Core Mechanics:</b></p>
      <ul>
        <li><b>Wave-Particle Duality:</b> Photons and electrons display characteristics of both localized particles and wave propagation fields.</li>
        <li><b>Schrödinger Equation:</b> Describes probability wave functions where particles do not have exact coordinates, but rather probability clouds of orbital density.</li>
        <li><b>Superposition:</b> A quantum particle is suspended across all potential pathways simultaneously until an measurement collapses the wave state.</li>
      </ul>
    `,
    flashcards: [
      { front: "What is Wave-Particle Duality?", back: "The concept that matter (like electrons) behaves as both a physical particle and wave propagation." },
      { front: "Define Quantum Superposition.", back: "A system remains in multiple potential states simultaneously until collapsed by measurement." },
      { front: "What does the Schrödinger Equation determine?", back: "The probability distribution clouds of an electron's energy orbit." }
    ]
  },
  heart: {
    title: "Human Heart Notes",
    content: `
      <h3>❤️ Cardiac Muscular Circulation</h3>
      <p>The human heart is a highly specialized 4-chamber pump that drives double circulatory pathways to support metabolism.</p>
      <p><b>Flow Dynamics:</b></p>
      <ul>
        <li><b>Right Side (Pulmonary):</b> Vena Cava return → Right Atrium → Tricuspid Valve → Right Ventricle → Pulmonary Arteries → Lungs (oxygen absorption).</li>
        <li><b>Left Side (Systemic):</b> Pulmonary Veins → Left Atrium → Bicuspid/Mitral Valve → Left Ventricle → Systolic Ejection → Aorta Arch → Systemic Arteries.</li>
        <li><b>Pacemaker Regulation:</b> The Sinoatrial (SA) node sends rapid electric depolarization vectors across the myocardium, scheduling synchronous contractions.</li>
      </ul>
    `,
    flashcards: [
      { front: "Which heart chamber has the thickest muscle wall?", back: "The Left Ventricle (to generate systemic systolic pressures)." },
      { front: "What role does the SA Node represent?", back: "Acts as the natural pacemaker, driving cardiodepolarization waves." },
      { front: "Contrast pulmonary vs systemic pathways.", back: "Pulmonary moves deoxygenated blood to lungs; systemic pumps oxygenated blood to the body." }
    ]
  },
  dna: {
    title: "DNA Double Helix Notes",
    content: `
      <h3>🧬 Nucleic Acid Replication</h3>
      <p>Deoxyribonucleic acid (DNA) is the molecular blueprint of living organisms, structured as a twisted double helix strands.</p>
      <p><b>Structural Constants:</b></p>
      <ul>
        <li><b>Anti-Parallel Strands:</b> Polynucleotide rails run opposingly (5' to 3' and 3' to 5') bound by hydrogen linkages.</li>
        <li><b>Complementary Base Pairs:</b> Adenine-Thymine (A-T) forms two hydrogen bonds; Guanine-Cytosine (G-C) forms three hydrogen bonds.</li>
        <li><b>Backbone Chemistry:</b> Sugars (deoxyribose) and negative phosphate groups provide water-soluble polar backbones, exposing genetic base codes inward.</li>
      </ul>
    `,
    flashcards: [
      { front: "What bonds hold base pairs together?", back: "Weak hydrogen bonds (2 between A-T; 3 between G-C)." },
      { front: "Explain Anti-Parallel strands.", back: "Opposing structural orientation of the twin phosphate rails (5' to 3' vs 3' to 5')." },
      { front: "Which base pairing has higher melting stability?", back: "G-C base pairing due to three hydrogen linkages." }
    ]
  }
};

function pickNotes(key, btn) {
  document.querySelectorAll('#pane-notes .topic-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  
  const notes = notesData[key];
  if (!notes) return;
  
  const contentDiv = document.getElementById('notesContent');
  contentDiv.innerHTML = `
    <h3>${notes.title}</h3>
    ${notes.content}
    <button class="btn btn-sm" style="margin-top:20px" onclick="downloadNotesText('${key}')">
      <i class="fas fa-download"></i> Download Study Sheet
    </button>
  `;
  
  // Set Flashcards
  flashcardsDeck = notes.flashcards || [];
  currentFlashcardIndex = 0;
  renderFlashcard();
}

async function searchNotesTopic() {
  const query = document.getElementById('notesSearchInput').value.trim();
  if (!query) {
    alert("Please enter a topic to search.");
    return;
  }
  
  const contentDiv = document.getElementById('notesContent');
  const lowerTopic = query.toLowerCase();
  
  let matchedKey = null;
  if (lowerTopic.includes('photosynthesis')) matchedKey = 'photosynthesis';
  else if (lowerTopic.includes('quantum')) matchedKey = 'quantum';
  else if (lowerTopic.includes('heart')) matchedKey = 'heart';
  else if (lowerTopic.includes('dna') || lowerTopic.includes('helix')) matchedKey = 'dna';
  
  if (matchedKey) {
    pickNotes(matchedKey, null);
  } else {
    // Dynamically generate the notes and flashcards using AI!
    contentDiv.innerHTML = `
      <div style="text-align:center;color:var(--muted);padding:40px">
        <i class="fas fa-spinner fa-spin" style="font-size:48px;color:var(--primary);margin-bottom:16px"></i>
        <p>AI is compiling custom study notes and flashcards for "${query}"...</p>
        <p style="font-size:12px;color:var(--muted);margin-top:6px">Structuring key concepts and composing active card deck...</p>
      </div>
    `;
    
    // Clear flashcards container temporarily
    document.getElementById('fcFront').textContent = "Generating...";
    document.getElementById('fcBack').textContent = "Generating...";
    document.getElementById('deckInfo').textContent = "0 / 0";
    
    try {
      const res = await fetch(API_BASE + '/generate-notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: query,
          model_provider: selectedModel || 'auto'
        })
      });
      
      if (!res.ok) throw new Error("AI Generation failed");
      const data = await res.json();
      
      if (data.fallback_key) {
        pickNotes(data.fallback_key, null);
        return;
      }
      
      contentDiv.innerHTML = `
        <h3>${data.title}</h3>
        ${data.content}
        <button class="btn btn-sm" style="margin-top:20px" onclick="downloadNotesText()">
          <i class="fas fa-download"></i> Download Study Sheet
        </button>
      `;
      
      flashcardsDeck = data.flashcards || [];
      currentFlashcardIndex = 0;
      renderFlashcard();
      
    } catch (e) {
      console.error(e);
      contentDiv.innerHTML = `
        <div style="text-align:center;color:var(--danger);padding:40px">
          <i class="fas fa-exclamation-triangle" style="font-size:48px;margin-bottom:16px"></i>
          <p>Failed to generate study notes using AI: ${e.message}</p>
          <p style="font-size:12px;color:var(--muted);margin-top:8px">Please check if your AI model provider or local Ollama is fully running.</p>
        </div>
      `;
      document.getElementById('fcFront').textContent = "Error";
      document.getElementById('fcBack').textContent = "Error generating flashcards.";
      document.getElementById('deckInfo').textContent = "0 / 0";
    }
  }
}

function renderFlashcard() {
  const container = document.getElementById('flashcardContainer');
  if (!container) return;
  
  container.classList.remove('flipped');
  
  const front = document.getElementById('fcFront');
  const back = document.getElementById('fcBack');
  const deckInfo = document.getElementById('deckInfo');
  
  if (flashcardsDeck.length > 0) {
    front.textContent = flashcardsDeck[currentFlashcardIndex].front;
    back.textContent = flashcardsDeck[currentFlashcardIndex].back;
    deckInfo.textContent = `${currentFlashcardIndex + 1} / ${flashcardsDeck.length}`;
  } else {
    front.textContent = "No flashcards active.";
    back.textContent = "Select notes topic.";
    deckInfo.textContent = "0 / 0";
  }
}

function flipFlashcard() {
  const container = document.getElementById('flashcardContainer');
  if (container) {
    container.classList.toggle('flipped');
  }
}

function nextFlashcard() {
  if (flashcardsDeck.length === 0) return;
  currentFlashcardIndex = (currentFlashcardIndex + 1) % flashcardsDeck.length;
  renderFlashcard();
}

function prevFlashcard() {
  if (flashcardsDeck.length === 0) return;
  currentFlashcardIndex = (currentFlashcardIndex - 1 + flashcardsDeck.length) % flashcardsDeck.length;
  renderFlashcard();
}

function downloadNotesText(key) {
  let title = "";
  let content = "";
  
  if (key && notesData[key]) {
    const notes = notesData[key];
    title = notes.title;
    content = notes.content;
  } else {
    // Read from the active DOM element for dynamically generated notes
    const contentDiv = document.getElementById('notesContent');
    const h3El = contentDiv.querySelector('h3');
    title = h3El ? h3El.textContent : "Study Notes";
    
    const tempDiv = document.createElement("div");
    tempDiv.innerHTML = contentDiv.innerHTML;
    const btn = tempDiv.querySelector('button');
    if (btn) btn.remove();
    const h3 = tempDiv.querySelector('h3');
    if (h3) h3.remove();
    content = tempDiv.innerHTML;
  }
  
  // Convert HTML notes content into plain clean text
  const temp = document.createElement("div");
  temp.innerHTML = content;
  const cleanText = `${title}\n\n${temp.innerText || temp.textContent}`;
  
  const blob = new Blob([cleanText], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${title.toLowerCase().replace(/\s+/g, '_')}_study_notes.txt`;
  a.click();
}

// ===== SMART INTERACTIVE QUIZZES =====
async function startQuiz(subject) {
  activeQuizSubject = subject;
  currentQuizIndex = 0;
  quizScore = 0;
  hasSubmittedAnswer = false;
  
  try {
    const res = await fetch(API_BASE + `/quiz/ai/${encodeURIComponent(subject)}`);
    if (!res.ok) throw new Error("AI quiz API load failed");
    const data = await res.json();
    
    activeQuizQuestions = data.questions || [];
    if (activeQuizQuestions.length === 0) {
      alert("No questions found for " + subject);
      return;
    }
    
    // Show Quiz Panel inside Assignments
    document.getElementById('assignmentsList').style.display = 'none';
    document.getElementById('quizPane').style.display = 'block';
    
    loadQuizQuestion();
  } catch (e) {
    alert("Could not load quiz questions: " + e.message);
  }
}

function loadQuizQuestion() {
  hasSubmittedAnswer = false;
  selectedOptionIndex = null;
  
  const question = activeQuizQuestions[currentQuizIndex];
  document.getElementById('quizQuestion').textContent = question.question;
  
  // Update progress fill bar
  const progressPercent = ((currentQuizIndex) / activeQuizQuestions.length) * 100;
  document.getElementById('quizProgress').style.style = `width: ${progressPercent}%`;
  document.getElementById('quizProgress').style.width = `${progressPercent}%`;
  document.getElementById('quizProgressText').textContent = `Q ${currentQuizIndex + 1} / ${activeQuizQuestions.length}`;
  
  // Hide feedback & hint blocks
  document.getElementById('quizFeedback').classList.add('hidden');
  document.getElementById('quizHintDisplay').innerHTML = '';
  
  const optionsContainer = document.getElementById('quizOptions');
  optionsContainer.innerHTML = '';
  
  question.options.forEach((opt, idx) => {
    const optLetter = String.fromCharCode(65 + idx); // A, B, C, D
    const btn = document.createElement('button');
    btn.className = 'quiz-option';
    btn.innerHTML = `<span class="quiz-opt-letter">${optLetter}</span> ${opt}`;
    btn.onclick = () => selectQuizOption(idx, btn);
    optionsContainer.appendChild(btn);
  });
  
  const nextBtn = document.getElementById('quizNextBtn');
  nextBtn.innerHTML = `Submit Answer <i class="fas fa-check"></i>`;
}

function selectQuizOption(idx, element) {
  if (hasSubmittedAnswer) return;
  
  selectedOptionIndex = idx;
  document.querySelectorAll('.quiz-option').forEach(el => el.classList.remove('selected'));
  element.classList.add('selected');
}

async function getQuizHint() {
  if (hasSubmittedAnswer) return;
  const question = activeQuizQuestions[currentQuizIndex];
  
  try {
    const res = await fetch(API_BASE + `/hint/${question.question_id}`);
    const data = await res.json();
    
    const display = document.getElementById('quizHintDisplay');
    display.innerHTML = `
      <div class="hint-tooltip">
        <i class="fas fa-lightbulb"></i> <b>Hint:</b> ${data.hint}
      </div>
    `;
  } catch (e) {
    console.error("Hint retrieve failed", e);
  }
}

async function nextQuizQuestion() {
  const nextBtn = document.getElementById('quizNextBtn');
  const question = activeQuizQuestions[currentQuizIndex];
  
  // Phase 1: Submit check
  if (!hasSubmittedAnswer) {
    if (selectedOptionIndex === null) {
      alert("Please select an option first.");
      return;
    }
    
    hasSubmittedAnswer = true;
    const isCorrect = selectedOptionIndex === question.correct_answer;
    if (isCorrect) quizScore++;
    
    // Play splash visual overlay flash
    triggerQuizSplash(isCorrect);
    
    // Highlight options green/red
    const options = document.querySelectorAll('.quiz-option');
    options.forEach((btn, idx) => {
      btn.onclick = null; // deactivate clicks
      if (idx === question.correct_answer) {
        btn.classList.add('correct');
      } else if (idx === selectedOptionIndex) {
        btn.classList.add('incorrect');
      }
    });
    
    // Load explanation details
    const feedback = document.getElementById('quizFeedback');
    const ftitle = document.getElementById('quizFeedbackTitle');
    const fdesc = document.getElementById('quizFeedbackDesc');
    
    feedback.classList.remove('hidden');
    ftitle.textContent = isCorrect ? "🎉 Correct!" : "❌ Incorrect";
    ftitle.style.color = isCorrect ? "var(--success)" : "var(--danger)";
    fdesc.textContent = question.explanation;
    
    // Submit progress vector to backend
    if (student) {
      try {
        await fetch(API_BASE + '/quiz/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: student.id,
            subject: activeQuizSubject,
            question_id: question.question_id,
            selected_answer: selectedOptionIndex
          })
        });
      } catch (e) {
        console.error("Quiz result submit failed", e);
      }
    }
    
    // Change button text
    if (currentQuizIndex === activeQuizQuestions.length - 1) {
      nextBtn.innerHTML = `Finish Quiz <i class="fas fa-flag-checkered"></i>`;
    } else {
      nextBtn.innerHTML = `Next Question <i class="fas fa-chevron-right"></i>`;
    }
    
    return;
  }
  
  // Phase 2: Next step loading
  currentQuizIndex++;
  if (currentQuizIndex < activeQuizQuestions.length) {
    loadQuizQuestion();
  } else {
    // Show Final score Splash inside question card
    showQuizScoreSummary();
  }
}

function triggerQuizSplash(isCorrect) {
  const splash = document.getElementById('quizSplash');
  splash.className = `quiz-splash active ${isCorrect ? 'correct' : 'incorrect'}`;
  setTimeout(() => {
    splash.classList.remove('active');
  }, 600);
}

function showQuizScoreSummary() {
  document.getElementById('quizProgress').style.style = `width: 100%`;
  document.getElementById('quizProgress').style.width = '100%';
  document.getElementById('quizProgressText').textContent = "Complete!";
  
  const optionsContainer = document.getElementById('quizOptions');
  optionsContainer.innerHTML = '';
  document.getElementById('quizFeedback').classList.add('hidden');
  document.getElementById('quizHintDisplay').innerHTML = '';
  
  const pct = Math.round((quizScore / activeQuizQuestions.length) * 100);
  
  document.getElementById('quizQuestion').innerHTML = `
    <div style="text-align:center;padding:20px">
      <div style="font-size:64px;margin-bottom:16px">🏆</div>
      <h3 style="font-size:24px;font-weight:800;color:#fff;margin-bottom:8px">Quiz Completed!</h3>
      <p style="color:var(--muted);font-size:15px;margin-bottom:20px">You scored ${quizScore} out of ${activeQuizQuestions.length} correct</p>
      
      <div style="width:120px;height:120px;border-radius:50%;background:rgba(99,102,241,0.1);border:3px solid var(--primary);display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:900;margin:0 auto 24px;color:var(--primary)">
        ${pct}%
      </div>
      
      <button class="btn btn-block" onclick="exitQuiz()">Exit to Assignments</button>
    </div>
  `;
  
  document.getElementById('quizNextBtn').style.display = 'none';
  
  // Submit complete progress event to backend
  if (student) {
    const progressPayload = {
      topic: `${activeQuizSubject.toUpperCase()} Topic Mastery`,
      score: quizScore / activeQuizQuestions.length,
      completed: true,
      notes: `Scored ${pct}% on active subject quiz.`
    };
    
    fetch(API_BASE + `/students/${student.id}/progress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(progressPayload)
    }).then(res => res.json()).then(d => {
      console.log("Completed progress saved:", d);
    }).catch(e => console.error(e));
  }
}

function exitQuiz() {
  document.getElementById('assignmentsList').style.display = 'grid';
  document.getElementById('quizPane').style.display = 'none';
  document.getElementById('quizNextBtn').style.display = 'inline-flex';
}

// ===== DYNAMIC ASSIGNMENTS =====
async function loadAssignmentsList() {
  const cls = student ? student.class : '10th';
  const list = document.getElementById('assignmentsList');
  if (!list) return;
  list.innerHTML = '<div style="color:var(--muted)">Loading curriculum tasks...</div>';
  
  try {
    const res = await fetch(API_BASE + `/assignments/${encodeURIComponent(cls)}`);
    const data = await res.json();
    assignmentItems = data.assignments || [];
    renderAssignmentsList(assignmentItems);
  } catch (e) {
    list.innerHTML = '<div style="color:var(--danger)">Could not load assignments from API server.</div>';
  }
}

function renderAssignmentsList(items) {
  const list = document.getElementById('assignmentsList');
  if (!list) return;
  list.innerHTML = '';
  if (items && items.length > 0) {
    items.forEach(a => {
      const div = document.createElement('div');
      div.className = 'card';
      div.innerHTML = `
        <div style="display:flex;justify-content:between;align-items:start;width:100%">
          <div style="flex:1">
            <span class="rec-tag" style="background:rgba(99, 102, 241, 0.15);color:#a5b4fc">${a.subject.toUpperCase()} • ${a.total_marks} Marks</span>
            <div class="card-title" style="font-size:17px;margin-top:6px">${a.title}</div>
            <div class="card-sub" style="margin-top:4px">${a.description}</div>
            <div class="card-sub" style="margin-top:8px;color:#a5b4fc"><i class="fas fa-calendar-alt"></i> Due: ${a.due_date}</div>
            <div style="margin-top:10px;font-size:13px;color:var(--muted)">AI generates the quiz questions in real time.</div>
          </div>
          <button class="btn btn-sm" onclick="startQuiz('${a.subject}')">AI Quiz <i class="fas fa-arrow-right"></i></button>
        </div>
      `;
      list.appendChild(div);
    });
  } else {
    list.innerHTML = `
      <div class="card" style="text-align:center;padding:24px">
        <p style="color:var(--muted)">No matching assignments were found. Try a different topic or keyword.</p>
        <div style="display:flex;gap:12px;justify-content:center;margin-top:16px">
          <button class="btn btn-sm" onclick="loadAssignmentsList()">Reload Assignments</button>
        </div>
      </div>
    `;
  }
}

async function searchAssignmentsTopic() {
  const query = document.getElementById('assignmentsSearchInput').value.trim();
  const list = document.getElementById('assignmentsList');
  const status = document.getElementById('assignmentsSearchStatus');
  const modelSelect = document.getElementById('assignmentModelSelect');
  const modelNameInput = document.getElementById('assignmentModelName');
  if (!list || !status) return;

  const modelProvider = modelSelect?.value || selectedModel;
  const modelName = modelNameInput?.value.trim() || '';
  const normalizedQuery = query.toLowerCase();
  const results = assignmentItems.filter(a => {
    return [a.title, a.description, a.subject, a.class_grade].some(value =>
      String(value || '').toLowerCase().includes(normalizedQuery)
    );
  });

  status.textContent = query
    ? `Showing ${results.length} result(s) for "${query}" using ${modelProvider === 'auto' ? 'Auto' : modelProvider.toUpperCase()}${modelName ? ` (${modelName})` : ''}`
    : 'Showing all assignments for your class.';

  renderAssignmentsList(results.length ? results : []);

  if (!query) return;

  const aiCard = document.createElement('div');
  aiCard.className = 'card';
  aiCard.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div>
        <div class="card-title" style="font-size:16px;margin-bottom:8px">🤖 AI Search Summary</div>
        <div style="color:var(--muted);font-size:13px">Using ${modelProvider === 'auto' ? 'Auto model selection' : modelProvider.toUpperCase()}${modelName ? ` (${modelName})` : ''}</div>
      </div>
      <div style="font-size:12px;color:var(--muted);">Query: ${query}</div>
    </div>
    <div id="assignmentsAiSearchResult" style="margin-top:18px;color:var(--muted);">Generating AI match guidance...</div>
  `;
  list.appendChild(aiCard);

  try {
    const res = await fetch(API_BASE + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Search the assignment tasks list for topic: ${query}. Explain which tasks are most relevant and what study focus is best for this topic.`,
        language: 'english',
        subject: 'assignments',
        model_provider: modelProvider !== 'auto' ? modelProvider : undefined,
        model_name: modelName || undefined
      })
    });
    const data = await res.json();
    const resultDiv = document.getElementById('assignmentsAiSearchResult');
    if (!res.ok) {
      resultDiv.innerHTML = `<span style="color:var(--danger)">AI search failed: ${data.detail || 'Unknown error'}</span>`;
      return;
    }
    resultDiv.innerHTML = data.reply.replace(/\n/g, '<br>');
  } catch (error) {
    const resultDiv = document.getElementById('assignmentsAiSearchResult');
    if (resultDiv) {
      resultDiv.innerHTML = `<span style="color:var(--danger)">AI search failed: ${error.message}</span>`;
    }
  }
}

// ===== POMODORO TIMER WORKSPACE =====
function toggleTimer() {
  const btn = document.getElementById('timerStartBtn');
  
  if (isTimerRunning) {
    // Pause
    clearInterval(timerInterval);
    isTimerRunning = false;
    btn.textContent = 'Resume Focus';
    btn.classList.remove('btn-outline');
  } else {
    // Start
    isTimerRunning = true;
    btn.textContent = 'Pause Focus';
    btn.classList.add('btn-outline');
    
    timerInterval = setInterval(() => {
      timerTimeLeft--;
      updateTimerDisplay();
      
      if (timerTimeLeft <= 0) {
        clearInterval(timerInterval);
        isTimerRunning = false;
        btn.textContent = 'Start Focus';
        btn.classList.remove('btn-outline');
        timerTimeLeft = 25 * 60; // reset
        
        // Play final synth success sound wave alert
        playFocusBeep();
        alert("🎉 Focus session completed! Take a short break.");
      }
    }, 1000);
  }
}

function resetTimer() {
  clearInterval(timerInterval);
  isTimerRunning = false;
  timerTimeLeft = 25 * 60;
  updateTimerDisplay();
  
  const btn = document.getElementById('timerStartBtn');
  btn.textContent = 'Start Focus';
  btn.classList.remove('btn-outline');
}

function updateTimerDisplay() {
  const minutes = Math.floor(timerTimeLeft / 60);
  const seconds = timerTimeLeft % 60;
  
  const timeStr = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  document.getElementById('timerTime').textContent = timeStr;
  
  // Progress SVG offset updates
  const circle = document.getElementById('timerFill');
  const dashArray = 440;
  const pct = timerTimeLeft / timerTotal;
  circle.style.strokeDashoffset = dashArray - (dashArray * pct);
}

function resetTimerCircle() {
  const circle = document.getElementById('timerFill');
  if (circle) circle.style.strokeDashoffset = 440;
}

function playFocusBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    
    osc.connect(gain);
    gain.connect(ctx.destination);
    
    osc.type = 'sine';
    osc.frequency.value = 880; // A5 pitch note
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    
    osc.start();
    osc.stop(ctx.currentTime + 0.4); // brief beep
  } catch (e) {
    console.error("Beep synth not supported", e);
  }
}

// ===== PERSISTENT STUDY NOTEPAD =====
function initNotepad() {
  const savedText = localStorage.getItem('atis_notepad') || '';
  document.getElementById('notepadArea').value = savedText;
}

function saveNotepad() {
  const text = document.getElementById('notepadArea').value;
  localStorage.setItem('atis_notepad', text);
  
  const status = document.getElementById('notepadStatus');
  status.textContent = "Saving changes...";
  setTimeout(() => {
    status.textContent = "Saved locally";
  }, 800);
}

// ===== CURRICULUM MIND MAP UTILITY =====
const mindmapData = {
  photosynthesis: {
    root: "🌿 Photosynthesis Process",
    nodes: [
      "Light Harvesting (Sunlight)",
      "Photolysis of Water (splits H2O -> O2)",
      "ATP & NADPH formation inside Thylakoids",
      "Calvin Cycle Stroma Reaction",
      "G3P Carbohydrate Synthesis"
    ]
  },
  quantum: {
    root: "⚛️ Quantum Wave Mechanics",
    nodes: [
      "Wave-Particle Duality equations",
      "Schrödinger Probability Clouds",
      "Superposition state suspension",
      "Wave function collapse on observation",
      "Action Entanglement vectors"
    ]
  },
  heart: {
    root: "❤️ Cardiac Circulatory Pump",
    nodes: [
      "Right Atrium Vena return",
      "Right Ventricle pulmonary drive",
      "Left Ventricle thick systemic shell",
      "High-pressure Aorta Ejection",
      "Sinoatrial depolarizing vector"
    ]
  },
  dna: {
    root: "🧬 Genetic Double Helix",
    nodes: [
      "Anti-parallel Sugar-phosphate rails",
      "Adenine-Thymine Double H-bonds",
      "Guanine-Cytosine Triple H-bonds",
      "Regulatory Major Groove docking",
      "Polar protective outer backbone"
    ]
  }
};

function updateMindMap(topicKey) {
  const container = document.getElementById('mindmapOutline');
  if (!container) return;
  
  const m = mindmapData[topicKey];
  if (!m) {
    container.innerHTML = `
      <div class="mindmap-node root">Study Center</div>
      <ul><li class="mindmap-node">Select a topic in Diagrams or Notes to parse.</li></ul>
    `;
    return;
  }
  
  let listItems = m.nodes.map(n => `<li class="mindmap-node">${n}</li>`).join('');
  container.innerHTML = `
    <div class="mindmap-node root">${m.root}</div>
    <ul>${listItems}</ul>
  `;
}

// ===== SYSTEM UTILITIES =====
function exportStudentData() {
  if (!student) {
    alert("Please register a student first.");
    return;
  }
  const blob = new Blob([JSON.stringify({ student, chatCount, streakCount, savedNotes: localStorage.getItem('atis_notepad') }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `atis_student_profile_${student.id}.json`;
  a.click();
}

// ===== SETTINGS MODULE =====
function saveModel() {
  selectedModel = document.getElementById('modelSelect').value;
  selectedModelName = document.getElementById('modelName').value.trim();
  localStorage.setItem('atis_model', selectedModel);
  localStorage.setItem('atis_modelName', selectedModelName);
  
  const kpiEl = document.getElementById('kpiModel');
  if (kpiEl) kpiEl.textContent = selectedModel === 'auto' ? 'Auto' : selectedModel.toUpperCase();
}

async function searchDiagramTopic() {
  const input = document.getElementById('diagramSearchInput');
  const query = input.value.trim();
  if (!query) {
    alert("Please enter a topic to search.");
    return;
  }
  
  const viewport = document.getElementById('diagramViewport');
  const lowerTopic = query.toLowerCase();
  
  let matchedKey = null;
  if (lowerTopic.includes('photosynthesis')) matchedKey = 'photosynthesis';
  else if (lowerTopic.includes('quantum')) matchedKey = 'quantum';
  else if (lowerTopic.includes('heart')) matchedKey = 'heart';
  else if (lowerTopic.includes('dna') || lowerTopic.includes('helix')) matchedKey = 'dna';
  else if (lowerTopic.includes('newton') || lowerTopic.includes('second law')) matchedKey = 'newton';
  else if (lowerTopic.includes('atom') || lowerTopic.includes('electron') || lowerTopic.includes('orbital') || lowerTopic.includes('nucleus')) matchedKey = 'atom';
  else if (lowerTopic.includes('water') || lowerTopic.includes('rain') || lowerTopic.includes('cycle') || lowerTopic.includes('evaporat')) matchedKey = 'water';
  else if (lowerTopic.includes('cell') || lowerTopic.includes('organelle')) matchedKey = 'cell';
  else if (lowerTopic.includes('volcano') || lowerTopic.includes('magma') || lowerTopic.includes('lava')) matchedKey = 'volcano';
  else if (lowerTopic.includes('mitosis') || lowerTopic.includes('division') || lowerTopic.includes('split')) matchedKey = 'mitosis';
  
  if (matchedKey) {
    // Render the premium prebuilt scientific diagram instantly!
    pickTopic(matchedKey, null);
  } else {
    // Dynamically generate the scientific diagram using AI!
    viewport.innerHTML = `
      <div style="text-align:center;color:var(--muted);padding:40px">
        <i class="fas fa-spinner fa-spin" style="font-size:48px;color:var(--primary);margin-bottom:16px"></i>
        <p>AI is generating custom scientific diagram for "${query}"...</p>
        <p style="font-size:12px;color:var(--muted);margin-top:6px">Writing vector paths and placing active coordinate hotspots...</p>
      </div>
    `;
    
    document.getElementById('diagramTitle').textContent = query.toUpperCase();
    document.getElementById('diagramDesc').innerHTML = `<i>Querying AI diagram generator. Please wait...</i>`;
    
    const controlsContainer = document.getElementById('simControlsContainer');
    controlsContainer.innerHTML = '';
    
    try {
      const res = await fetch(API_BASE + '/generate-diagram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: query,
          model_provider: selectedModel || 'auto'
        })
      });
      
      if (!res.ok) throw new Error("AI Generation failed");
      const data = await res.json();
      
      if (data.fallback_key) {
        // AI decided to route to one of our prebuilt templates
        pickTopic(data.fallback_key, null);
        return;
      }
      
      // Load custom SVG and tooltip wrapper
      document.getElementById('diagramTitle').textContent = data.title;
      document.getElementById('diagramDesc').innerHTML = `<p style="white-space: pre-wrap;">${data.desc}</p>`;
      
      viewport.innerHTML = data.svg + `
        <div class="diagram-tooltip hidden" id="diagramTooltip">
          <h4 id="tooltipTitle">Hotspot Info</h4>
          <p id="tooltipDesc">Details...</p>
        </div>
      `;
      
      const svgEl = viewport.querySelector('svg') || document.getElementById('dynamicSvg');
      if (svgEl && data.coords && data.coords.length > 0) {
        data.coords.forEach(coord => {
          const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
          g.setAttribute("class", "hotspot");
          g.addEventListener('mouseenter', (e) => showHotspotTooltip(coord, e));
          g.addEventListener('mouseleave', hideHotspotTooltip);
          g.addEventListener('click', () => {
            const chatInput = document.getElementById('chatInput');
            if (chatInput) {
              chatInput.value = `Explain the significance of "${coord.name.replace(/^\d+\.\s+/, '')}" in ${data.title} in pointwise format.`;
              sendChat(data.title);
            }
          });
          
          const pulse = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          pulse.setAttribute("cx", coord.cx);
          pulse.setAttribute("cy", coord.cy);
          pulse.setAttribute("r", coord.r + 6);
          pulse.setAttribute("fill", "rgba(99,102,241,0.2)");
          pulse.setAttribute("class", "hotspot-pulse");
          
          const core = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          core.setAttribute("cx", coord.cx);
          core.setAttribute("cy", coord.cy);
          core.setAttribute("r", coord.r);
          core.setAttribute("fill", "#6366f1");
          core.setAttribute("stroke", "#fff");
          core.setAttribute("stroke-width", "2");
          
          const txt = document.createElementNS("http://www.w3.org/2000/svg", "text");
          txt.setAttribute("x", coord.cx);
          txt.setAttribute("y", coord.cy - 14);
          txt.setAttribute("fill", "var(--muted)");
          txt.setAttribute("font-size", "8");
          txt.setAttribute("font-weight", "700");
          txt.setAttribute("text-anchor", "middle");
          const nameStr = coord.name || '';
          txt.textContent = nameStr.includes('. ') ? nameStr.split('. ')[1] : nameStr;
          
          g.appendChild(pulse);
          g.appendChild(core);
          g.appendChild(txt);
          svgEl.appendChild(g);
        });
      }
    } catch (e) {
      console.error(e);
      viewport.innerHTML = `
        <div style="text-align:center;color:var(--danger);padding:40px">
          <i class="fas fa-exclamation-triangle" style="font-size:48px;margin-bottom:16px"></i>
          <p>Failed to generate scientific diagram using AI: ${e.message}</p>
          <p style="font-size:12px;color:var(--muted);margin-top:8px">Please check if your AI model provider or local Ollama is fully running.</p>
        </div>
      `;
      document.getElementById('diagramTitle').textContent = "Generation Error";
      document.getElementById('diagramDesc').textContent = `⚠️ Error generating dynamic AI diagram: ${e.message}`;
    }
  }
}


