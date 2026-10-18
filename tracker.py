import ctypes
import time
import psutil
from ctypes import wintypes

# Windows API libraries 

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.QueryUnbiasedInterruptTime.argtypes = [
    ctypes.POINTER(ctypes.c_ulonglong)
]
kernel32.QueryUnbiasedInterruptTime.restype = wintypes.BOOL


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_time() -> float:
    """Return seconds since the last keyboard or mouse input."""
    last_input = LASTINPUTINFO()
    last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(last_input)):
        raise ctypes.WinError(ctypes.get_last_error())

    current_tick = kernel32.GetTickCount64() & 0xFFFFFFFF
    elapsed_ms = (current_tick - last_input.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def get_system_sleep_time() -> float:
    """Return cumulative seconds the system has spent asleep since boot."""
    unbiased_interrupt_time = ctypes.c_ulonglong()
    if not kernel32.QueryUnbiasedInterruptTime(
        ctypes.byref(unbiased_interrupt_time)
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    uptime_seconds = kernel32.GetTickCount64() / 1000.0
    awake_seconds = unbiased_interrupt_time.value / 10_000_000.0
    return uptime_seconds - awake_seconds

def get_active_window_info():
    """Get the app name, window title, and executable path of the active window."""
    #1. Here I handle the case where there is no active window (hwnd is None)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "Unknown", "Unknown", ""
    
    #2. Get the names of the title 
    lenght = user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(lenght + 1)
    user32.GetWindowTextW(hwnd, buff, lenght + 1)
    window_title = buff.value if buff.value else "Unknown"

    #3. Get the process ID of the window
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    #4. Basically translate the process ID to the actual executable name of the application
    app_name = "Unknown"
    executable_path = ""
    try:
        process = psutil.Process(pid.value)
        app_name = process.name()
        try:
            executable_path = process.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return app_name, window_title, executable_path


def get_active_window_title():
    """Get the app name and window title of the currently active window."""
    app_name, window_title, _ = get_active_window_info()
    return app_name, window_title

if __name__ == "__main__":
    print("Testing active window title retrieval...")

    for i in range(5):
        app_name, window_title = get_active_window_title()
        print(f"Active Window: {app_name} - {window_title}")
        time.sleep(2)  # Wait for 2 seconds before checking again