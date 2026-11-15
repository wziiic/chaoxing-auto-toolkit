"""Run real hidden PowerShell helpers and watch their visible console windows."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess,sys,threading,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from windows_process import background_options


def main():
    api=ctypes.windll.user32;kernel=ctypes.windll.kernel32
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    api.EnumWindows.argtypes=[callback,wintypes.LPARAM]
    api.IsWindowVisible.argtypes=[wintypes.HWND]
    api.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    api.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('pid',wintypes.DWORD),('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),('name',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD];kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    children=set();visible=[];stop=threading.Event()
    def parent(pid):
        handle=kernel.CreateToolhelp32Snapshot(2,0)
        if handle==ctypes.c_void_p(-1).value:return None
        try:
            row=Entry();row.dwSize=ctypes.sizeof(row);ok=kernel.Process32FirstW(handle,ctypes.byref(row))
            while ok:
                if row.pid==pid:return row.parent
                ok=kernel.Process32NextW(handle,ctypes.byref(row))
        finally:kernel.CloseHandle(handle)
    @callback
    def visit(hwnd,_):
        if not api.IsWindowVisible(hwnd):return True
        cls=ctypes.create_unicode_buffer(80);api.GetClassNameW(hwnd,cls,80)
        if cls.value!='ConsoleWindowClass':return True
        pid=wintypes.DWORD();api.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value in children or parent(pid.value) in children:visible.append(int(hwnd))
        return True
    def monitor():
        while not stop.is_set():api.EnumWindows(visit,0);stop.wait(.015)
    worker=threading.Thread(target=monitor,daemon=True);worker.start()
    try:
        for _ in range(3):
            child=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-Command',"[Console]::Write('42'); Start-Sleep -Milliseconds 350"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,**background_options())
            children.add(child.pid);out,err=child.communicate(timeout=15)
            assert child.returncode==0 and out==b'42',(child.returncode,err[:100])
        assert not visible,'A helper console became visible'
    finally:stop.set();worker.join(timeout=2)
    print('PASS: three real PowerShell helpers completed with output preserved and no visible console window')


if __name__=='__main__':main()
