```
jarvis-v2/
├── brain.py           # Main agent (the big file above)
├── test.py       # System compatibility tests
├── requirements.txt    # Python dependencies
└── README.md           # Documentation
```

To run it:
```
# Install system dependencies
sudo pacman -S ydotool tesseract tesseract-data-eng python-evdev

# Enable ydotool daemon
sudo systemctl enable --now ydotoold

# Install Python dependencies
pip install mistralai mss Pillow i3ipc pyatspi

# Test everything works
python test_tools.py

# Run the agent
# export MISTRAL_API_KEY="your-key"
python jarvis.py "Open Firefox and search for Python tutorials"
```

Ydotool
```
systemctl --user enable --now ydotool.service
systemctl --user status ydotool.service
```


## Dual-Model Supervision and Loop Recovery

`brain.py` now separates model responsibilities to reduce loops without exhausting a free-tier Mistral key:

- `JARVIS_PLANNER_MODEL` defaults to `mistral-large-latest` and creates the initial plan.
- `JARVIS_WORKER_MODEL` defaults to `mistral-small-latest` and handles normal tool/action decisions.
- `JARVIS_SUPERVISOR_MODEL` defaults to the planner model and reviews repeated behavior plus every ten action cycles.
- `pixtral-12b` is still used only by `visual_screenshot`.

An action cycle is one executed action batch plus its settle/verification phase. Each cycle record stores the normalized action fingerprint, worker reasoning, expected outcome, pre/post focus state, tool observations, success status, and any supervisor directive. Three repeated fingerprints without meaningful state change trigger a supervisor review. Every ten cycles, the supervisor receives the previous ten records and may request up to five observation tools before returning a JSON review with `status`, `summary`, and `directive`.

URL navigation batches are guarded by a loading-aware settle gate. When the worker sends `Ctrl+l` or `Ctrl+t`, types a URL, and presses `Enter`, the controller waits for the configured settle window and records whether the focused window stabilized. The same URL batch cannot be repeated immediately; the worker receives a directive to wait or verify with another observation tool.

Interactive CLI mode keeps monitors and clients alive across objectives. A worker `terminate` response returns to the objective prompt with fresh session state. `Ctrl+C` stops monitors and exits the process.
