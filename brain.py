#!/usr/bin/env python3
"""
JARVIS v3.1 – Workspace-aware, power-user shortcuts, verified goal termination.
"""

import os, sys, json, time, subprocess, threading, re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from mistralai.client import Mistral

# ═══════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SemanticAction:
    name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""

@dataclass
class CycleRecord:
    cycle_number: int
    snapshot_before: Dict
    action: SemanticAction
    success: bool
    snapshot_after: Dict
    state_changed: bool

# ═══════════════════════════════════════════════════════════════════
# Keyboard Monitor
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

# ═══════════════════════════════════════════════════════════════════
# i3 Monitor (full implementation)
# ═══════════════════════════════════════════════════════════════════

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
                ws = leaf.workspace().name if leaf.workspace() else None
                windows.append({
                    "name": leaf.name,
                    "class": leaf.window_class,
                    "workspace": ws,
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
                ws = focused.workspace().name if focused.workspace() else None
                return {"name": focused.name, "class": focused.window_class, "workspace": ws}
        except:
            pass
        return None

# ═══════════════════════════════════════════════════════════════════
# Accessibility Tree (with get_focused_element_info)
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
            return ""
        try:
            desktop = self.pyatspi.Registry.getDesktop(0)
            lines = []
            for app in desktop:
                if not app: continue
                for window in app:
                    if not window: continue
                    if target == "focused" and not window.getState().contains(self.pyatspi.STATE_FOCUSED):
                        continue
                    if target.lower() not in window.name.lower() and target.lower() not in (app.name or "").lower():
                        continue
                    lines.append(f"Window: {window.name} ({window.getRoleName()})")
                    if depth >= 2:
                        self._walk(window, lines, 0, depth, "  ")
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

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

    def get_focused_element_info(self):
        """Extract key UI elements from focused window: URL bar, search fields."""
        if not self.available:
            return {}
        try:
            desktop = self.pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if not app: continue
                for window in app:
                    if not window: continue
                    if window.getState().contains(self.pyatspi.STATE_FOCUSED):
                        info = {}
                        self._find_elements(window, info)
                        return info
        except:
            pass
        return {}

    def _find_elements(self, el, info):
        try:
            role = el.getRoleName()
            name = el.name or ""
            if role in ("text", "entry", "password text") and ("address" in name.lower() or "url" in name.lower() or "search" in name.lower()):
                text = ""
                try:
                    text_iface = el.queryText()
                    text = text_iface.getText(0, min(text_iface.characterCount, 200))
                except:
                    pass
                info["url_bar"] = {"name": name, "text": text}
            if el.getState().contains(self.pyatspi.STATE_FOCUSED) and role in ("text", "entry", "password text"):
                text = ""
                try:
                    text_iface = el.queryText()
                    text = text_iface.getText(0, min(text_iface.characterCount, 200))
                except:
                    pass
                info["focused_field"] = {"name": name, "text": text}
            for i in range(el.childCount):
                self._find_elements(el.getChildAtIndex(i), info)
        except:
            pass

# ═══════════════════════════════════════════════════════════════════
# OCR Tool
# ═══════════════════════════════════════════════════════════════════

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
            return result.stdout.strip() or ""
        except Exception:
            return ""

# ═══════════════════════════════════════════════════════════════════
# Action Executor (unchanged)
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
        "ctrl+shift+r": "29:1 42:1 19:1 19:0 42:0 29:0",
        "f5": "63:1 63:0", "ctrl+f5": "29:1 63:1 63:0 29:0",
        "/": "53:1 53:0",  # slash key
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
# Semantic Translator
# ═══════════════════════════════════════════════════════════════════

class SemanticTranslator:
    def __init__(self):
        self.executor = ActionExecutor()

    def translate(self, action: SemanticAction, current_snapshot: Dict) -> List[Dict]:
        name = action.name
        params = action.parameters

        if name == "launch_app":
            app = params.get("app_name", "").lower()
            open_windows = current_snapshot.get("open_windows", [])
            existing = [w for w in open_windows if w["class"].lower() == app]
            if existing:
                # focus existing window
                return [{"type": "focus_window", "class_name": app}]
            else:
                return [
                    {"type": "key_combo", "keys": "super+d"},
                    {"type": "wait", "duration": 0.3},
                    {"type": "type_text", "text": app},
                    {"type": "key_combo", "keys": "enter"}
                ]

        elif name == "focus_window":
            class_name = params.get("class_name", "")
            return [{"type": "focus_window", "class_name": class_name}]

        elif name == "navigate_to":
            url = params.get("url", "")
            return [
                {"type": "key_combo", "keys": "ctrl+l"},
                {"type": "wait", "duration": 0.2},
                {"type": "key_combo", "keys": "ctrl+a"},
                {"type": "type_text", "text": url},
                {"type": "key_combo", "keys": "enter"}
            ]

        elif name == "type_in_focused_field":
            return [{"type": "type_text", "text": params.get("text", "")}]

        elif name == "press_key_combo":
            return [{"type": "key_combo", "keys": params.get("keys", "")}]

        elif name == "search_youtube":
            query = params.get("query", "")
            return [
                {"type": "key_combo", "keys": "/"},
                {"type": "wait", "duration": 0.3},
                {"type": "type_text", "text": query},
                {"type": "wait", "duration": 1.0},
                {"type": "key_combo", "keys": "tab"},
                {"type": "key_combo", "keys": "enter"}
            ]

        elif name == "focus_youtube_search":
            return [{"type": "key_combo", "keys": "/"}]

        elif name == "force_reload":
            return [{"type": "key_combo", "keys": "ctrl+shift+r"}]

        elif name == "close_tab":
            return [{"type": "key_combo", "keys": "ctrl+w"}]

        elif name == "new_tab":
            return [{"type": "key_combo", "keys": "ctrl+t"}]

        elif name == "wait":
            return [{"type": "wait", "duration": float(params.get("seconds", 1.0))}]

        else:
            print(f"⚠️ Unknown semantic action: {name}")
            return []

    def execute_low_level(self, actions: List[Dict]) -> bool:
        for act in actions:
            if act["type"] == "focus_window":
                class_name = act["class_name"]
                try:
                    subprocess.run(["i3-msg", f'[class="{class_name}"]', "focus"], check=True)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"⚠️ i3-msg focus failed: {e}")
                    return False
            else:
                if not self.executor.execute([act]):
                    return False
        return True

# ═══════════════════════════════════════════════════════════════════
# World Snapshot
# ═══════════════════════════════════════════════════════════════════

class WorldSnapshot:
    def __init__(self, kb, i3, atspi, ocr):
        self.kb = kb
        self.i3 = i3
        self.atspi = atspi
        self.ocr = ocr

    def capture(self):
        focused = self.i3.get_focused()
        windows = self.i3.get_window_list()
        recent_events = self.i3.get_recent_events(3)
        at_tree = self.atspi.get_tree("focused", 2)
        focused_el = self.atspi.get_focused_element_info()
        ocr_text = ""
        if not at_tree or "error" in at_tree.lower():
            ocr_text = self.ocr.extract_text()

        return {
            "focused_window": focused,
            "open_windows": windows,
            "recent_wm_events": [
                {"type": e["event_type"], "window_class": e["container"].get("window_class", ""),
                 "title": e["container"].get("name", "")} for e in recent_events
            ],
            "accessibility_tree": at_tree[:1200],
            "focused_ui_elements": focused_el,
            "ocr_text": ocr_text[:600],
        }

    def diff(self, before, after):
        if before.get("focused_window") != after.get("focused_window"):
            return True
        bw = {w["name"] for w in before.get("open_windows", [])}
        aw = {w["name"] for w in after.get("open_windows", [])}
        if bw != aw:
            return True
        if before.get("accessibility_tree") != after.get("accessibility_tree"):
            return True
        if before.get("ocr_text") != after.get("ocr_text"):
            return True
        return False

# ═══════════════════════════════════════════════════════════════════
# LLM Client
# ═══════════════════════════════════════════════════════════════════

class LLMClient:
    def __init__(self):
        self.api_key = os.environ["MISTRAL_API_KEY"]
        self.client = Mistral(api_key=self.api_key)
        self.small = os.environ.get("JARVIS_SMALL_MODEL", "mistral-small-latest")
        self.large = os.environ.get("JARVIS_LARGE_MODEL", "mistral-large-latest")
        self.delay = float(os.environ.get("JARVIS_LLM_DELAY", "0.6"))
        self.last = 0

    def _wait(self):
        elapsed = time.time() - self.last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last = time.time()

    def complete(self, prompt, model=None, max_tokens=500, json_mode=False):
        self._wait()
        if model is None:
            model = self.small
        kwargs = {"model": model, "messages": [{"role":"user","content":prompt}],
                  "temperature":0.0, "max_tokens":max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            return self.client.chat.complete(**kwargs).choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e):
                print("⏳ Rate limited, waiting 30s...")
                time.sleep(30)
            else:
                print(f"⚠️ LLM error: {e}")
            return ""

    def ask_small(self, prompt, json_mode=True, max_tokens=400):
        return self.complete(prompt, model=self.small, json_mode=json_mode, max_tokens=max_tokens)

    def ask_large(self, prompt, json_mode=True, max_tokens=600):
        return self.complete(prompt, model=self.large, json_mode=json_mode, max_tokens=max_tokens)

# ═══════════════════════════════════════════════════════════════════
# Goal Evaluator (large model, used only on termination)
# ═══════════════════════════════════════════════════════════════════

class GoalEvaluator:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def confirm_completion(self, objective: str, snapshot: Dict) -> Tuple[bool, str]:
        prompt = f"""Objective: {objective}
Current desktop state:
{json.dumps(snapshot, indent=2)}

Has the objective been fully achieved? Answer with a JSON: {{"completed": true/false, "explanation": "..."}}."""
        resp = self.llm.ask_large(prompt, json_mode=True, max_tokens=300)
        if not resp:
            return False, "Failed to evaluate."
        try:
            data = json.loads(resp)
            return data.get("completed", False), data.get("explanation", "")
        except:
            return False, "Invalid evaluation response."

# ═══════════════════════════════════════════════════════════════════
# Main Agent
# ═══════════════════════════════════════════════════════════════════

class Jarvis:
    def __init__(self):
        self.kb = KeyboardMonitor()
        self.i3 = I3Monitor()
        self.atspi = AccessibilityTree()
        self.ocr = OCRTool()
        self.snapshot_engine = WorldSnapshot(self.kb, self.i3, self.atspi, self.ocr)
        self.translator = SemanticTranslator()
        self.llm = LLMClient()
        self.evaluator = GoalEvaluator(self.llm)
        self.kb.start()
        self.i3.start()
        time.sleep(0.5)

    def stop(self):
        self.kb.stop()
        self.i3.stop()

    def _prompt_worker(self, objective, snap, history, extra_directive=""):
        snap_str = json.dumps(snap, indent=2)
        hist_str = "\n".join(
            f"Cycle {r.cycle_number}: {r.action.name}({r.action.parameters}) success={r.success} state_changed={r.state_changed}"
            for r in history[-6:]
        ) or "None"
        return f"""You are a desktop automation agent controlling i3 on Linux.
GOAL: {objective}

CURRENT DESKTOP STATE:
{snap_str}

RECENT ACTIONS:
{hist_str}

{extra_directive}

POWER-USER KEYBOARD TIPS:
- Use '/' on YouTube to focus the search box.
- Use Ctrl+L for address bar, Ctrl+T for new tab.
- If an app is already open, use focus_window (class name) instead of launch_app.
- You can use press_key_combo with any i3 key combination (e.g., 'super+2' to switch to workspace 2).
- Use Ctrl+F to search for text on a webpage.

AVAILABLE SEMANTIC ACTIONS (output JSON with 'action', 'parameters', 'reasoning'):
- launch_app: {{"app_name": "firefox"}}
- focus_window: {{"class_name": "firefox"}}
- navigate_to: {{"url": "https://..."}}
- type_in_focused_field: {{"text": "..."}}
- press_key_combo: {{"keys": "ctrl+t"}}
- search_youtube: {{"query": "..."}}   (uses '/' to focus, type, Tab, Enter)
- focus_youtube_search: (just press '/')
- force_reload: ()
- close_tab: ()
- new_tab: ()
- wait: {{"seconds": 2.0}}
- terminate: ()   (if you believe the goal is fully achieved)

Choose ONE action that makes progress. Explain your reasoning briefly."""

    def _guard_rails(self, action: SemanticAction, history: List[CycleRecord]) -> Tuple[bool, str]:
        if len(history) < 3:
            return False, ""
        last_three = history[-3:]
        if all(r.action.name == action.name and r.action.parameters == action.parameters for r in last_three):
            if not any(r.state_changed for r in last_three):
                return True, "Same action repeated 3 times with no state change."
        return False, ""

    def _supervisor_intervention(self, objective, snap, history):
        prompt = f"""You are a supervisor agent. The small worker is stuck on: {objective}
Current state:
{json.dumps(snap, indent=2)}

History:
{json.dumps([{"cycle": r.cycle_number, "action": r.action.name, "params": r.action.parameters, "changed": r.state_changed} for r in history[-6:]], indent=2)}

Propose ONE semantic action to break the deadlock. Use the same action names as the worker.
Output JSON: {{"action": "...", "parameters": {{...}}, "reasoning": "..."}}"""
        resp = self.llm.ask_large(prompt, json_mode=True, max_tokens=400)
        if not resp:
            return SemanticAction("press_key_combo", {"keys": "esc"}, "fallback")
        try:
            data = json.loads(resp)
            return SemanticAction(data["action"], data.get("parameters", {}), data["reasoning"])
        except:
            return SemanticAction("force_reload", {}, "fallback")

    def run(self, objective):
        history: List[CycleRecord] = []
        snap = self.snapshot_engine.capture()
        cycle = 0

        while True:
            cycle += 1
            print(f"\n🔄 Cycle {cycle}")

            directive = ""
            if history and not history[-1].state_changed:
                directive = "Note: last action did not change the state. Consider a different approach."

            worker_prompt = self._prompt_worker(objective, snap, history, directive)
            resp = self.llm.ask_small(worker_prompt, json_mode=True, max_tokens=400)
            if not resp:
                print("❌ No response from worker. Retrying...")
                continue
            try:
                data = json.loads(resp)
                action = SemanticAction(data["action"], data.get("parameters", {}), data.get("reasoning", ""))
            except:
                print(f"⚠️ Invalid worker JSON: {resp[:150]}")
                continue

            print(f"💡 Worker: {action.name}({action.parameters}) | {action.reasoning[:120]}")

            if action.name == "terminate":
                confirmed, reason = self.evaluator.confirm_completion(objective, snap)
                if confirmed:
                    print(f"✅ Goal achieved: {reason}")
                    return "completed"
                else:
                    print(f"❌ Not yet. Reason: {reason}")
                    directive = f"Goal not yet achieved: {reason}. Please continue."
                    continue

            blocked, msg = self._guard_rails(action, history)
            if blocked:
                print(f"🚫 Blocked: {msg}")
                action = self._supervisor_intervention(objective, snap, history)
                print(f"↩️ Supervisor: {action.name}({action.parameters})")

            low_level = self.translator.translate(action, snap)
            if not low_level:
                print("⚠️ No low-level actions.")
                continue

            print(f"⌨️ Executing {len(low_level)} step(s)...")
            success = self.translator.execute_low_level(low_level)
            time.sleep(0.5)

            new_snap = self.snapshot_engine.capture()
            changed = self.snapshot_engine.diff(snap, new_snap)

            record = CycleRecord(cycle, snap, action, success, new_snap, changed)
            history.append(record)
            snap = new_snap

            if len(history) >= 3 and not any(r.state_changed for r in history[-3:]):
                print("🧠 No progress for 3 cycles, calling supervisor...")
                sup_action = self._supervisor_intervention(objective, snap, history)
                print(f"↩️ Supervisor: {sup_action.name}({sup_action.parameters})")
                sup_low = self.translator.translate(sup_action, snap)
                if sup_low:
                    self.translator.execute_low_level(sup_low)
                    time.sleep(0.5)
                    sup_snap = self.snapshot_engine.capture()
                    sup_changed = self.snapshot_engine.diff(snap, sup_snap)
                    history.append(CycleRecord(cycle + 0.5, snap, sup_action, True, sup_snap, sup_changed))
                    snap = sup_snap
                    continue

            if cycle % 25 == 0:
                a = input("\n🤔 Still working. Continue? (y/n): ")
                if a.lower() != 'y':
                    return "interrupted"

# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    jarvis = Jarvis()
    try:
        print("🚀 JARVIS v3.1 – Workspace-aware & Power-User Ready.")
        print("Press Ctrl+C to exit.\n")
        while True:
            obj = input("What should JARVIS do?\n👉 ").strip()
            if not obj:
                print("❌ Empty objective.")
                continue
            result = jarvis.run(obj)
            print(f"\n↩️ Mission: {result}. Ready for next.\n")
    except KeyboardInterrupt:
        print("\n🛑 Shutting down.")
    finally:
        jarvis.stop()
        print("👋 JARVIS shutdown complete.")