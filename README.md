# JARVIS: An Event-Driven Autonomous Desktop Agent

**Status:** Active Development
**Environment:** Arch Linux, i3 Window Manager (X11)
**LLM Backend:** Mistral API (`mistral-large-latest` for supervision, `mistral-small-latest` for worker actions, `pixtral-12b` for vision)

An autonomous desktop agent that controls a Linux machine through structured observation and keyboard-level input injection. Rather than treating the screen as its only source of truth, the agent builds a multi-layered understanding of system state -- combining window manager events, accessibility trees, optical character recognition, and visual language models only when necessary. Designed for low-spec hardware, it offloads all cognitive processing to the cloud while keeping local resource usage minimal.

---

## Evolution of the Project

What began as a simple screen-capture-and-act loop has evolved through four distinct architectural phases, each driven by hard lessons learned at the boundary between AI reasoning and real desktop environments.

### Phase One: The Blind Visionary

The original concept was straightforward. Capture a screenshot, send it to a vision-language model, ask what to do next, and inject the resulting keystrokes. Mistral's `pixtral-12b` served as both eyes and brain, receiving a base64-encoded PNG alongside a system prompt and returning JSON action commands.

This approach worked -- barely. On a machine with 4GB of RAM, running any local model caused the system to spiral into swap, pushing frame processing times past 200 seconds. Offloading to the cloud solved the memory crisis, but created new ones. The free tier's five requests per minute meant a mandatory 12-second gap between every action. Opening a single application required three separate API calls: one to press Super, one to type the app name, one to press Enter. That was 36 seconds just to launch a program. Any mistake meant starting over.

Worse, the agent was effectively blind between screenshots. It would press a shortcut, wait for the next capture cycle, and hope the right window had appeared. When it hadn't, the agent often hallucinated success and began typing Python code into a Bash terminal, or into nothing at all.

### Phase Two: The Event-Driven Architecture

The breakthrough came from recognizing that the screen is the slowest and most expensive source of truth about a computer's state. Linux already exposes rich structured data about what's happening: the kernel logs every keystroke through `/dev/input`, the i3 window manager broadcasts every window creation, focus change, and title update through its IPC socket, and the AT-SPI2 accessibility protocol exposes the internal structure of most GUI applications as a traversable tree of elements with names, roles, and text content.

The rewrite replaced the single screenshot loop with five on-demand observation tools, any of which the LLM could request before committing to an action:

- **input_events**: Returns the last N keyboard events captured at the kernel level, confirming what actually reached the system.
- **wm_events**: Shows recent window manager events alongside a complete list of currently open windows with focus indicators, making application state immediately visible.
- **accessibility_tree**: Reads the UI element tree of any window, revealing buttons, text fields, labels, and their contents without touching a pixel.
- **ocr_screenshot**: Extracts all visible text from the screen using Tesseract, providing a fast textual readout of dialogs, error messages, and web page content.
- **visual_screenshot**: The original vision model call, now reserved as a last resort for understanding complex visual layouts or applications that don't expose accessibility data.

The LLM now operates as an orchestrator. It begins by generating a strategic plan, then enters a tight observe-decide-act-verify loop. After each action batch -- up to five keystroke injections that can be executed atomically -- it requests a verification tool. If the accessibility tree confirms that a save dialog appeared and shows the filename field, there is no need to waste a vision API call on a screenshot. If `wm_events` reports that a Firefox window opened and is now focused, the agent can proceed directly to typing a URL.

This reduced vision model calls by roughly 80% and made the agent feel responsive for the first time. Actions that previously took 36-48 seconds now complete in 2-5 seconds.

The environment also shifted. GNOME on Wayland had been a constant source of friction: animations that confused the vision model, mouse coordinate scaling that broke cursor injection, and resource consumption that strained limited RAM. The move to i3 on X11 provided a deterministic, keyboard-native desktop where every window fills its workspace predictably, no animations interfere with screenshots, and the window manager itself exposes programmatic state through IPC.

### Phase Three: Resilience Engineering

The event-driven architecture solved the core efficiency problem but revealed new failure modes that only emerge when an AI agent runs unsupervised for extended periods.

The most persistent was the tool-request loop: the LLM would request `wm_events`, receive a window list showing no change, and request `wm_events` again -- expecting different results despite identical inputs. This could burn through a dozen API calls with no progress. The fix was a same-tool detector that forces an action after three consecutive identical tool requests, jolting the system into a new state.

Malformed responses from the language model presented another challenge. Despite explicit JSON schema instructions, the model occasionally omitted required fields like `tool_name` from a tool request. Rather than crash, the agent now defaults to `wm_events` when fields are missing and increments a malformed response counter. After three malformed responses, it forces a circuit-breaker action.

The most subtle failure was the action loop: the agent would successfully type a URL and press Enter, but the window title events arrived out of order or showed multiple states, confusing the LLM into retyping the same URL repeatedly. A fingerprint-based detector compared the current action batch against previous ones and halted with a human question if an identical sequence appeared three times consecutively.

