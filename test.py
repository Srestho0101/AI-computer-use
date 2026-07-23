#!/usr/bin/env python3
"""
test_tools.py - Test all tools independently before running the full agent.
"""

import os
import sys
import time

def test_imports():
    """Test that all required packages are importable."""
    print("=" * 50)
    print("Testing imports...")
    print("=" * 50)
    
    modules = {
        "mistralai": "Mistral AI client",
        "mss": "Screen capture",
        "PIL": "Image processing",
        "evdev": "Input device monitoring",
        "i3ipc": "i3 window manager IPC",
        "pyatspi": "Accessibility tree",
        "json": "JSON parsing",
        "subprocess": "Command execution",
    }
    
    all_ok = True
    for module, description in modules.items():
        try:
            __import__(module)
            print(f"  ✅ {module} ({description})")
        except ImportError:
            print(f"  ❌ {module} ({description}) - NOT INSTALLED")
            all_ok = False
    
    return all_ok

def test_ydotool():
    """Test that ydotool is available."""
    print("\n" + "=" * 50)
    print("Testing ydotool...")
    print("=" * 50)
    
    import subprocess
    try:
        result = subprocess.run(["which", "ydotool"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✅ ydotool found at: {result.stdout.strip()}")
            
            # Check daemon
            result = subprocess.run(
                ["ydotool", "key", "28:1", "28:0"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                print("  ✅ ydotool daemon is running")
            else:
                print("  ⚠️  ydotool daemon may not be running")
                print("     Start with: sudo systemctl enable --now ydotoold")
            return True
        else:
            print("  ❌ ydotool not found in PATH")
            print("     Install with: sudo pacman -S ydotool")
            return False
    except Exception as e:
        print(f"  ❌ ydotool error: {e}")
        return False

def test_i3():
    """Test i3 connection."""
    print("\n" + "=" * 50)
    print("Testing i3 connection...")
    print("=" * 50)
    
    try:
        import i3ipc
        i3 = i3ipc.Connection()
        tree = i3.get_tree()
        focused = tree.find_focused()
        if focused:
            print(f"  ✅ Connected to i3")
            print(f"     Focused window: {focused.window_class} - \"{focused.name}\"")
            return True
        else:
            print("  ⚠️  Connected but no focused window found")
            return False
    except Exception as e:
        print(f"  ❌ i3 connection failed: {e}")
        print("     Make sure i3 is running")
        return False

def test_keyboard():
    """Test keyboard device detection."""
    print("\n" + "=" * 50)
    print("Testing keyboard detection...")
    print("=" * 50)
    
    try:
        import evdev
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        keyboards = []
        for device in devices:
            caps = device.capabilities()
            if evdev.ecodes.EV_KEY in caps:
                keys = caps[evdev.ecodes.EV_KEY]
                if evdev.ecodes.KEY_A in keys:
                    keyboards.append((device.path, device.name))
                device.close()
        
        if keyboards:
            print(f"  ✅ Found {len(keyboards)} keyboard(s):")
            for path, name in keyboards:
                print(f"     - {name} ({path})")
            return True
        else:
            print("  ⚠️  No keyboards found")
            return False
    except Exception as e:
        print(f"  ❌ Keyboard detection failed: {e}")
        return False

def test_tesseract():
    """Test Tesseract OCR availability."""
    print("\n" + "=" * 50)
    print("Testing Tesseract OCR...")
    print("=" * 50)
    
    import subprocess
    try:
        result = subprocess.run(["tesseract", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            print(f"  ✅ Tesseract available: {version_line}")
            return True
        else:
            print("  ❌ Tesseract not working")
            return False
    except FileNotFoundError:
        print("  ❌ Tesseract not installed")
        print("     Install with: sudo pacman -S tesseract tesseract-data-eng")
        return False

def test_api_key():
    """Test Mistral API key."""
    print("\n" + "=" * 50)
    print("Testing Mistral API key...")
    print("=" * 50)
    
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("  ❌ MISTRAL_API_KEY not set")
        print("     export MISTRAL_API_KEY='your-key-here'")
        return False
    
    print(f"  ✅ API key found (starts with: {api_key[:8]}...)")
    return True

def main():
    print("\n🧪 JARVIS v2 - System Compatibility Test\n")
    
    results = {
        "Python Imports": test_imports(),
        "Mistral API Key": test_api_key(),
        "ydotool": test_ydotool(),
        "i3 Window Manager": test_i3(),
        "Keyboard Device": test_keyboard(),
        "Tesseract OCR": test_tesseract(),
    }
    
    print("\n" + "=" * 50)
    print("RESULTS SUMMARY")
    print("=" * 50)
    
    all_pass = True
    for test, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_pass = False
        print(f"  {status}: {test}")
    
    print("\n" + "=" * 50)
    if all_pass:
        print("✅ All tests passed! You can run jarvis.py")
    else:
        print("⚠️  Some tests failed. Install missing dependencies:")
        print("   sudo pacman -S ydotool tesseract tesseract-data-eng python-evdev")
        print("   pip install mistralai mss Pillow i3ipc pyatspi")
    print("=" * 50)

if __name__ == "__main__":
    main()