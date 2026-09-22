#!/usr/bin/env python3
"""
Emergency desktop icon recovery tool.

Recovers everything that apply_desktop_icons() in ransom.py changed:
  - .lnk shortcuts get their original IconLocation back
  - .url files get their original contents back
  - folder desktop.ini files are removed (or restored to their original content)
  - hidden original files are moved back out of hidden_originals/

Layers of recovery (tries each in order):
  1. Reads icon_backup.json from %LOCALAPPDATA%\\RansomIconState
  2. Reads .origin.json sidecars next to each hidden file
  3. If both are gone, scans hidden_originals/ and reconstructs from
     the matching .lnk targets.
  4. Sweeps orphaned .lnk files whose targets live under RansomIconState.
  5. Resets any .lnk still pointing at stop_sign.ico.

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
HIDDEN_MANIFEST_SUFFIX = '.origin.json'
STATE_DIR_NAME = 'RansomIconState'
ICON_FILE = 'stop_sign.ico'

_FILE_ATTRIBUTE_READONLY = 0x01
_FILE_ATTRIBUTE_HIDDEN   = 0x02
_FILE_ATTRIBUTE_SYSTEM   = 0x04

SILENT = '--silent' in sys.argv


# ---------------------------------------------------------------- logging / UI


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


# ---------------------------------------------------------------- paths


def _state_dir():
    return os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
        STATE_DIR_NAME,
    )


def _backup_path():
    return os.path.join(_state_dir(), BACKUP_FILENAME)


def _hidden_dir():
    return os.path.join(_state_dir(), HIDDEN_FOLDER_NAME)


def _sidecar_path(hidden_path):
    return hidden_path + HIDDEN_MANIFEST_SUFFIX


# ---------------------------------------------------------------- known folders


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


# ---------------------------------------------------------------- file attributes


def _set_attr(path, mask, on):
    attrs = windll.kernel32.GetFileAttributesW(str(path))
    if attrs == -1:
        return
    new = attrs | mask if on else attrs & ~mask
    windll.kernel32.SetFileAttributesW(str(path), new)


# ---------------------------------------------------------------- folder-icon API
# SHGetSetFolderCustomSettings is the same call Explorer's own
# Properties -> Customize -> Change Icon dialog uses. Calling it with an
# empty icon path is the canonical way to say "this folder has no custom
# icon" — it also invalidates Explorer's per-folder icon cache, which
# SHChangeNotify(SHCNE_UPDATEDIR) alone does not reliably do.

class _SHFOLDERCUSTOMSETTINGS(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('dwMask', wintypes.DWORD),
        ('pvid', ctypes.c_void_p),
        ('pszWebViewTemplate', ctypes.c_wchar_p),
        ('cchWebViewTemplate', wintypes.DWORD),
        ('pszWebViewTemplateVersion', ctypes.c_wchar_p),
        ('pszInfoTip', ctypes.c_wchar_p),
        ('cchInfoTip', wintypes.DWORD),
        ('pclsid', ctypes.c_void_p),
        ('cchCLSID', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('pszIconFile', ctypes.c_wchar_p),
        ('cchIconFile', wintypes.DWORD),
        ('iIconIndex', ctypes.c_int),
        ('pszLogo', ctypes.c_wchar_p),
        ('cchLogo', wintypes.DWORD),
    ]


_FCSM_ICONFILE = 0x00000010
_FCS_FORCEWRITE = 0x00000002


def _clear_folder_icon(folder):
    """Ask Explorer to forget any custom icon for this folder."""
    try:
        fcs = _SHFOLDERCUSTOMSETTINGS()
        fcs.dwSize = ctypes.sizeof(_SHFOLDERCUSTOMSETTINGS)
        fcs.dwMask = _FCSM_ICONFILE
        fcs.pszIconFile = None
        fcs.iIconCount = 0
        fcs.iIconIndex = 0
        res = windll.shell32.SHGetSetFolderCustomSettings(
            ctypes.byref(fcs), str(folder), _FCS_FORCEWRITE
        )
        return res == 0
    except Exception:
        return False


# ---------------------------------------------------------------- shell notify


_changed_dirs = []


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
    # Per-folder update so folder icons actually drop their cache.
    try:
        SHCNE_UPDATEDIR = 0x00001000
        SHCNF_PATHW = 0x0005
        for path in _changed_dirs:
            try:
                windll.shell32.SHChangeNotify(
                    SHCNE_UPDATEDIR, SHCNF_PATHW, str(path), None
                )
            except Exception:
                continue
    except Exception:
        pass


# ---------------------------------------------------------------- WScript shell


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

        result = _msgbox(
            'Recover Desktop',
            'Python processes are running.\n\n'
            'If the ransom program is still active, close it first, '
            'then click Yes.\n\nContinue anyway?',
            0x04 | 0x30,  # MB_YESNO | MB_ICONWARNING
        )
        if result != 6:  # IDYES
            sys.exit(1)
    except Exception as e:
        _log(f'process check failed (ignored): {e}')


# ---------------------------------------------------------------- recovery: layer 1


def _recover_from_backup(backup, shell):
    """Primary path: replay icon_backup.json in reverse."""
    recovered = 0
    remaining = dict(backup)

    # file:: entries first — they remove placeholder .lnks and put the
    # hidden originals back before anything else touches those folders.
    keys = sorted(
        backup.keys(),
        key=lambda k: 0 if k.startswith('file::') else 1,
    )

    for key in keys:
        value = backup[key]
        try:
            # -----------------------------------------------------
            # file::  — undo the hidden-file + placeholder .lnk swap
            # -----------------------------------------------------
            if key.startswith('file::'):
                info = value
                lnk = info.get('lnk_path')
                hidden = info.get('hidden_path')
                original_name = info.get('original_name')
                original_dir = info.get('original_dir')

                if lnk and os.path.exists(lnk):
                    _set_attr(lnk, _FILE_ATTRIBUTE_READONLY, False)
                    try:
                        os.remove(lnk)
                    except Exception as e:
                        _log(f'  cannot remove {lnk}: {e}')
                        continue

                if hidden and original_name and os.path.exists(hidden):
                    target_dir = original_dir or (
                        os.path.dirname(lnk) if lnk else None
                    )
                    if target_dir:
                        target = os.path.join(target_dir, original_name)
                        if os.path.exists(target):
                            _log(f'  target exists, skipping: {target}')
                        else:
                            shutil.move(hidden, target)
                            recovered += 1
                        # Clean up the sidecar that lived next to
                        # the hidden file.
                        sc = _sidecar_path(hidden)
                        try:
                            if os.path.exists(sc):
                                os.remove(sc)
                        except Exception:
                            pass
                    else:
                        _log(f'  no target dir for {hidden}, leaving in place')

                remaining.pop(key, None)

            # -----------------------------------------------------
            # lnk::  — restore the original IconLocation
            # -----------------------------------------------------
            elif key.startswith('lnk::'):
                lnk = key[5:]
                if os.path.exists(lnk):
                    sc = shell.CreateShortCut(lnk)
                    sc.IconLocation = value
                    sc.Save()
                    recovered += 1
                remaining.pop(key, None)

            # -----------------------------------------------------
            # url::  — restore the original file contents
            # -----------------------------------------------------
            elif key.startswith('url::'):
                url = key[5:]
                if os.path.exists(url):
                    _set_attr(url, _FILE_ATTRIBUTE_READONLY, False)
                    with open(url, 'w', encoding='utf-8') as f:
                        f.write(value)
                    recovered += 1
                remaining.pop(key, None)

            # -----------------------------------------------------
            # dir::  — remove or restore the folder's desktop.ini
            # -----------------------------------------------------
            elif key.startswith('dir::'):
                folder = key[5:]
                entry = value if isinstance(value, dict) else {
                    'had_ini': False, 'content': '', 'had_system': False
                }

                # Clear the folder's READONLY first — it blocks the
                # subsequent writes/deletes on some systems.
                if os.path.isdir(folder):
                    _set_attr(folder, _FILE_ATTRIBUTE_READONLY, False)

                ini = os.path.join(folder, 'desktop.ini')

                # Delete whatever ini is currently there.
                if os.path.exists(ini):
                    _set_attr(ini, _FILE_ATTRIBUTE_READONLY, False)
                    _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, False)
                    _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, False)
                    try:
                        os.remove(ini)
                    except Exception as e:
                        _log(f'  cannot remove {ini}: {e}')

                if entry.get('had_ini'):
                    # Put the original content back and re-apply the
                    # original attribute set.
                    try:
                        with open(ini, 'w', encoding='utf-8') as f:
                            f.write(entry.get('content', ''))
                        _set_attr(ini, _FILE_ATTRIBUTE_HIDDEN, True)
                        _set_attr(ini, _FILE_ATTRIBUTE_READONLY, True)
                        if entry.get('had_system'):
                            _set_attr(ini, _FILE_ATTRIBUTE_SYSTEM, True)
                    except Exception as e:
                        _log(f'  could not restore {ini}: {e}')
                else:
                    # We created the ini — tell Explorer to drop the
                    # custom folder icon so it refreshes immediately.
                    _clear_folder_icon(folder)

                if os.path.isdir(folder):
                    _changed_dirs.append(folder)

                recovered += 1
                remaining.pop(key, None)

        except Exception as e:
            _log(f'  failed on {key}: {e}')

    # Save the reduced backup so a re-run picks up where we left off.
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


# ---------------------------------------------------------------- recovery: layer 2b


def _recover_from_sidecars():
    """Read .origin.json sidecars directly.

    Works even if both the backup JSON and the placeholder .lnk files
    are gone — each hidden file carries its own origin metadata.
    """
    hidden_dir = _hidden_dir()
    if not os.path.isdir(hidden_dir):
        return 0
    recovered = 0
    for name in os.listdir(hidden_dir):
        if name.endswith(HIDDEN_MANIFEST_SUFFIX):
            continue
        hidden_path = os.path.join(hidden_dir, name)
        sidecar = _sidecar_path(hidden_path)
        if not os.path.exists(sidecar):
            continue
        try:
            with open(sidecar, 'r', encoding='utf-8') as f:
                info = json.load(f)
        except Exception:
            continue
        target = os.path.join(info['original_dir'], info['original_name'])
        if os.path.exists(target):
            continue
        try:
            shutil.move(hidden_path, target)
            try:
                os.remove(sidecar)
            except Exception:
                pass
            # Also remove the placeholder .lnk if it exists.
            lnk = info.get('lnk_path')
            if lnk and os.path.exists(lnk):
                try:
                    _set_attr(lnk, _FILE_ATTRIBUTE_READONLY, False)
                    os.remove(lnk)
                except Exception:
                    pass
            recovered += 1
        except Exception as e:
            _log(f'[sidecar] failed on {hidden_path}: {e}')
    return recovered


# ---------------------------------------------------------------- recovery: layer 2


def _recover_from_hidden_dir(shell):
    """Fallback: backup JSON is gone, but hidden originals survived.

    For each file in hidden_originals/, find the matching .lnk on any
    desktop and reverse the swap.
    """
    hidden_dir = _hidden_dir()
    if not os.path.isdir(hidden_dir):
        return 0

    files = [f for f in os.listdir(hidden_dir)
             if not f.endswith(HIDDEN_MANIFEST_SUFFIX)]
    if not files:
        return 0

    _log(f'fallback: reconstructing from {len(files)} hidden file(s).')
    recovered = 0

    for name in files:
        # convention: hidden file may be prefixed with `sid_` or `sid_n_`
        original_name = name
        if '_' in name:
            # strip the leading hex sid
            rest = name.split('_', 1)[1]
            # if there's a number prefix (sid_n_name), strip that too
            if '_' in rest:
                head, tail = rest.split('_', 1)
                if head.isdigit():
                    rest = tail
            if rest:
                original_name = rest

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
                    _set_attr(candidate_lnk, _FILE_ATTRIBUTE_READONLY, False)
                    os.remove(candidate_lnk)
                    dest = os.path.join(desktop, original_name)
                    if not os.path.exists(dest):
                        shutil.move(hidden_path, dest)
                        # Also try to remove any matching sidecar.
                        scp = _sidecar_path(hidden_path)
                        try:
                            if os.path.exists(scp):
                                os.remove(scp)
                        except Exception:
                            pass
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
                    try:
                        shutil.move(hidden_path, dest)
                        recovered += 1
                    except Exception:
                        pass

    return recovered


# ---------------------------------------------------------------- recovery: layer 3


def _sweep_orphan_lnks(shell):
    """Delete any .lnk whose target no longer exists AND whose target
    lives under a RansomIconState folder."""
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
                        _set_attr(full, _FILE_ATTRIBUTE_READONLY, False)
                        os.remove(full)
                        recovered += 1
            except Exception:
                continue
    return recovered


# ---------------------------------------------------------------- recovery: layer 4


def _sweep_stop_sign_icons(shell):
    """Reset any .lnk still pointing at stop_sign.ico."""
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

    # Layer 2b: sidecars
    n = _recover_from_sidecars()
    if n:
        _log(f'layer 2b (sidecars) recovered {n} item(s).')
        total += n

    # Layer 2: hidden_originals/ (fallback when sidecars are missing)
    n = _recover_from_hidden_dir(shell)
    if n:
        _log(f'layer 2 (hidden dir) recovered {n} item(s).')
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