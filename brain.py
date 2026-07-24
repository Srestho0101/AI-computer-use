#!/usr/bin/env python3
"""
JARVIS v2 – Robust brain.py with fallbacks and circuit breakers
"""

import os, sys, json, time, subprocess, threading, base64, re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from mistralai import Mistral

# ═══════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CycleRecord:
    cycle_number: int
    actions: List[Dict]
    fingerprint: str
    reasoning: str = ""
    expected_outcome: str = ""
    result: str = "unknown"
    pre_focus: Optional[Dict] = None
    post_focus: Optional[Dict] = None
    state_changed: bool = False
    tool_results: List[Dict] = field(default_factory=list)
    supervisor_directive: str = ""


@dataclass
class NavigationSettleState:
    pending: bool = False
    target: str = ""
    fingerprint: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    status: str = "idle"
    message: str = ""


@dataclass
class AgentState:
    objective: str
    initial_reasoning: str = ""
    action_history: List[Dict] = field(default_factory=list)
    tool_results: List[Dict] = field(default_factory=list)
    turn_count: int = 0
    cycle_count: int = 0
    consecutive_failures: int = 0
    consecutive_malformed: int = 0
    consecutive_same_tool: int = 0
    last_tool_name: str = ""
    task_phase: str = "planning"
    cycle_history: List[CycleRecord] = field(default_factory=list)
    repeated_action_count: int = 0
    last_action_fingerprint: str = ""
    last_review_cycle: int = 0
    latest_supervisor_directive: str = ""
    supervisor_warning: str = ""
    navigation: NavigationSettleState = field(default_factory=NavigationSettleState)


def normalize_actions(actions: List[Dict]) -> str:
    normalized = []
    for action in actions:
        kind = action.get("type", "")
        if kind == "type_text":
            text = re.sub(r"\s+", " ", action.get("text", "").strip().lower())
            normalized.append({"type": kind, "text": text})
        elif kind == "key_combo":
            normalized.append({"type": kind, "keys": action.get("keys", "").strip().lower()})
        elif kind == "wait":
            normalized.append({"type": kind, "duration": round(float(action.get("duration", 0)), 1)})
        else:
            normalized.append(action)
    return json.dumps(normalized, sort_keys=True)


def is_navigation_batch(actions: List[Dict]) -> Tuple[bool, str]:
    keys = [a.get("keys", "").lower().strip() for a in actions if a.get("type") == "key_combo"]
    texts = [a.get("text", "").strip() for a in actions if a.get("type") == "type_text"]
    has_location_focus = any(k in {"ctrl+l", "ctrl+t"} for k in keys)
    has_enter = "enter" in keys
    target = texts[-1] if texts else ""
    looks_like_url = bool(re.search(r"(^https?://)|([a-z0-9-]+\.[a-z]{2,})", target.lower()))
    return has_location_focus and has_enter and looks_like_url, target

# ═══════════════════════════════════════════════════════════════════
# Event Monitors
# ═══════════════════════════════════════════════════════════════════

class KeyboardMonitor:
    def __init__(self, max_events=200):
        self.max_events = max_events
        self.events = []
        self.lock = threading.Lock()
        self.running = False
        self.device_path = None
        try:
            import evdev
            devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
            for device in devices:
                if evdev.ecodes.EV_KEY in device.capabilities():
                    keys = device.capabilities()[evdev.ecodes.EV_KEY]
                    if evdev.ecodes.KEY_A in keys and evdev.ecodes.KEY_ENTER in keys:
                        self.device_path = device.path
                        break
                device.close()
        except Exception:
            pass

    def start(self):
        if not self.device_path:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

    def _monitor(self):
        try:
            import evdev
            device = evdev.InputDevice(self.device_path)
            for event in device.read_loop():
                if not self.running:
                    break
                if event.type == evdev.ecodes.EV_KEY:
                    key_event = {
                        "timestamp": event.timestamp(),
                        "keyname": evdev.ecodes.KEY.get(event.code, f"KEY_{event.code}"),
                        "event_type": "press" if event.value == 1 else "release"
                    }
                    with self.lock:
                        self.events.append(key_event)
                        if len(self.events) > self.max_events:
                            self.events = self.events[-self.max_events:]
        except Exception:
            pass

    def stop(self):
        self.running = False

    def get_recent(self, count=5):
        count = min(count, 10)
        with self.lock:
            events = self.events[-count:] if self.events else []
        return [e for e in events if e["event_type"] == "press"]

