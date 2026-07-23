jarvis-v2/
├── jarvis.py           # Main agent (the big file above)
├── test_tools.py       # System compatibility tests
├── requirements.txt    # Python dependencies
└── README.md           # Documentation

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