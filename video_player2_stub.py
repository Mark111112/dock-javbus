
# This is a stub for video_player2.py to ensure it's correctly packaged in the exe
import os
import sys

# Add current directory to module search path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Try to import the real video_player2 module
try:
    import video_player2
except ImportError as e:
    print(f"Cannot import video_player2 module: {e}")

# Make sure _MEIPASS is added to path if it exists (PyInstaller specific)
if hasattr(sys, '_MEIPASS'):
    meipass_path = sys._MEIPASS
    if meipass_path not in sys.path:
        sys.path.insert(0, meipass_path)
    print(f"Added PyInstaller _MEIPASS path: {meipass_path}")
    print(f"Files in _MEIPASS: {os.listdir(meipass_path)}")