class I3Monitor:
    def __init__(self, max_events=200):
        self.max_events = max_events
        self.events = []
        self.lock = threading.Lock()
        self.running = False
        self.i3 = None
        try:
            import i3ipc
            self.i3 = i3ipc.Connection()
        except Exception:
            pass

    def start(self):
        if not self.i3:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

    def _monitor(self):
        try:
            import i3ipc
            def on_event(i3, e):
                if not self.running:
                    return
                event_data = {
                    "timestamp": time.time(),
                    "event_type": e.change,
                    "container": {
                        "name": e.container.name,
                        "window_class": e.container.window_class,
                        "workspace": e.container.workspace().name if e.container.workspace() else None,
                        "focused": e.container.focused
                    }
                }
                with self.lock:
                    self.events.append(event_data)
                    if len(self.events) > self.max_events:
                        self.events = self.events[-self.max_events:]
            self.i3.on(i3ipc.Event.WINDOW_NEW, on_event)
            self.i3.on(i3ipc.Event.WINDOW_CLOSE, on_event)
            self.i3.on(i3ipc.Event.WINDOW_FOCUS, on_event)
            self.i3.on(i3ipc.Event.WINDOW_TITLE, on_event)
            self.i3.on(i3ipc.Event.WORKSPACE_FOCUS, on_event)
            self.i3.main()
        except Exception:
            pass

    def stop(self):
        self.running = False
        if self.i3:
            try:
                self.i3.main_quit()
            except:
                pass

    def get_recent_events(self, count=5):
        count = min(count, 10)
        with self.lock:
            return self.events[-count:] if self.events else []

    def get_window_list(self):
        if not self.i3:
            return []
        try:
            tree = self.i3.get_tree()
            windows = []
            for leaf in tree.leaves():
                windows.append({
                    "name": leaf.name,
                    "class": leaf.window_class,
                    "workspace": leaf.workspace().name if leaf.workspace() else None,
                    "focused": leaf.focused
                })
            return windows
        except:
            return []

    def get_focused(self):
        if not self.i3:
            return None
        try:
            focused = self.i3.get_tree().find_focused()
            if focused:
                return {"name": focused.name, "class": focused.window_class}
        except:
            pass
        return None

# ═══════════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════════

class AccessibilityTree:
    def __init__(self):
        self.available = False
        try:
            import pyatspi
            self.pyatspi = pyatspi
            self.available = True
        except:
            pass

    def get_tree(self, target="focused", depth=2):
        if not self.available:
            return "AT-SPI2 not available."
        try:
            desktop = self.pyatspi.Registry.getDesktop(0)
            lines = []
            for app in desktop:
                if not app: continue
                app_name = app.name or "Unknown"
                for window in app:
                    if not window: continue
                    if target == "focused" and not window.getState().contains(self.pyatspi.STATE_FOCUSED):
                        continue
                    if target.lower() not in window.name.lower() and target.lower() not in app_name.lower():
                        continue
                    lines.append(f"App: {app_name} | Window: {window.name} ({window.getRoleName()})")
                    if depth >= 2:
                        self._walk(window, lines, 0, depth, "  ")
            return "\n".join(lines) if lines else f"No matching window for '{target}'."
        except Exception as e:
            return f"Error: {e}"

    def _walk(self, el, lines, depth, max_depth, indent):
        if depth >= max_depth: return
        try:
            for i in range(min(el.childCount, 30)):
                child = el.getChildAtIndex(i)
                if not child: continue
                role = child.getRoleName()
                name = child.name or ""
                text = ""
                try:
                    text_iface = child.queryText()
                    text = text_iface.getText(0, min(text_iface.characterCount, 100))
                except:
                    pass
                desc = f"{indent}[{role}] {name}"
                if text: desc += f' "{text}"'
                lines.append(desc)
                self._walk(child, lines, depth+1, max_depth, indent+"  ")
        except:
            pass

class OCRTool:
    def extract_text(self):
        try:
            import mss, tempfile
            with mss.MSS() as sct:
                img = sct.grab(sct.monitors[1])
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                    mss.tools.to_png(img.rgb, img.size, output=f.name)
                    tmp = f.name
            result = subprocess.run(["tesseract", tmp, "stdout", "--psm", "3"],
                                    capture_output=True, text=True, timeout=10)
            os.unlink(tmp)
            return result.stdout.strip() or "(no text detected)"
        except Exception as e:
            return f"OCR error: {e}"

