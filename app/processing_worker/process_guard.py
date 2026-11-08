"""Owned-process containment, without a PID-based kill interface or SDK."""
import os


class OwnedProcesses:
    def __init__(self):
        self.handle = None
        if os.name == 'nt':
            import ctypes as c
            from ctypes import wintypes as w
            class Basic(c.Structure):
                _fields_=[('process_time',c.c_int64),('job_time',c.c_int64),('flags',w.DWORD),
                          ('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),
                          ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
            class Info(c.Structure):
                _fields_=[('basic',Basic),('io',c.c_uint64*6),('process_memory',c.c_size_t),
                          ('job_memory',c.c_size_t),('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
            k=c.WinDLL('kernel32',use_last_error=True)
            k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
            k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
            k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
            k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
            self.kernel=k;self.handle=k.CreateJobObjectW(None,None)
            if not self.handle:raise OSError('Process containment unavailable')
            info=Info();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(self.handle,9,c.byref(info),c.sizeof(info)):
                self.close();raise OSError('Process containment unavailable')
        elif os.name != 'posix':
            raise OSError('Unsupported supervised platform')

    def add(self, process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle,process.sentinel):
            raise OSError('Owned process could not be contained')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle);self.handle=None


def child_parent_guard(expected_parent):
    """Linux direct-child parent death; Windows relies on parent's Job Object.

    A child waits for parent IPC before executing. No inherited DB session exists
    with spawn. No arbitrary process identifier is terminated here.
    """
    if os.name == 'posix':
        import ctypes,signal,sys
        if not sys.platform.startswith('linux'):
            raise OSError('Parent death containment requires Linux')
        if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)!=0:
            raise OSError('Parent death containment unavailable')
        if os.getppid()!=expected_parent:os._exit(75)


def reap(process, grace, force):
    """Only a Process object created/retained by this supervisor is accepted."""
    if process is None or process.pid is None:return True
    process.join(grace)
    if process.is_alive():process.terminate();process.join(force)
    if process.is_alive():process.kill();process.join(force)
    return not process.is_alive()


def resident_bytes(process):
    """Sample an owned process, not an OS memory limit or allocation guarantee."""
    if os.name=='nt':
        import ctypes as c
        from ctypes import wintypes as w
        class Counters(c.Structure):
            _fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(n,c.c_size_t) for n in
                ('peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
        api=c.WinDLL('psapi',use_last_error=True).GetProcessMemoryInfo
        api.argtypes=[w.HANDLE,c.c_void_p,w.DWORD];api.restype=w.BOOL
        counters=Counters();counters.cb=c.sizeof(counters)
        if not api(process.sentinel,c.byref(counters),c.sizeof(counters)):raise OSError('Owned memory observation unavailable')
        return counters.ws
    with open(f'/proc/{process.pid}/status') as source:
        for line in source:
            if line.startswith('VmRSS:'):return int(line.split()[1])*1024
    raise OSError('Owned memory observation unavailable')
