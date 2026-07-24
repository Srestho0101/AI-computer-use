# JARVIS: An Event-Driven Autonomous Desktop Agent

**Status:** Active Development
**Environment:** Arch Linux, i3 Window Manager (X11)
**LLM Backend:** Mistral API (`mistral-large-latest` for planning/supervision, `mistral-small-latest` for worker actions, `pixtral-12b` for vision)

An autonomous desktop agent that controls a Linux machine through structured observation and keyboard-level input injection. Rather than treating the screen as its only source of truth, the agent builds a multi-layered understanding of system state -- combining window manager events, accessibility trees, optical character recognition, and visual language models only when necessary. Designed for low-spec hardware, it offloads all cognitive processing to the cloud while keeping local resource usage minimal.

---

## Evolution of the Project

What began as a simple screen-capture-and-act loop has evolved through three distinct architectural phases, each driven by hard lessons learned at the boundary between AI reasoning and real desktop environments.

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

The most persistent was the tool-request loop: the LLM would request `wm_events`, receive a window list showing no change, and request `wm_events` again -- expecting different results despite identical inputs. This could burn through a dozen API calls with no progress. The fix was a same-tool detector that forces an action (opening dmenu) after three consecutive identical tool requests, jolting the system into a new state.

Malformed responses from the language model presented another challenge. Despite explicit JSON schema instructions, the model occasionally omitted required fields like `tool_name` from a tool request. Rather than crash, the agent now defaults to `wm_events` when fields are missing and increments a malformed response counter. After three malformed responses, it forces a circuit-breaker action.

The most subtle failure was the action loop: the agent would successfully type a URL and press Enter, but the window title events arrived out of order or showed multiple states, confusing the LLM into retyping the same URL repeatedly. A fingerprint-based detector now compares the current action batch against the previous two and halts with a human question if an identical sequence appears three times consecutively.

The choice of language model also evolved. The original architecture used `pixtral-12b` for everything, but the free tier's four requests per minute made multi-step tasks impractical. The current dual-model design uses `mistral-large-latest` only for the initial plan, loop recovery, and ten-cycle checkpoints, while `mistral-small-latest` performs high-frequency worker decisions. `pixtral-12b` remains reserved for the visual screenshot tool. A shared configurable inter-request delay keeps the free-tier quota protected.

---

## Core Features

**Multi-Layer State Observation.** The agent builds its understanding of the desktop from four independent data sources -- kernel input events, window manager IPC, accessibility trees, and screen capture -- requesting only the layers it needs for the current decision.

**On-Demand Tool Architecture.** Rather than streaming raw event logs, the LLM explicitly requests specific tools with parameters, receiving precisely the context it needs. This keeps prompts lean and reasoning focused.

**Keyboard-Level Control.** All actions are translated to raw scancodes and injected through the `ydotool` daemon at the kernel level, making them indistinguishable from physical keypresses. Mouse movement is deliberately excluded, forcing all navigation through keyboard shortcuts for reliability.

**Verification Gates.** Every action batch must be confirmed by an observation tool before the next batch proceeds. The agent cannot assume that pressing Ctrl+N actually created a new file; it must verify through window title changes, accessibility tree updates, or OCR.

**Circuit Breakers and Self-Recovery.** The controller tracks completed action cycles instead of raw LLM turns. Three repeated normalized action batches without meaningful focus/title progress trigger a `mistral-large-latest` supervisor review, and every ten action cycles the supervisor reviews the previous ten cycle records. Same-tool and malformed-response guards remain recoverable, but the preferred recovery path is a structured directive injected into the worker prompt.

**Strategic Planning and Memory.** Before execution begins, the large planner generates and records a high-level plan. Worker prompts include recent actions, current verification results, navigation/loading state, and the latest supervisor directive. Structured cycle records retain action fingerprints, tool observations, state changes, and supervisor guidance for later reviews.

**Rate-Limit Awareness.** API calls respect free-tier constraints: the small worker handles most turns, large-model reviews are sparse, supervisor observation calls are capped at five, and the expensive vision model is protected by a mandatory 12-second cooldown.

**Navigation Settle Gate.** URL-entry batches are recognized deterministically (`Ctrl+l` or `Ctrl+t`, typed URL, `Enter`). After such a batch, the controller waits for a bounded stabilization period and records the navigation state. Immediate duplicate URL entry is blocked so slow-loading sites such as YouTube are verified instead of reloaded in a loop.

**Persistent CLI Sessions.** In interactive mode, a `terminate` response ends only the current objective. The process returns to the objective prompt with fresh per-session state, while monitors and API clients remain initialized. `Ctrl+C` stops monitors and exits cleanly.

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

### Display Manager

The agent requires the i3 window manager. Install with `sudo pacman -S i3` and ensure it is running before starting the agent. A minimal i3 configuration is recommended: disable window decorations, gaps, and the status bar to reduce visual noise that can confuse the vision model when it is called.