class VLMTool:
    def __init__(self, api_key):
        self.client = Mistral(api_key=api_key)
        self.last_call = 0

    def describe(self, query):
        now = time.time()
        if now - self.last_call < 12:
            time.sleep(12 - (now - self.last_call))
        self.last_call = time.time()
        try:
            import mss, base64
            with mss.MSS() as sct:
                img = sct.grab(sct.monitors[1])
                png = mss.tools.to_png(img.rgb, img.size)
                b64 = base64.b64encode(png).decode()
            resp = self.client.chat.complete(
                model="pixtral-12b",
                messages=[{"role":"user","content":[
                    {"type":"text","text": f"Analyze this Linux desktop (i3 wm). {query}"},
                    {"type":"image_url","image_url": f"data:image/png;base64,{b64}"}
                ]}],
                temperature=0.0, max_tokens=500
            )
            return resp.choices[0].message.content
        except Exception as e:
            return f"VLM error: {e}"

# ═══════════════════════════════════════════════════════════════════
# Action Executor
# ═══════════════════════════════════════════════════════════════════

class ActionExecutor:
    KEY_MAP = {
        "super": "125:1 125:0", "ctrl": "29:1 29:0", "alt": "56:1 56:0", "shift": "42:1 42:0",
        "enter": "28:1 28:0", "tab": "15:1 15:0", "esc": "1:1 1:0", "space": "57:1 57:0",
        "backspace": "14:1 14:0", "delete": "111:1 111:0",
        "up":"103:1 103:0","down":"108:1 108:0","left":"105:1 105:0","right":"106:1 106:0",
        "home":"102:1 102:0","end":"107:1 107:0","pageup":"104:1 104:0","pagedown":"109:1 109:0",
        "ctrl+a":"29:1 30:1 30:0 29:0","ctrl+c":"29:1 46:1 46:0 29:0",
        "ctrl+v":"29:1 47:1 47:0 29:0","ctrl+x":"29:1 45:1 45:0 29:0",
        "ctrl+z":"29:1 44:1 44:0 29:0","ctrl+s":"29:1 31:1 31:0 29:0",
        "ctrl+n":"29:1 49:1 49:0 29:0","ctrl+w":"29:1 17:1 17:0 29:0",
        "ctrl+t":"29:1 20:1 20:0 29:0","ctrl+l":"29:1 38:1 38:0 29:0",
        "ctrl+f":"29:1 33:1 33:0 29:0","ctrl+d":"29:1 32:1 32:0 29:0",
        "ctrl+q":"29:1 16:1 16:0 29:0","alt+f4":"56:1 62:1 62:0 56:0",
        "alt+tab":"56:1 15:1 15:0 56:0","super+d":"125:1 32:1 32:0 125:0",
        "super+enter":"125:1 28:1 28:0 125:0",
    }
    LETTER = {chr(k): v for k,v in {
        30:'a',48:'b',46:'c',32:'d',18:'e',33:'f',34:'g',35:'h',23:'i',36:'j',37:'k',38:'l',50:'m',
        49:'n',24:'o',25:'p',16:'q',19:'r',31:'s',20:'t',22:'u',47:'v',17:'w',45:'x',21:'y',44:'z'
    }.items()}
    NUMBER = {str(i): v for i,v in enumerate([11,2,3,4,5,6,7,8,9,10])}
    SYMBOL = {' ':57,'-':12,'=':13,'[':26,']':27,'\\':43,';':39,"'":40,',':51,'.':52,'/':53,'`':41}

    def execute(self, actions):
        for action in actions:
            t = action["type"]
            if t == "key_combo":
                self._key(action["keys"])
                time.sleep(0.1)
            elif t == "type_text":
                subprocess.run(["ydotool", "type", action["text"]], check=True)
                time.sleep(0.2)
            elif t == "wait":
                time.sleep(min(action.get("duration",0.3), 2.0))
            else:
                print(f"⚠️ Unknown action: {t}")
                return False
        return True

    def _key(self, keys):
        k = keys.lower().strip()
        if k in self.KEY_MAP:
            codes = self.KEY_MAP[k].split()
            subprocess.run(["ydotool", "key"] + codes, check=True)
            return
        parts = k.split('+')
        if len(parts) > 1:
            down, up = [], []
            for p in parts:
                code = self._code(p.strip())
                if code:
                    down.append(f"{code}:1")
                    up.insert(0, f"{code}:0")
            if down:
                subprocess.run(["ydotool", "key"] + down + up, check=True)
                return
        code = self._code(k)
        if code:
            subprocess.run(["ydotool", "key", f"{code}:1", f"{code}:0"], check=True)

    def _code(self, key):
        if key in self.KEY_MAP:
            return int(self.KEY_MAP[key].split(':')[0])
        if len(key)==1 and key.isalpha():
            return self.LETTER.get(key)
        if len(key)==1 and key.isdigit():
            return self.NUMBER.get(key)
        return self.SYMBOL.get(key)

