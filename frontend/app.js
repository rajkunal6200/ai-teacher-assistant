// ===== STATE =====
let student = JSON.parse(localStorage.getItem('atis_student') || 'null');
let chatCount = parseInt(localStorage.getItem('atis_chats') || '0');
let selectedModel = localStorage.getItem('atis_model') || 'auto';
let selectedModelName = localStorage.getItem('atis_modelName') || '';

const API_BASE = (() => {
  const saved = (localStorage.getItem('apiBaseUrl') || '').trim().replace(/\/$/,'');
  if (saved) return saved;
  const devPorts = ['3000','8004','5173','5500'];
  if (devPorts.includes(location.port) || location.protocol === 'file:')
    return 'http://' + (location.hostname || '127.0.0.1') + ':8003';
  return location.origin;
})();

// ===== SCREEN MANAGEMENT =====
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.add('hidden'));
  const target = document.getElementById(id);
  if (target) target.classList.remove('hidden');
  if (id === 'register') {
    document.getElementById('welcome').classList.add('hidden');
  }
}

// ===== REGISTRATION =====
function doRegister() {
  const name = document.getElementById('regName').value.trim();
  const school = document.getElementById('regSchool').value.trim();
  const cls = document.getElementById('regClass').value;
  const mobile = document.getElementById('regMobile').value.trim();
  const email = document.getElementById('regEmail').value.trim();

  if (!name || !school || !cls || !mobile) {
    alert('Please fill all required fields.');
    return;
  }
  if (mobile.length !== 10) {
    alert('Mobile number must be 10 digits.');
    return;
  }

  student = {
    id: 'STU' + Math.floor(Math.random() * 90000 + 10000),
    name, school, class: cls, mobile, email,
    registered: Date.now()
  };
  localStorage.setItem('atis_student', JSON.stringify(student));
  enterApp();
}

function enterApp() {
  // Hide welcome & register screens
  document.querySelectorAll('.screen').forEach(s => s.classList.add('hidden'));
  // Show app
  const app = document.getElementById('app');
  app.classList.add('visible');
  // Update UI
  document.getElementById('topbarUser').textContent = student.name + ' • ' + student.id;
  document.getElementById('kpiClass').textContent = student.class;
  document.getElementById('kpiChats').textContent = chatCount;
  // Restore model
  const ms = document.getElementById('modelSelect');
  if (ms) ms.value = selectedModel;
  const mn = document.getElementById('modelName');
  if (mn) mn.value = selectedModelName;
}

function doLogout() {
  if (!confirm('Logout and clear your profile?')) return;
  localStorage.removeItem('atis_student');
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

async function sendChat() {
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  addMsg(msg, 'user');
  chatCount++;
  localStorage.setItem('atis_chats', chatCount);
  document.getElementById('kpiChats').textContent = chatCount;

  try {
    const res = await fetch(API_BASE + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: msg,
        language: 'english',
        student_id: student ? student.id : null,
        subject: 'general',
        model_provider: selectedModel !== 'auto' ? selectedModel : undefined,
        model_name: selectedModelName || undefined
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    addMsg(data.reply, 'ai');
  } catch (e) {
    addMsg('⚠️ ' + (e.message || 'Could not reach the AI server.'), 'ai');
  }
}

function clearChat() {
  document.getElementById('chatBox').innerHTML = '<div class="msg ai">👋 Chat cleared. Ask me anything!</div>';
}

// ===== TOPICS =====
const topicData = {
  photosynthesis: { icon: '🌿', title: 'Photosynthesis', desc: 'Light energy → Chemical energy via chlorophyll in chloroplasts. Equation: 6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂. Two stages: Light reactions and Calvin cycle.' },
  quantum: { icon: '⚛️', title: 'Quantum Mechanics', desc: 'Wave-particle duality, Heisenberg uncertainty principle, Schrödinger equation. Particles exist in probability clouds until measured.' },
  heart: { icon: '❤️', title: 'Human Heart', desc: 'Four-chambered organ: 2 atria + 2 ventricles. Pumps oxygenated blood via systemic circulation and deoxygenated via pulmonary circulation.' },
  dna: { icon: '🧬', title: 'DNA Structure', desc: 'Double helix with sugar-phosphate backbone. Base pairs: Adenine-Thymine, Guanine-Cytosine. Contains genetic instructions for all living organisms.' },
  newton: { icon: '⚡', title: "Newton's Laws", desc: '1st: Inertia. 2nd: F=ma. 3rd: Action-reaction pairs. Foundation of classical mechanics.' }
};

function pickTopic(key, btn) {
  document.querySelectorAll('#pane-diagrams .topic-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const t = topicData[key];
  if (!t) return;
  document.getElementById('diagramContent').innerHTML =
    '<div style="font-size:48px;margin-bottom:16px">' + t.icon + '</div>' +
    '<div class="card-title" style="font-size:18px;margin-bottom:12px">' + t.title + '</div>' +
    '<p style="color:var(--muted);line-height:1.8">' + t.desc + '</p>';
}

function pickNotes(key, btn) {
  document.querySelectorAll('#pane-notes .topic-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const t = topicData[key];
  if (!t) return;
  document.getElementById('notesContent').innerHTML =
    '<h3 style="margin-bottom:12px">' + t.icon + ' ' + t.title + '</h3>' +
    '<p style="color:var(--muted);line-height:2">' + t.desc + '</p>' +
    '<button class="btn" style="margin-top:16px;padding:8px 20px;font-size:13px" onclick="downloadNotes(\'' + key + '\')">⬇️ Download Notes</button>';
}

function downloadNotes(key) {
  const t = topicData[key];
  if (!t) return;
  const blob = new Blob([t.title + '\n\n' + t.desc], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = t.title.replace(/\s+/g, '_') + '_notes.txt';
  a.click();
}

// ===== VOICE =====
let recognition = null;
let isListening = false;

function toggleVoice() {
  const btn = document.getElementById('voiceBtn');
  const status = document.getElementById('voiceStatus');
  if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
    status.textContent = 'Voice not supported in this browser.';
    return;
  }
  if (isListening) {
    recognition.stop();
    isListening = false;
    btn.textContent = 'Start Listening';
    return;
  }
  recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
  recognition.lang = 'en-US';
  recognition.onresult = (e) => {
    const text = e.results[0][0].transcript;
    status.textContent = 'You said: ' + text;
    document.getElementById('chatInput').value = text;
    sendChat();
  };
  recognition.onerror = (e) => { status.textContent = 'Error: ' + e.error; };
  recognition.onend = () => { isListening = false; btn.textContent = 'Start Listening'; };
  recognition.start();
  isListening = true;
  btn.textContent = '⏹ Stop Listening';
  status.textContent = 'Listening...';
}

// ===== SETTINGS =====
function saveModel() {
  selectedModel = document.getElementById('modelSelect').value;
  selectedModelName = document.getElementById('modelName').value.trim();
  localStorage.setItem('atis_model', selectedModel);
  localStorage.setItem('atis_modelName', selectedModelName);
  document.getElementById('kpiModel').textContent = selectedModel === 'auto' ? 'Auto' : selectedModel;
  alert('Settings saved!');
}

// ===== INIT =====
window.addEventListener('DOMContentLoaded', () => {
  if (student) {
    enterApp();
  } else {
    showScreen('welcome');
  }
});
