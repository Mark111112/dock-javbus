
import os
import sys
import vlc

def setup_vlc_paths():
    """Setup VLC paths to ensure Python-VLC can find the VLC media player"""
    # Try to find VLC installation paths
    vlc_paths = [
        # Windows paths
        "C:\\Program Files\\VideoLAN\\VLC",
        "C:\\Program Files (x86)\\VideoLAN\\VLC",
        # Linux paths
        "/usr/lib/vlc",
        "/usr/local/lib/vlc",
        # macOS paths
        "/Applications/VLC.app/Contents/MacOS",
    ]
    
    # Check if VLC path is already set in environment variables
    if 'VLC_PLUGIN_PATH' in os.environ:
        return
    
    # Try to get path from vlc module
    try:
        instance = vlc.Instance()
        # If instance creation succeeds, VLC is properly configured
        instance.release()
        return
    except:
        pass
    
    # Try to set VLC path
    for path in vlc_paths:
        if os.path.exists(path):
            if sys.platform.startswith('win'):
                os.environ['VLC_PLUGIN_PATH'] = path + "\\plugins"
            else:
                os.environ['VLC_PLUGIN_PATH'] = path + "/plugins"
            break

# Execute when program starts
if __name__ == "__main__":
    setup_vlc_paths()
