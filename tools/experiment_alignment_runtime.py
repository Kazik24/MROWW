"""Untimed Windows loader/layout check for owned alignment artifacts: MANIFEST OUTPUT.

Uses the same absolute-path LoadLibraryW as the host. Never calls simulation entry points.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys
from experiment_alignment import read, write, pe_text

api = ctypes.WinDLL('kernel32', use_last_error=True)
api.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
api.LoadLibraryW.restype = wintypes.HMODULE
api.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
api.GetModuleFileNameW.restype = wintypes.DWORD
api.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
api.GetProcAddress.restype = ctypes.c_void_p
api.FreeLibrary.argtypes = [wintypes.HMODULE]
api.FreeLibrary.restype = wintypes.BOOL

records = []
handles = []
assert len(sys.argv) == 3, __doc__
try:
    # Same absolute-path LoadLibraryW API as the Rust host. Retain the seed module first.
    manifest = read(sys.argv[1])
    paths = [('seed', Path(manifest['seed']) / 'engine.dll', None)]
    paths += [(label, Path(row['directory']) / 'engine.dll', row) for label, row in manifest['artifacts'].items()
              if label in ('production', 'flip64', '128-32-32-0', '128-32-0-128')]
    for label, path, metadata in paths:
        path = path.resolve()
        handle = api.LoadLibraryW(str(path))
        assert handle, ctypes.WinError(ctypes.get_last_error())
        handles.append(handle)
        name = ctypes.create_unicode_buffer(32768)
        assert api.GetModuleFileNameW(handle, name, len(name))
        assert Path(name.value) == path, (name.value, path)
        rva, code = pe_text(path.read_bytes())
        runtime_code = ctypes.string_at(handle + rva, len(code))
        tick = api.GetProcAddress(handle, b'ne_tick')
        assert tick
        record = dict(config=label, path=str(path), loaded_path=name.value, base=handle,
                      tick_address=tick, tick_rva=tick-handle,
                      text_sha256=hashlib.sha256(code).hexdigest(),
                      loaded_text_sha256=hashlib.sha256(runtime_code).hexdigest())
        assert record['text_sha256'] == record['loaded_text_sha256'], 'Loaded instructions differ'
        if metadata:
            record['helpers'] = {name: dict(address=handle+row['rva'], modulo64=(handle+row['rva']) % 64)
                                 for name, row in metadata['layout'].items() if row['emitted']}
        records.append(record)
    assert len(set(handles)) == len(handles), 'Loader aliased distinct absolute module paths'
finally:
    for handle in reversed(handles):
        assert api.FreeLibrary(handle)
write(sys.argv[2],
      dict(scope='separate untimed diagnostic process; seed and variants retained together; not sampled benchmark addresses', modules=records))
print(f'Checked {len(records)} loaded images: correct paths, distinct handles, exact instruction bytes')