# ═══════════════════════════════════════════════════════════════════
# Tool Handler
# ═══════════════════════════════════════════════════════════════════

class ToolHandler:
    def __init__(self, kb, i3, atspi, ocr, vlm):
        self.kb = kb
        self.i3 = i3
        self.atspi = atspi
        self.ocr = ocr
        self.vlm = vlm

    def run(self, request):
        name = request.get("tool_name", "")
        params = request.get("parameters", {})
        data = ""
        
        if name == "input_events":
            events = self.kb.get_recent(params.get("count", 5))
            data = "\n".join(f"{e['keyname']} ({e['event_type']})" for e in events) or "No recent keypresses."
        
        elif name == "wm_events":
            events = self.i3.get_recent_events(params.get("count", 5))
            windows = self.i3.get_window_list()
            focused = self.i3.get_focused()
            lines = []
            
            if events:
                lines.append("Recent window events:")
                for e in events[-3:]:
                    c = e["container"]
                    lines.append(f"  [{e['event_type']}] {c.get('window_class','')}: \"{c.get('name','')}\"")
            
            lines.append("\nAll open windows:")
            if windows:
                for w in windows:
                    mark = " ← FOCUSED" if w["focused"] else ""
                    lines.append(f"  [{w['class']}] {w['name']}{mark}")
            else:
                lines.append("  (no windows open)")
            
            data = "\n".join(lines)
        
        elif name == "accessibility_tree":
            data = self.atspi.get_tree(params.get("target","focused"), params.get("depth",2))
        
        elif name == "ocr_screenshot":
            data = self.ocr.extract_text()
        
        elif name == "visual_screenshot":
            data = self.vlm.describe(params.get("query","Describe the screen"))
        
        elif name == "get_action_history":
            return {"tool": name, "data": "history_request", "count": params.get("count",5)}
        
        else:
            data = f"Unknown tool: {name}"
        
        return {"tool": name, "data": data}

# ═══════════════════════════════════════════════════════════════════
# LLM Client
# ═══════════════════════════════════════════════════════════════════

class LLMClient:
    def __init__(self):
        self.api_key = os.environ.get("MISTRAL_API_KEY")
        if not self.api_key:
            print("❌ MISTRAL_API_KEY not set!")
            sys.exit(1)
        self.client = Mistral(api_key=self.api_key)
        self.planner_model = os.environ.get("JARVIS_PLANNER_MODEL", "mistral-large-latest")
        self.supervisor_model = os.environ.get("JARVIS_SUPERVISOR_MODEL", self.planner_model)
        self.worker_model = os.environ.get("JARVIS_WORKER_MODEL", "mistral-small-latest")
        self.min_delay = float(os.environ.get("JARVIS_LLM_DELAY", "1.5"))
        self.last_call = 0

    def _wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_call = time.time()

    def _complete(self, model, messages, json_mode=False, max_tokens=1000):
        self._wait()
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return self.client.chat.complete(**kwargs).choices[0].message.content.strip()

    def initial_plan(self, objective):
        try:
            return self._complete(
                self.planner_model,
                [{"role":"user","content":
                    f"Task: {objective}\n\n"
                    "You are the planning model for a Linux i3 keyboard-only desktop agent.\n"
                    "Create a concise 4-6 step plan. Include verification steps and avoid retrying navigation while pages load.\n"
                    "Launch apps with Super+d, type name, Enter. Firefox navigation uses Ctrl+l, URL, Enter.\n"
                    "Respond with numbered steps only."}],
                max_tokens=500,
            )
        except Exception as e:
            print(f"⚠️ Plan error ({self.planner_model}): {e}")
            return "1. Observe the desktop\n2. Open the needed application\n3. Navigate or enter data once\n4. Wait for loading and verify state\n5. Complete the task"

    def ask_worker(self, system_prompt):
        return self._ask_json(self.worker_model, system_prompt, fallback={"response_type":"question","question_text":"Repeated worker errors. Please guide me."})

    def ask_supervisor(self, system_prompt):
        return self._ask_json(self.supervisor_model, system_prompt, fallback={
            "response_type":"review",
            "status":"intervention",
            "summary":"Supervisor review failed.",
            "directive":"Worker should stop repeating the last action, wait briefly, and verify with a different observation tool."
        })

    def _ask_json(self, model, system_prompt, fallback):
        for attempt in range(2):
            try:
                content = self._complete(model, [{"role":"system","content": system_prompt}], json_mode=True, max_tokens=1200)
                parsed = json.loads(content)
                if "response_type" not in parsed:
                    raise ValueError("Missing response_type")
                return parsed
            except json.JSONDecodeError:
                print(f"⚠️ JSON error from {model}, retrying...")
                time.sleep(1)
            except Exception as e:
                if "429" in str(e):
                    print("⏳ Rate limited, waiting 30s...")
                    time.sleep(30)
                else:
                    print(f"⚠️ API error from {model}: {e}")
                    time.sleep(2)
        return fallback.copy()

