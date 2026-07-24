# 🤖 JARVIS: Cloud Vision Desktop Agent (Mistral Edition)

> **Status:** 🚀 Active
> **Environment:** Arch Linux, GNOME (Wayland/X11)
> **LLM Backend:** Mistral `pixtral-12b` (Cloud API)

An autonomous, multimodal AI desktop agent engineered specifically for low-spec hardware (e.g., 4GB RAM, older dual-core CPUs). Instead of relying on heavy local Vision-Language Models (VLMs) that exhaust system swap space, this architecture offloads cognitive processing to the Mistral cloud. It captures lossless desktop frames, reasons about the UI state, and executes keyboard-driven navigation via kernel-level input injection.

---

## ✨ Core Features

* **Zero-Disk Perception:** Uses `mss` to capture native 1080p display frames directly into system RAM as raw PNG bytes, bypassing SSD wear and ensuring the AI can read small terminal fonts.
* **Keyboard-Only Wayland Navigation:** Bypasses Wayland's absolute mouse-coordinate scaling issues by forcing the AI to navigate GNOME entirely via standard keyboard shortcuts (Super, Enter, Ctrl+N).
* **Kernel-Level Input Injection:** Translates string-based AI commands (e.g., `super`, `ctrl+n`) into raw hardware scancodes executed via the `ydotool` daemon.
* **Built-in API Rate Shielding:** Hardcoded 12-second execution throttling prevents exhausting the free-tier Mistral API limits (5 requests/minute).
* **Auditory Human-in-the-Loop:** Automatically pauses the autonomous loop and fires a terminal bell (`\a`) when the AI requires human permission, clarification, or password entry.
* **Sandboxed Execution:** Designed to be run in a dedicated, isolated `ai-worker` Linux user account to completely protect personal data and host system integrity.

---

## 🛠️ Installation & Setup

### 1. System Dependencies (Arch Linux)

Install the required kernel-level input injector:

```bash
sudo pacman -S ydotool

```

Enable the system daemon and set the global socket path for your user:

```bash
sudo systemctl enable --now ydotoold
export YDOTOOL_SOCKET=/run/ydotoold.socket

```

*(Note: Add `export YDOTOOL_SOCKET=/run/ydotoold.socket` to your `~/.bashrc` to make it persistent).*

Ensure your user is in the `input` group:

```bash
sudo usermod -aG input $USER

```

### 2. The Security Sandbox (Highly Recommended)

To prevent the agent from accidentally modifying your personal files if it hallucinates, create a dedicated AI worker account:

```bash
sudo useradd -m -G video,input ai-worker
sudo passwd ai-worker

```

Log into this user on a separate TTY (e.g., `Ctrl + Alt + F3`) and start a fresh GNOME session to run the script.

### 3. Python Environment

Create a virtual environment and install the required packages:

```bash
python -m venv .venv
source .venv/bin/activate
pip install mss mistralai

```

---

## 🎮 How to Use

1. Export your free Mistral API key:
```bash
export MISTRAL_API_KEY="your_api_key_here"

```


2. Run the agent loop:
```bash
python agent_cloud_loop.py

```


3. When prompted, provide a natural language objective (e.g., *"Open VS Code, create a new python file, and write a hello world script"*).
4. Switch to your target workspace. The agent will begin its capture-analyze-execute loop.

---

## 🛑 Challenges Faced & Engineering Solutions

Building an agentic loop on a 4GB RAM machine navigating a Wayland desktop presented severe physical and software roadblocks. Here is how they were engineered away:

| The Challenge | The Root Cause | The Engineered Solution |
| --- | --- | --- |
| **The RAM Death Spiral** | Local VLMs (like Moondream) or LLMs (like Qwen) consumed all 4GB of physical memory, triggering a swap-space loop that pushed execution times past 200 seconds per frame. | **Cloud Offloading:** Migrated the intelligence layer to Mistral's `pixtral-12b` API. This freed up 100% of local RAM, reducing loop times to ~1-3 seconds plus network latency. |
| **"Black Screen" Blindness** | Initial optimizations heavily compressed the screenshots into low-quality JPEGs to save API token costs, destroying UI contrast and rendering terminal text unreadable to the AI. | **Lossless High-Res Capture:** Removed OpenCV compression entirely. Switched the `mss` pipeline to capture crisp, uncompressed base64 PNGs directly from RAM. |
| **Broken Mouse Injection** | Wayland's display scaling misinterpreted `ydotool` absolute $X,Y$ mouse coordinates, violently throwing the cursor into GNOME's "Hot Corner" and triggering unintended menus. | **Keyboard-Only Navigation:** Removed mouse execution entirely. Rewrote the system prompt to force the AI to use keyboard shortcuts (Super $\rightarrow$ Type App Name $\rightarrow$ Enter). |
| **Silent `ydotool` Failures** | The `ydotool` client ignored standard string keys (like `super` or `ctrl+n`), resulting in silent command drops where nothing happened on screen. | **Raw Scancode Translator:** Built a hardcoded Python dictionary that intercepts AI shortcut strings and translates them into raw kernel down/up scancodes (e.g., `125:1 125:0`). |
| **Visual Hallucination Loops** | The AI would execute a shortcut, assume the app opened instantly, and begin blindly typing code into the wrong window (e.g., typing Python into a Bash terminal). | **Strict Verification Prompts:** Updated the system prompt with explicit GNOME behavioral rules. Forced the AI to visually verify that an application's UI had successfully rendered on the screen *before* interacting with it. |
| **API Rate Limit Bans** | The Mistral Free Tier limits requests to 1 per second and a maximum of 5 per minute. | **Algorithmic Throttling:** Programmed a mandatory 12-second sleep delta into the main loop to dynamically pad execution times, guaranteeing the rate limit is never breached. |