These circuit breakers caught symptoms but didn't address the root cause: the agent was following a static plan rather than reasoning from current desktop state. A plan generated once at the start couldn't adapt when reality diverged — slow page loads, wrong workspaces, unexpected dialogs. The agent needed to re-derive its next step fresh each cycle based on what it actually observed.

### Phase Four: Observe-Reason-Act with Semantic Actions

Phase Four represented a fundamental architectural shift: abandoning stored plans entirely in favor of a tight **Observe → Reason → Act** loop where every decision is grounded in current desktop state.

**The Core Insight.** The old model was Plan → Execute → Verify. A plan was generated once and the agent tried to advance through it step by step. When something unexpected happened — a slow page load, a window on the wrong workspace — the agent couldn't adapt because it was following a script rather than looking at reality. The new model asks a single question each cycle: "Given what I see right now, what is the smallest action that moves toward the goal?"

**World Snapshot.** The agent captures a structured JSON snapshot of the entire desktop at the start of every cycle. This includes:
- The focused window (name, class, workspace)
- All open windows with their workspaces and focus status
- Recent window manager events (focus changes, title updates, new windows)
- The accessibility tree of the focused window, parsed to extract URL bars, search fields, and currently focused UI elements
- OCR text as a fallback when accessibility data is sparse

**Semantic Action Layer.** Instead of outputting raw keystrokes, the worker LLM selects from a curated set of high-level semantic actions: `launch_app`, `navigate_to`, `search_youtube`, `focus_window`, `press_key_combo`, `wait`, `force_reload`, and others. A Python translation layer maps each semantic action to the exact keystroke sequence for the current environment. This eliminates an entire class of failures: the LLM cannot hallucinate keyboard shortcuts or produce syntactically invalid action sequences.

**Workspace Awareness.** The snapshot includes workspace information for every window. When the agent is asked to launch Firefox but Firefox already exists on workspace 2, `launch_app` detects this and uses `i3-msg` to focus the existing window instead of spawning a duplicate. This prevents the agent from opening multiple Firefox instances across different workspaces and losing track of where it's typing.

**State-Based Guard Rails.** The repetition detector no longer compares keystroke fingerprints. Instead, it tracks whether each action produced a meaningful state change — did the focused window change? Did the accessibility tree update? Did the window list change? If the same semantic action is attempted three times with zero state change, the guard rail blocks it and escalates to the supervisor.

**Verified Goal Termination.** The agent no longer declares success unilaterally. When the worker believes the objective is complete, it outputs `terminate`. The large model then evaluates the full objective against the current World Snapshot. Only if the large model confirms completion does the loop exit. If not, its explanation is injected as a directive and the agent continues.

**Supervisor with Full Context.** When the supervisor is invoked — either by guard rails or by three consecutive no-change cycles — it receives the complete current World Snapshot and recent semantic action history. This allows it to diagnose specific problems ("the address bar already contains youtube.com but the page title shows 'Problem loading page' — try force reload") rather than issuing generic directives.

**Results.** The Phase Four architecture eliminated the pathological loops that plagued earlier versions. The agent no longer retypes URLs endlessly into the address bar. It no longer launches duplicate application windows. It correctly focuses existing windows across workspaces. API call efficiency improved further by removing the initial planning step — the implicit plan emerges from the gap between current state and goal state, re-derived fresh each cycle.

---

## Core Features

**Observe-Reason-Act Loop.** Every cycle starts with a fresh World Snapshot. The agent asks "what do I see?" rather than "where am I in my plan?" This eliminates stale-plan failures entirely.

**Semantic Action Translation.** The LLM chooses high-level actions (`search_youtube`, `navigate_to`) from a curated set. A Python layer translates these to exact keystrokes. The LLM never outputs raw key codes, eliminating syntax errors and hallucinated shortcuts.

**Workspace-Aware Window Management.** The agent knows which workspace every window occupies. Launching an already-open application focuses the existing window via `i3-msg` instead of creating duplicates.

**State-Based Guard Rails.** Repetition is detected by lack of state change, not identical keystroke patterns. An action that produces no change in the World Snapshot across three attempts is blocked and escalated.

**Verified Goal Completion.** The worker proposes termination; the large model confirms or rejects based on the full objective and current state. False completions are caught and corrected with specific feedback.

**Multi-Layer State Observation.** The agent builds its understanding from kernel input events, window manager IPC, accessibility trees, and screen capture — requesting only the layers needed for the current decision.

**Keyboard-Level Control.** All actions are injected through `ydotool` at the kernel level. Mouse movement is deliberately excluded, forcing all navigation through keyboard shortcuts for reliability.

**Power-User Keyboard Patterns.** The semantic translator encodes power-user shortcuts: `/` to focus YouTube search, `Ctrl+L` for address bar, `Ctrl+Shift+R` for force reload, `i3-msg` for workspace and window focus operations.

**Supervisor Escalation.** A `mistral-large-latest` supervisor is invoked only when the agent is stuck (three no-change cycles or guard rail trigger). It receives full current state and diagnoses the specific problem.

**Persistent CLI Sessions.** A `terminate` ends only the current objective. The process returns to the objective prompt with fresh per-session state, while monitors and API clients remain initialized.