# ═══════════════════════════════════════════════════════════════════
# System Prompt (stricter)
# ═══════════════════════════════════════════════════════════════════

PROMPT = """You are JARVIS, controlling i3 on Arch Linux via keyboard injection.

OBJECTIVE: {objective}
PHASE: {phase} | TURN: {turn} | ACTION CYCLE: {cycle}

YOUR PLAN:
{plan}

RECENT ACTIONS:
{history}

LAST TOOL RESULTS:
{tool_results}

NAVIGATION/LOADING STATE:
{navigation_state}

SUPERVISOR DIRECTIVE:
{supervisor_directive}

AVAILABLE TOOLS (request using tool_request JSON):
- wm_events(count) : Shows recent window events AND list of ALL open windows with focus.
- input_events(count) : Shows recent keypresses.
- accessibility_tree(target, depth) : UI elements/text inside a window. target: "focused" or class name.
- ocr_screenshot() : Extract ALL text from screen via OCR.
- visual_screenshot(query) : Ask VLM about screen (EXPENSIVE, last resort).
- get_action_history(count) : Get more past actions.

ACTION FORMAT (max 5 per batch):
{{
  "response_type": "action",
  "actions": [
    {{"type": "key_combo", "keys": "super+d"}},
    {{"type": "wait", "duration": 0.3}},
    {{"type": "type_text", "text": "firefox"}},
    {{"type": "key_combo", "keys": "enter"}}
  ],
  "reasoning": "Why I'm doing this",
  "expected_outcome": "What should happen"
}}

TOOL REQUEST FORMAT (must include tool_name and parameters):
{{
  "response_type": "tool_request",
  "tool_name": "wm_events",
  "parameters": {{"count": 5}},
  "reasoning": "Why I need this tool"
}}

CRITICAL RULES:
1. Launch apps: Super+d → wait 0.3s → type app name → Enter.
2. Firefox navigation: Ctrl+l focuses address bar → type URL → Enter. Ctrl+t for new tab.
3. ALWAYS verify with wm_events or accessibility_tree after every action batch.
4. If wm_events already shows what you need, ACT on it. Do NOT request the same tool again.
4a. After entering a website URL, do NOT immediately enter the same URL again. Pages can still be loading; wait or verify with accessibility_tree/OCR before retrying navigation.
5. NEVER output a tool_request without a "tool_name" field.
6. If stuck after 2 attempts, use question response to ask human.

Respond with VALID JSON ONLY. Choose ONE: tool_request, action, question, terminate."""

# ═══════════════════════════════════════════════════════════════════
# Main Agent with circuit breakers
# ═══════════════════════════════════════════════════════════════════

