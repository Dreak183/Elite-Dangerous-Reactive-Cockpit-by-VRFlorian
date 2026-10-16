"""Standalone Windows/Elite input experiment. No dependencies or file changes."""
import ctypes as C
import os
import time
import random

def make_wind_sequence():
    # Reproducible irregular timing allows like-for-like tests of input methods.
    rng = random.Random(20261010)
    holds = [rng.uniform(1.5, 3.0) for _ in range(48)]
    gaps = [rng.uniform(0.12, 0.5) for _ in range(47)]
    remaining = 120.0 - sum(holds) - sum(gaps)
    for i in range(len(holds)):
        adjustment = max(1.5 - holds[i], min(3.0 - holds[i], remaining))
        holds[i] += adjustment
        remaining -= adjustment
        if abs(remaining) < 1e-9:
            break
    assert abs(remaining) < 1e-9
    return tuple(zip(holds, gaps + [0.0]))


WIND = make_wind_sequence()


class Stopped(Exception):
    pass


def resolve_key(key):
    key = key.strip().upper()
    scans = (0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49)
    if key.startswith('NUM') and len(key) == 4 and key[3] in '0123456789':
        digit = int(key[3])
        return 0x60 + digit, scans[digit]
    if len(key) == 1 and key in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
        return ord(key), None
    named = {'SPACE':(0x20,0x39),'TAB':(0x09,0x0F),'ENTER':(0x0D,0x1C),
             'BACKSPACE':(0x08,0x0E),'LEFT':(0x25,0xE04B),'RIGHT':(0x27,0xE04D),
             'UP':(0x26,0xE048),'DOWN':(0x28,0xE050),'HOME':(0x24,0xE047),
             'END':(0x23,0xE04F),'INSERT':(0x2D,0xE052),'DELETE':(0x2E,0xE053),
             'PAGEUP':(0x21,0xE049),'PAGEDOWN':(0x22,0xE051)}
    if key in named:return named[key]
    if key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=12:
        n=int(key[1:]);return 0x6F+n, (0x3A+n if n<=10 else 0x57+n-11)
    raise ValueError('Use a plain letter, digit, numpad digit, arrow, navigation, space, tab, enter or F1-F12 key.')


class WindowsInput:
    def __init__(self, key, method='virtual'):
        if method not in ('virtual', 'legacy', 'scan'):
            raise ValueError('Unknown input method')
        self.method = method
        from ctypes import wintypes as W
        self.u = C.WinDLL('user32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        # INPUT's union must include MOUSEINPUT for the correct size/alignment.
        class KEYBDINPUT(C.Structure):
            _fields_ = [('vk', W.WORD), ('scan', W.WORD), ('flags', W.DWORD),
                        ('time', W.DWORD), ('extra', C.c_size_t)]
        class MOUSEINPUT(C.Structure):
            _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('data', W.DWORD),
                        ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]
        class HARDWAREINPUT(C.Structure):
            _fields_ = [('msg', W.DWORD), ('lo', W.WORD), ('hi', W.WORD)]
        class UNION(C.Union):
            _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]
        class INPUT(C.Structure):
            _fields_ = [('type', W.DWORD), ('data', UNION)]
        assert C.sizeof(INPUT) == (40 if C.sizeof(C.c_void_p) == 8 else 28)
        self.INPUT = INPUT
        self.u.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
        self.u.SendInput.restype = W.UINT
        self.u.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, C.c_size_t]
        self.u.keybd_event.restype = None
        self.u.GetAsyncKeyState.argtypes = [C.c_int]
        self.u.GetAsyncKeyState.restype = C.c_short
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.u.GetWindowThreadProcessId.restype = W.DWORD
        self.u.MapVirtualKeyW.argtypes = [W.UINT, W.UINT]
        self.u.MapVirtualKeyW.restype = W.UINT
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.W = W
        self.vk, fixed_scan = resolve_key(key)
        self.scan = fixed_scan if fixed_scan is not None else self.u.MapVirtualKeyW(self.vk, 0)
        self.extended = self.scan > 0xFF
        self.scan &= 0xFF
        if not self.scan:
            raise RuntimeError('Cannot map this key to a scan code.')

    def pressed(self, vk):
        return bool(self.u.GetAsyncKeyState(vk) & 0x8000)

    def focused(self):
        pid = self.W.DWORD()
        window = self.u.GetForegroundWindow()
        self.u.GetWindowThreadProcessId(window, C.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            buf = C.create_unicode_buffer(32768)
            size = self.W.DWORD(len(buf))
            if not self.k.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
                return False
            return os.path.basename(buf.value).lower() in ('elitedangerous64.exe', 'elitedangerous.exe')
        finally:
            self.k.CloseHandle(handle)

    def key_busy(self):
        # With Num Lock off, keypad keys can report navigation virtual keys.
        if 0x60 <= self.vk <= 0x69:
            navigation = (0x2D, 0x23, 0x28, 0x22, 0x25, 0x0C, 0x27, 0x24, 0x26, 0x21)
            return self.pressed(self.vk) or self.pressed(navigation[self.vk - 0x60])
        return self.pressed(self.vk)

    def modifiers(self):
        return any(self.pressed(vk) for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def send(self, down):
        if self.method == 'legacy':
            self.u.keybd_event(self.vk, self.scan, (0x0001 if self.extended else 0) | (0 if down else 0x0002), 0)
            return  # This older API has no success/failure return value.
        event = self.INPUT()
        event.type = 1
        event.data.ki.vk = self.vk if self.method == 'virtual' else 0
        event.data.ki.scan = self.scan if self.method == 'scan' else 0
        event.data.ki.flags = (0x0008 if self.method == 'scan' else 0) | (0x0001 if self.extended else 0) | (0 if down else 0x0002)
        if self.u.SendInput(1, C.byref(event), C.sizeof(event)) != 1:
            raise RuntimeError('Windows rejected the input (error %s). Check application privilege levels.' % C.get_last_error())

