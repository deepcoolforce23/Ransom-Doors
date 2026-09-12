#!/usr/bin/env python3
"""
Emergency desktop icon recovery tool.

Recovers everything that apply_desktop_icons() in ransom.py changed:
  - .lnk shortcuts get their original IconLocation back
  - .url files get their original contents back
  - folder desktop.ini files are removed, read-only cleared
  - hidden original files are moved back out of hidden_originals/

Layers of recovery (tries each in order):
  1. Reads icon_backup.json from %LOCALAPPDATA%\\RansomIconState
  2. If missing/corrupt, scans hidden_originals/ and reconstructs
  3. If hidden_originals/ is also gone, scans for orphaned .lnk files
     whose targets point into a RansomIconState folder.
  4. Resets any .lnk still pointing at stop_sign.ico.

Run with:
    python recover_desktop.py            (console, prompts)
    python recover_desktop.py --silent   (no prompts, auto-continue)

Build to .exe:
    pyinstaller --onefile --console --name RecoverDesktop recover_desktop.py
    pyinstaller --onefile --windowed --name RecoverDesktop recover_desktop.py

NOT A GUARANTEED, ONE-SHOT, NO-LOSS, ALL-IS-FINE-AND-GOOD, PERFECT SOLUTION TO YOUR PREDICAMENT.
AGAIN, it is HEAVILY recommended to run this program in a Virtual Machine, which will prevent your REAL COMPUTER from getting
affected.

Link to a good VM I know:
https://www.virtualbox.org/

You can also use others, just make sure its using Windows 10/11 as the virtual OS.
"""

import os
import sys
import json
import shutil
import ctypes
from ctypes import windll, wintypes

# ---------------------------------------------------------------- constants

BACKUP_FILENAME = 'icon_backup.json'
HIDDEN_FOLDER_NAME = 'hidden_originals'
STATE_DIR_NAME = 'RansomIconState'
ICON_FILE = 'stop_sign.ico'

SILENT = '--silent' in sys.argv


# ---------------------------------------------------------------- helpers


def _msgbox(title, text, flags=0x40):
    """0x40 = MB_ICONINFORMATION, 0x04 = MB_YESNO,
    0x30 = MB_ICONWARNING, 0x10 = MB_ICONERROR"""
    try:
        return windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        return 0


def _log(msg):
    if not SILENT:
        try:
            print(f'[recover] {msg}')
        except Exception:
            pass


def _state_dir():
    d = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
        STATE_DIR_NAME,
    )
    return d


def _backup_path():
    return os.path.join(_state_dir(), BACKUP_FILENAME)


def _hidden_dir():
    return os.path.join(_state_dir(), HIDDEN_FOLDER_NAME)


def _known_folder_desktop():
    """Ask Windows for the real Desktop path (handles OneDrive redirect)."""
    class GUID(ctypes.Structure):
        _fields_ = [
            ('Data1', wintypes.DWORD),
            ('Data2', wintypes.WORD),
            ('Data3', wintypes.WORD),
            ('Data4', ctypes.c_ubyte * 8),
        ]

    try:
        guid = GUID(
            3032468538, 56108, 16972,
            (ctypes.c_ubyte * 8)(176, 41, 127, 233, 154, 135, 198, 65),
        )
        path_ptr = ctypes.c_wchar_p()
        result = windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            path = path_ptr.value
            windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass
    return None


def _desktop_folders():
    folders = []
    real = _known_folder_desktop()
    if real and os.path.isdir(real):
        folders.append(real)
    public = os.path.join(
        os.environ.get('PUBLIC', 'C:\\Users\\Public'), 'Desktop'
    )
    if os.path.isdir(public) and public not in folders:
        folders.append(public)
    naive = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop')
    if os.path.isdir(naive) and naive not in folders:
        folders.append(naive)
    return folders


def _set_attr(path, mask, on):
    attrs = windll.kernel32.GetFileAttributesW(str(path))
    if attrs == -1:
        return
    new = attrs | mask if on else attrs & ~mask
    windll.kernel32.SetFileAttributesW(str(path), new)


def _refresh_shell():
    try:
        os.system('ie4uinit.exe -show')
    except Exception:
        pass
    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except Exception:
        pass


def _get_shell():
    import win32com.client
    return win32com.client.Dispatch('WScript.Shell')


