"""'Start with Windows' toggle (per-user Run key, same value the installer uses)."""
import sys

VALUE_NAME = "OSRS GE Toolkit"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _exe():
    return f'"{sys.executable}"' if getattr(sys, "frozen", False) else None


def is_enabled():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(on):
    if sys.platform != "win32" or not _exe():
        return False
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, _exe() + " --background")
        else:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except OSError:
                pass
    return True