---

## Architecture

```
User Objective
      |
      v
+------------------+     +-------------------+
| World Snapshot   |---->| Agent Controller  |
| (wm_events,      |     | (Observe-Reason-  |
|  accessibility,  |     |  Act loop)        |
|  OCR, i3 state)  |     +--------+----------+
+------------------+              |
                      +-----------+-----------+
                      |           |           |
              +-------v------+ +--v---------+ +------v-------+
              | Semantic     | | Action      | | Event        |
              | Translator   | | Executor    | | Monitors     |
              | (action→keys)| | (ydotool)   | | /dev/input   |
              +--------------+ +------------+ | i3 IPC       |
                                              | (background  |
                                              |  threads)    |
                                              +--------------+
```

---

## Semantic Actions Reference

| Action | Parameters | Description |
|--------|------------|-------------|
| `launch_app` | `app_name` | Launches via dmenu or focuses existing window on any workspace |
| `focus_window` | `class_name` | Focuses a window by class name using i3-msg |
| `navigate_to` | `url` | Focuses address bar (Ctrl+L), selects all, types URL, presses Enter |
| `search_youtube` | `query` | Presses `/` to focus YouTube search, types query, tabs to first suggestion, presses Enter |
| `focus_youtube_search` | none | Presses `/` to focus the YouTube search box |
| `type_in_focused_field` | `text` | Types text into the currently focused field |
| `press_key_combo` | `keys` | Executes an arbitrary key combination (e.g., `ctrl+t`, `super+2`) |
| `force_reload` | none | Ctrl+Shift+R to bypass cache |
| `close_tab` | none | Ctrl+W |
| `new_tab` | none | Ctrl+T |
| `wait` | `seconds` | Pauses execution |
| `terminate` | none | Proposes goal completion for large-model verification |

---

## Installation and Setup

### System Dependencies (Arch Linux)

```bash
sudo pacman -S ydotool tesseract tesseract-data-eng python-evdev
sudo systemctl enable --now ydotoold
export YDOTOOL_SOCKET=/run/ydotoold.socket
sudo usermod -aG input $USER
```

Add the socket export to `~/.bashrc` for persistence.

### Python Environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install mistralai mss Pillow i3ipc pyatspi
```

### Configuration

```bash
export MISTRAL_API_KEY="your_api_key_here"
export JARVIS_LLM_DELAY=0.7          # seconds between API calls (default: 1.5)
export JARVIS_SMALL_MODEL="mistral-small-latest"   # worker decisions
export JARVIS_LARGE_MODEL="mistral-large-latest"   # supervisor & goal verification
```

### Display Manager

The agent requires the i3 window manager. Install with `sudo pacman -S i3` and ensure it is running before starting the agent.

---

## Usage

```bash
python brain.py
```

When prompted, provide a natural language objective:

```
Open Firefox and go to github.com
```

Or more complex multi-step tasks:

```
Open Firefox, navigate to YouTube, search for "3 blue 1 brown", and play the latest video
```

The agent will capture a World Snapshot, select a semantic action, translate it to keystrokes, execute, verify state change, and repeat. If it cannot make progress after three cycles, the supervisor intervenes with a diagnosis. If it believes the goal is complete, the large model verifies before terminating.

---

## Key Design Decisions

**Why no stored plan?** A plan generated once at the start becomes stale the moment reality diverges — a slow page load, a window on the wrong workspace, an unexpected dialog. By re-deriving the next step fresh each cycle from current state, the agent naturally adapts to whatever it encounters.

**Why semantic actions instead of raw keystrokes?** LLMs sometimes hallucinate keyboard shortcuts or produce malformed action JSON. A curated set of semantic actions with a Python translation layer eliminates this entire class of errors. The LLM says what it wants to do; the code figures out how.

**Why workspace awareness?** Without workspace information, the agent couldn't distinguish between "Firefox is running on workspace 2" and "Firefox is not running." This led to duplicate windows and typing commands into the wrong application. i3's IPC provides workspace data natively, making this a zero-cost fix.

**Why large-model goal verification?** The worker model sometimes declares success prematurely — it types a search query and assumes the task is done. The large model evaluates the full objective against the actual state and catches these false completions with specific feedback.

**Why keyboard-only?** Mouse coordinates are fragile across display configurations and Wayland scaling. Keyboard shortcuts are deterministic: `/` always focuses YouTube search, `Ctrl+L` always focuses the address bar. This reliability is essential for autonomous operation.

---

## Limitations and Current Work

**Web page element selection.** The agent can search YouTube and navigate to results pages, but selecting a specific video from search results remains challenging. The page structure is dynamic and the accessibility tree is deep. A `Ctrl+F` text-finding approach is in development to locate video titles and activate them via keyboard.

**Accessibility tree coverage.** AT-SPI2 support varies by application. Firefox and GTK applications expose rich trees, while Electron apps and games provide minimal data. OCR serves as a fallback but is slower and less structured.

**Dynamic content handling.** YouTube and similar sites update their structure as content loads. The agent currently handles this through wait-and-verify cycles but could benefit from more sophisticated change detection.

---

## License

MIT