def _kill_ransom_if_running():
    """Best-effort: warn if python processes (the ransom program) are alive."""
    try:
        import subprocess

        def _tasklist(image):
            try:
                return subprocess.run(
                    ['tasklist', '/FI', f'IMAGENAME eq {image}',
                     '/FO', 'CSV', '/NH'],
                    capture_output=True, text=True,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                ).stdout.lower()
            except Exception:
                return ''

        combined = _tasklist('pythonw.exe') + _tasklist('python.exe')
        if 'python' not in combined:
            return

        _log('WARNING: python/pythonw processes detected.')

        if SILENT:
            _log('--silent set, continuing anyway.')
            return

        # MB_YESNO = 0x04, MB_ICONWARNING = 0x30
        result = _msgbox(
            'Recover Desktop',
            'Python processes are running.\n\n'
            'If the ransom program is still active, close it first, '
            'then click Yes.\n\nContinue anyway?',
            0x04 | 0x30,
        )
        if result != 6:  # IDYES
            sys.exit(1)
    except Exception as e:
        _log(f'process check failed (ignored): {e}')


# ---------------------------------------------------------------- recovery paths


def _recover_from_backup(backup, shell):
    """Primary path: replay icon_backup.json in reverse."""
    recovered = 0
    remaining = dict(backup)

    # files first — they restore the actual hidden originals
    keys = sorted(
        backup.keys(),
        key=lambda k: 0 if k.startswith('file::') else 1,
    )

    for key in keys:
        value = backup[key]
        try:
            if key.startswith('file::'):
                info = value
                lnk = info.get('lnk_path')
                hidden = info.get('hidden_path')
                original_name = info.get('original_name')

                if lnk and os.path.exists(lnk):
                    os.remove(lnk)

                if hidden and original_name and os.path.exists(hidden):
                    target = os.path.join(
                        os.path.dirname(lnk), original_name
                    )
                    if os.path.exists(target):
                        _log(f'  target exists, skipping: {target}')
                    else:
                        shutil.move(hidden, target)
                        recovered += 1
                remaining.pop(key, None)

            elif key.startswith('lnk::'):
                lnk = key[5:]
                if os.path.exists(lnk):
                    sc = shell.CreateShortCut(lnk)
                    sc.IconLocation = value
                    sc.Save()
                    recovered += 1
                remaining.pop(key, None)

            elif key.startswith('url::'):
                url = key[5:]
                if os.path.exists(url):
                    with open(url, 'w', encoding='utf-8') as f:
                        f.write(value)
                    recovered += 1
                remaining.pop(key, None)

            elif key.startswith('dir::'):
                folder = key[5:]
                ini = os.path.join(folder, 'desktop.ini')
                if os.path.exists(ini):
                    _set_attr(ini, 2, False)  # unhide
                    try:
                        os.remove(ini)
                    except Exception:
                        pass
                if os.path.isdir(folder):
                    _set_attr(folder, 1, False)  # clear read-only
                recovered += 1
                remaining.pop(key, None)

        except Exception as e:
            _log(f'  failed on {key}: {e}')

    # save the reduced backup (so a re-run picks up where we left off)
    try:
        if remaining:
            with open(_backup_path(), 'w', encoding='utf-8') as f:
                json.dump(remaining, f, indent=2)
        else:
            if os.path.exists(_backup_path()):
                os.remove(_backup_path())
    except Exception:
        pass

    return recovered


def _recover_from_hidden_dir(shell):
    """Fallback: backup JSON is gone, but hidden originals survived.

    For each file in hidden_originals/, find the matching .lnk on any
    desktop and reverse the swap.
    """
    hidden_dir = _hidden_dir()
    if not os.path.isdir(hidden_dir):
        return 0

    files = os.listdir(hidden_dir)
    if not files:
        return 0

    _log(f'fallback: reconstructing from {len(files)} hidden file(s).')
    recovered = 0

    for name in files:
        # our convention: hidden file may be prefixed with `id_`
        original_name = name
        if '_' in name:
            maybe = name.split('_', 1)[1]
            if maybe:
                original_name = maybe

        hidden_path = os.path.join(hidden_dir, name)
        placed = False

        for desktop in _desktop_folders():
            candidate_lnk = os.path.join(desktop, original_name + '.lnk')
            if not os.path.exists(candidate_lnk):
                continue
            try:
                sc = shell.CreateShortCut(candidate_lnk)
                target = sc.TargetPath
                if os.path.normcase(target) == os.path.normcase(hidden_path):
                    os.remove(candidate_lnk)
                    dest = os.path.join(desktop, original_name)
                    if not os.path.exists(dest):
                        shutil.move(hidden_path, dest)
                        recovered += 1
                        placed = True
                        break
            except Exception:
                continue

        if not placed:
            desktops = _desktop_folders()
            if desktops:
                dest = os.path.join(desktops[0], original_name)
                if not os.path.exists(dest):
                    shutil.move(hidden_path, dest)
                    recovered += 1

    return recovered