---

## Usage

Export your Mistral API key and run the agent:

```bash
export MISTRAL_API_KEY="your_api_key_here"
python brain.py
```

When prompted, provide a natural language objective. For example:

```
Open Firefox and go to github.com
```

Or more complex tasks:

```
Open Firefox, navigate to YouTube, search for "3 blue 1 brown", and play the latest video
```

The agent will generate a strategic plan with the large model, then begin its worker observe-decide-act-verify loop. It will print each tool request, action batch, and verification result to the terminal. If it encounters a situation it cannot resolve, it will ring the terminal bell and ask for human guidance.

---

## Architecture

```
User Objective
      |
      v
Initial Strategic Plan (mistral-large-latest)
      |
      v
+-----+------+     +-------------------+
| LLM Client |<--->| Agent Controller  |
| worker +   |     | (cycle tracking,  |
| supervisor) |     |  settle/reviews)  |
+------------+     +--------+----------+
                            |
            +---------------+---------------+
            |               |               |
    +-------v------+ +-----v------+ +------v-------+
    | Tool Handler | | Action     | | Event        |
    |              | | Executor   | | Monitors     |
    | wm_events    | | (ydotool)  | | /dev/input   |
    | input_events | |            | | i3 IPC       |
    | access_tree  | | key_combo  | | (background  |
    | ocr_screen   | | type_text  | |  threads)    |
    | visual_screen| | wait       | |              |
    +--------------+ +------------+ +--------------+
```

---

## Tools Reference

| Tool | Parameters | Description |
|------|------------|-------------|
| `wm_events` | `count` (1-10) | Recent window events and complete window list with focus indicators. After page loads, shows Firefox current page title. |
| `input_events` | `count` (1-10) | Recent keyboard events captured at kernel level. |
| `accessibility_tree` | `target` ("focused" or class name), `depth` (1-3) | Structured UI element tree with names, roles, and text content. |
| `ocr_screenshot` | none | Screen capture processed through Tesseract OCR, returning all visible text. |
| `visual_screenshot` | `query` (string) | Full screenshot analyzed by `pixtral-12b` vision model. The most expensive tool, reserved for situations where other tools are insufficient. |
| `get_action_history` | `count` | Returns earlier actions beyond the default four included in each prompt. |

---

## Response Types

The LLM communicates through structured JSON using four response types:

**tool_request**: Requests one of the six observation tools with specific parameters and a reasoning string explaining why the information is needed.

**action**: Specifies a batch of up to five actions to execute, along with the reasoning and expected outcome. Valid action types are `key_combo` (keyboard shortcuts), `type_text` (string input), and `wait` (pause in seconds, maximum 2.0).

**question**: Halts the autonomous loop, rings the terminal bell, and presents a question to the human operator. The operator's response is fed back into the next prompt.

**terminate**: Ends the mission with a summary and status (`completed`, `failed`, or `blocked`).

---

## Key Design Decisions

**Why not continue with GNOME on Wayland?** GNOME's animations, dynamic activities overview, and translucent overlays created visual ambiguity that forced excessive vision model calls. i3's deterministic tiling, absence of animations, and IPC interface provide both cleaner screenshots for the vision model and structured state data that often eliminates the need for screenshots entirely.

**Why keyboard-only navigation?** Mouse coordinate injection proved unreliable across display configurations and Wayland's scaling. More fundamentally, keyboard navigation is inherently deterministic: Ctrl+L always focuses the address bar in Firefox, regardless of screen resolution or window position.

**Why five tools instead of streaming events?** Continuous event streams would overwhelm the LLM's context window with noise. On-demand tools let the agent request exactly what it needs when it needs it, keeping prompts focused and reasoning clear.

**Why circuit breakers instead of trying to perfect the prompts?** Language models are inherently probabilistic and will occasionally produce unexpected output. Prompt engineering can reduce but not eliminate these failures. Circuit breakers provide a safety net that catches pathological loops regardless of their cause.

---

## Limitations and Future Work

The accessibility tree tool depends on application support for AT-SPI2, which varies. Firefox and most GTK applications expose rich trees, while some Electron applications and games provide minimal or no accessibility data.

YouTube and similar dynamic web applications present a challenge: their accessibility trees can be deep and complex, and the page structure changes as content loads. The agent sometimes needs guidance to locate specific elements like search boxes or video thumbnails.

The current implementation uses only keyboard input. Future versions may reintroduce mouse control for applications where keyboard navigation is insufficient, using i3's deterministic window positioning to calculate reliable click coordinates.

Local model support is a natural next step. A small local model handling verification decisions (confirming that a window opened, checking if text was entered) could eliminate the 1.5-second inter-request delay entirely, while the cloud model handles complex reasoning and planning.

---

## License

MIT