class Jarvis:
    def __init__(self):
        print("🚀 Initializing JARVIS v2...")
        self.kb = KeyboardMonitor()
        self.i3 = I3Monitor()
        self.atspi = AccessibilityTree()
        self.ocr = OCRTool()
        self.vlm = VLMTool(os.environ["MISTRAL_API_KEY"])
        self.executor = ActionExecutor()
        self.tools = ToolHandler(self.kb, self.i3, self.atspi, self.ocr, self.vlm)
        self.llm = LLMClient()
        self.supervisor_tool_limit = int(os.environ.get("JARVIS_SUPERVISOR_TOOL_LIMIT", "5"))
        self.review_interval = int(os.environ.get("JARVIS_REVIEW_INTERVAL", "10"))
        self.repetition_threshold = int(os.environ.get("JARVIS_REPETITION_THRESHOLD", "3"))
        self.settle_timeout = float(os.environ.get("JARVIS_NAV_SETTLE_TIMEOUT", "6"))
        self.settle_poll = float(os.environ.get("JARVIS_NAV_SETTLE_POLL", "0.75"))
        self.kb.start()
        self.i3.start()
        time.sleep(0.5)
        print("✅ Ready.\n")

    def reset_session(self, objective):
        return AgentState(objective=objective)

    def stop(self):
        self.kb.stop()
        self.i3.stop()

    def _focus_signature(self):
        focused = self.i3.get_focused()
        if not focused:
            return None
        return {"name": focused.get("name"), "class": focused.get("class")}

    def _format_history(self, state):
        hist = ""
        for i, a in enumerate(state.action_history[-6:]):
            t = a.get('type','?')
            r = a.get('reasoning','')[:100]
            if t == 'action':
                acts = a.get('actions',[])
                desc = ', '.join(f"{x.get('type')}:{x.get('keys', x.get('text', x.get('duration','')))}" for x in acts[:3])
                hist += f"{i+1}. ACTION: {desc}\n   → {r}\n"
            elif t == 'tool_request':
                hist += f"{i+1}. TOOL: {a.get('tool_name','?')} - {r}\n"
            elif t == 'supervisor_review':
                hist += f"{i+1}. SUPERVISOR: {a.get('status')} - {a.get('directive','')[:100]}\n"
            elif t == 'question':
                hist += f"{i+1}. ASKED: {a.get('text','')[:80]}\n"
            elif t == 'human_response':
                hist += f"{i+1}. HUMAN: {a.get('text','')[:80]}\n"
        return hist or "None yet"

    def _format_tools(self, state):
        return "".join(f"[{r.get('tool','?')}]:\n{str(r.get('data',''))[:600]}\n\n" for r in state.tool_results) or "None yet. Request a tool to gather info."

    def _nav_state_text(self, state):
        nav = state.navigation
        if not nav.pending and not nav.message:
            return "No pending navigation."
        return f"status={nav.status}; target={nav.target}; message={nav.message}"

    def _worker_prompt(self, state):
        return PROMPT.format(
            objective=state.objective,
            phase=state.task_phase,
            turn=state.turn_count,
            cycle=state.cycle_count,
            plan=state.initial_reasoning,
            history=self._format_history(state),
            tool_results=self._format_tools(state),
            navigation_state=self._nav_state_text(state),
            supervisor_directive=state.latest_supervisor_directive or state.supervisor_warning or "None",
        )

    def _settle_navigation(self, state, actions, fingerprint):
        is_nav, target = is_navigation_batch(actions)
        if not is_nav:
            state.navigation.pending = False
            return None
        if state.navigation.fingerprint == fingerprint and time.time() - state.navigation.completed_at < self.settle_timeout:
            state.navigation.message = "Duplicate navigation suppressed: verify loading instead of re-entering the URL."
            return {"tool":"navigation_settle", "data": state.navigation.message}
        state.navigation = NavigationSettleState(True, target, fingerprint, time.time(), 0.0, "loading", f"Waiting for {target} to settle")
        before = self._focus_signature()
        deadline = time.time() + self.settle_timeout
        last = before
        stable_seen = 0
        while time.time() < deadline:
            time.sleep(self.settle_poll)
            current = self._focus_signature()
            if current and current == last:
                stable_seen += 1
                if stable_seen >= 2:
                    break
            else:
                stable_seen = 0
                last = current
        state.navigation.pending = False
        state.navigation.completed_at = time.time()
        state.navigation.status = "settled" if stable_seen >= 2 else "timeout"
        state.navigation.message = f"Navigation to {target} {state.navigation.status}; verify page before retrying URL entry."
        return {"tool":"navigation_settle", "data": state.navigation.message, "before": before, "after": last}

    def _record_cycle(self, state, actions, reasoning, expected, success, pre_focus, settle_result):
        post_focus = self._focus_signature()
        fingerprint = normalize_actions(actions)
        state.cycle_count += 1
        changed = pre_focus != post_focus or bool(settle_result and settle_result.get("before") != settle_result.get("after"))
        if fingerprint == state.last_action_fingerprint and not changed:
            state.repeated_action_count += 1
        else:
            state.repeated_action_count = 1
        state.last_action_fingerprint = fingerprint
        tools = list(state.tool_results)
        if settle_result:
            tools.append(settle_result)
            state.tool_results = [settle_result]
        rec = CycleRecord(state.cycle_count, actions, fingerprint, reasoning, expected, "success" if success else "failed", pre_focus, post_focus, changed, tools)
        state.cycle_history.append(rec)
        return rec

    def _supervisor_prompt(self, state, reason, records, observations):
        payload = [{
            "cycle": r.cycle_number,
            "fingerprint": r.fingerprint,
            "actions": r.actions,
            "result": r.result,
            "state_changed": r.state_changed,
            "pre_focus": r.pre_focus,
            "post_focus": r.post_focus,
            "reasoning": r.reasoning,
            "tools": r.tool_results[-2:],
        } for r in records]
        return ("You are the large-model supervisor for a keyboard-only Linux desktop agent. "
                "Review progress, detect loops, and provide concise guidance. You may request observation tools only.\n"
                f"Objective: {state.objective}\nReason for review: {reason}\nPlan:\n{state.initial_reasoning}\n"
                f"Navigation state: {self._nav_state_text(state)}\nCycle records JSON:\n{json.dumps(payload, default=str)[:6000]}\n"
                f"Extra observations JSON:\n{json.dumps(observations, default=str)[:3000]}\n"
                "Respond as JSON. Either request one tool with response_type=tool_request, tool_name, parameters, reasoning, "
                "or finish with {\"response_type\":\"review\",\"status\":\"ok|intervention\",\"summary\":\"...\",\"directive\":\"clear instruction for worker\"}.")

    def _run_supervisor_review(self, state, reason):
        records = state.cycle_history[-10:]
        observations = []
        for _ in range(self.supervisor_tool_limit + 1):
            resp = self.llm.ask_supervisor(self._supervisor_prompt(state, reason, records, observations))
            if resp.get("response_type") == "tool_request" and len(observations) < self.supervisor_tool_limit:
                name = resp.get("tool_name") or "wm_events"
                if name == "get_action_history":
                    data = json.dumps([r.__dict__ for r in records], default=str)
                    observations.append({"tool":"action_history", "data": data})
                else:
                    resp["tool_name"] = name
                    observations.append(self.tools.run(resp))
                continue
            if resp.get("response_type") == "review":
                state.latest_supervisor_directive = resp.get("directive", "")
                state.last_review_cycle = state.cycle_count
                state.action_history.append({"type":"supervisor_review", "status":resp.get("status"), "reason":reason, "summary":resp.get("summary",""), "directive":state.latest_supervisor_directive})
                if state.cycle_history:
                    state.cycle_history[-1].supervisor_directive = state.latest_supervisor_directive
                print(f"🧭 Supervisor {resp.get('status')}: {resp.get('summary','')[:160]}")
                if state.latest_supervisor_directive:
                    print(f"   Directive: {state.latest_supervisor_directive[:220]}")
                return resp
            break
        warning = "Supervisor could not complete review; worker must avoid repeating the last action and verify with a different observation."
        state.supervisor_warning = warning
        state.latest_supervisor_directive = warning
        return {"response_type":"review", "status":"intervention", "summary":"Supervisor unavailable", "directive":warning}

    def _has_repeated_short_pattern(self, state):
        if len(state.cycle_history) < 4:
            return False
        recent = state.cycle_history[-4:]
        fingerprints = [r.fingerprint for r in recent]
        no_progress = not any(r.state_changed for r in recent)
        return no_progress and fingerprints[:2] == fingerprints[2:]

    def _maybe_review(self, state):
        if state.cycle_count and state.repeated_action_count >= self.repetition_threshold and state.last_review_cycle != state.cycle_count:
            return self._run_supervisor_review(state, "three repeated action cycles without meaningful progress")
        if state.cycle_count and self._has_repeated_short_pattern(state) and state.last_review_cycle != state.cycle_count:
            return self._run_supervisor_review(state, "repeated short action pattern without meaningful progress")
        if state.cycle_count and state.cycle_count % self.review_interval == 0 and state.last_review_cycle != state.cycle_count:
            return self._run_supervisor_review(state, f"periodic {self.review_interval}-cycle checkpoint")
        return None

    def run(self, objective):
        state = self.reset_session(objective)
        state.initial_reasoning = self.llm.initial_plan(objective)
        print(f"📝 Plan:\n{state.initial_reasoning}\n{'─'*50}\n")
        while True:
            state.turn_count += 1
            resp = self.llm.ask_worker(self._worker_prompt(state))
            rtype = resp.get("response_type", "")
            if not rtype:
                state.consecutive_malformed += 1
                if state.consecutive_malformed >= 3:
                    resp = {"response_type":"question","question_text":"The worker returned malformed responses repeatedly. What should I do next?"}
                    rtype = "question"
                else:
                    print("⚠️ Malformed response (missing response_type). Skipping turn.")
                    continue
            if rtype == "tool_request":
                name = resp.get("tool_name") or "wm_events"
                resp["tool_name"] = name
                if name == state.last_tool_name:
                    state.consecutive_same_tool += 1
                else:
                    state.consecutive_same_tool = 1
                state.last_tool_name = name
                if state.consecutive_same_tool >= 3:
                    state.latest_supervisor_directive = f"You requested {name} repeatedly. Use a different observation or take the next safe action."
                    state.consecutive_same_tool = 0
                print(f"🔧 Turn {state.turn_count}: {name}")
                if name == "get_action_history":
                    count = min(resp.get("parameters",{}).get("count",5), 10)
                    formatted = json.dumps([r.__dict__ for r in state.cycle_history[-count:]], default=str)
                    state.tool_results = [{"tool":"action_history","data":formatted}]
                else:
                    result = self.tools.run(resp)
                    state.tool_results = [result]
                    print(f"   Result: {str(result.get('data',''))[:250]}")
                state.action_history.append({"type":"tool_request", "tool_name":name, "reasoning":resp.get("reasoning","")})
                state.consecutive_malformed = 0
                continue
            if rtype == "action":
                actions = resp.get("actions", [])[:5]
                if not actions:
                    print("⚠️ Action response with no actions")
                    continue
                fingerprint = normalize_actions(actions)
                nav, target = is_navigation_batch(actions)
                if nav and state.navigation.fingerprint == fingerprint and time.time() - state.navigation.completed_at < self.settle_timeout:
                    msg = "Blocked immediate duplicate navigation; wait/observe because the page may still be loading."
                    state.tool_results = [{"tool":"navigation_guard", "data":msg}]
                    state.latest_supervisor_directive = msg
                    print(f"🛑 {msg}")
                    continue
                reasoning = resp.get("reasoning", "")
                expected = resp.get("expected_outcome", "")
                print(f"\n⚡ Cycle {state.cycle_count + 1}: Executing {len(actions)} action(s)")
                pre_focus = self._focus_signature()
                success = self.executor.execute(actions)
                settle = self._settle_navigation(state, actions, fingerprint)
                state.action_history.append({"type":"action", "actions":actions, "reasoning":reasoning, "expected_outcome":expected, "result":"success" if success else "failed"})
                self._record_cycle(state, actions, reasoning, expected, success, pre_focus, settle)
                state.task_phase = "verifying"
                state.consecutive_same_tool = 0
                state.consecutive_malformed = 0
                state.consecutive_failures = 0 if success else state.consecutive_failures + 1
                self._maybe_review(state)
                continue
            if rtype == "question":
                qtext = resp.get("question_text", "I need help.")
                print(f"\n{'='*50}\n\a\a\a\n🔔 AGENT NEEDS HELP\n{'='*50}\n\n❓ {qtext}")
                ans = input("\n👉 Your response (or 'exit'): ")
                if ans.lower() == 'exit':
                    print("🛑 Terminated by user.")
                    return "user_exit"
                state.action_history.append({"type":"question","text":qtext})
                state.action_history.append({"type":"human_response","text":ans})
                continue
            if rtype == "terminate":
                status = resp.get("task_status", "completed")
                summary = resp.get("summary", "Task finished.")
                print(f"\n{'='*50}\n{'✅' if status=='completed' else '❌'} MISSION {status.upper()}\n{'='*50}\n\n{summary}")
                print(f"\n📊 {state.turn_count} turns, {state.cycle_count} action cycles")
                return status
            state.consecutive_malformed += 1
            print(f"⚠️ Unknown response_type '{rtype}'. Will retry.")

if __name__ == "__main__":
    jarvis = Jarvis()
    try:
        if len(sys.argv) > 1:
            obj = " ".join(sys.argv[1:]).strip()
            if not obj:
                print("❌ No objective provided.")
                sys.exit(1)
            jarvis.run(obj)
        else:
            print("\n🤖 JARVIS v2 - Autonomous Desktop Agent")
            print("Press Ctrl+C to exit. Completed missions return here.\n")
            while True:
                obj = input("What should JARVIS do?\n👉 ").strip()
                if not obj:
                    print("❌ No objective provided.")
                    continue
                jarvis.run(obj)
                print("\n↩️ Ready for the next objective.\n")
    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C received. Shutting down monitors.")
    finally:
        jarvis.stop()
        print("👋 JARVIS shutdown complete.")