def _sweep_orphan_lnks(shell):
    """Last resort: no backup, no hidden originals.

    Delete any .lnk on the desktop whose target no longer exists AND
    whose target lives under a RansomIconState folder.
    """
    recovered = 0
    for desktop in _desktop_folders():
        try:
            names = os.listdir(desktop)
        except Exception:
            continue
        for name in names:
            if not name.lower().endswith('.lnk'):
                continue
            full = os.path.join(desktop, name)
            try:
                sc = shell.CreateShortCut(full)
                target = sc.TargetPath or ''
                if STATE_DIR_NAME.lower() in target.lower():
                    if not os.path.exists(target):
                        os.remove(full)
                        recovered += 1
            except Exception:
                continue
    return recovered


def _sweep_stop_sign_icons(shell):
    """Extra safety net: any .lnk that still points at stop_sign.ico
    gets its icon reset to the target's own icon."""
    recovered = 0
    for desktop in _desktop_folders():
        try:
            names = os.listdir(desktop)
        except Exception:
            continue
        for name in names:
            if not name.lower().endswith('.lnk'):
                continue
            full = os.path.join(desktop, name)
            try:
                sc = shell.CreateShortCut(full)
                icon_loc = (sc.IconLocation or '').lower()
                if ICON_FILE.lower() in icon_loc:
                    sc.IconLocation = ''
                    sc.Save()
                    recovered += 1
            except Exception:
                continue
    return recovered


# ---------------------------------------------------------------- main


def main():
    _log(f'state dir: {_state_dir()}')

    if not os.path.isdir(_state_dir()):
        _log('no RansomIconState folder found — nothing to recover.')
        _refresh_shell()
        return 0

    _kill_ransom_if_running()
    shell = _get_shell()

    total = 0

    # Layer 1: backup JSON
    backup = None
    bp = _backup_path()
    if os.path.exists(bp):
        try:
            with open(bp, 'r', encoding='utf-8') as f:
                backup = json.load(f)
            _log(f'layer 1: loaded backup ({len(backup)} entries).')
        except Exception as e:
            _log(f'layer 1: backup corrupt ({e}), skipping.')
            backup = None

    if backup:
        n = _recover_from_backup(backup, shell)
        total += n
        _log(f'layer 1 recovered {n} item(s).')
    else:
        _log('layer 1: no usable backup JSON.')

    # Layer 2: hidden_originals/
    n = _recover_from_hidden_dir(shell)
    if n:
        _log(f'layer 2 recovered {n} item(s).')
        total += n

    # Layer 3: orphaned .lnk sweep
    n = _sweep_orphan_lnks(shell)
    if n:
        _log(f'layer 3 removed {n} orphaned shortcut(s).')
        total += n

    # Layer 4: reset any remaining stop_sign-iconed shortcuts
    n = _sweep_stop_sign_icons(shell)
    if n:
        _log(f'layer 4 reset {n} leftover stop-sign icon(s).')
        total += n

    # Clean up empty hidden dir
    hd = _hidden_dir()
    try:
        if os.path.isdir(hd) and not os.listdir(hd):
            os.rmdir(hd)
    except Exception:
        pass

    _refresh_shell()
    _log(f'done. {total} item(s) recovered.')

    if total == 0:
        _log('If your icons still look wrong, sign out and back in '
             '(Explorer caches icons aggressively).')

    return total


if __name__ == '__main__':
    try:
        total = main()
        if not SILENT:
            if total > 0:
                _msgbox(
                    'Recover Desktop',
                    f'Recovery complete.\n\n'
                    f'{total} item(s) restored.\n\n'
                    'If icons still look wrong, sign out and back in.',
                    0x40,
                )
            else:
                _msgbox(
                    'Recover Desktop',
                    'Nothing to recover.\n\n'
                    'No RansomIconState data was found, or the desktop '
                    'is already clean.',
                    0x40,
                )
    except KeyboardInterrupt:
        _log('interrupted.')
        sys.exit(130)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _log(tb)
        if not SILENT:
            _msgbox('Recover Desktop - ERROR', tb[:2000], 0x10)
        sys.exit(1)