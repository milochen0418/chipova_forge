# Chipova Forge: watchers that run inside Renode (IronPython 2.7, loaded with `include @hooks.py`).
#
# They write what the board does, stamped with Renode's virtual time, as JSON lines to
# /work/out/renode-events.jsonl, which the session agent follows:
#   {"src": "uart", "name": "console", "vt": 1.234567, "line": "READY ..."}
#   {"src": "led", "name": "alarm", "vt": 1.234567, "on": true}
# and add monitor commands the agent uses:
#   forge_watch_uart "<peripheral path>" "<name>"
#   forge_watch_led "<peripheral path>" "<name>"
#   forge_uart_send "<peripheral path>" "<text>"     (sends text + CR, as a terminal would)
#   forge_time                                       (prints the virtual time in seconds)
import clr
# Renode's C# compiler references the assemblies loaded so far: load the co-simulation plugin now,
# so Forge's own peripherals built on it (ForgeSpiChip.cs) compile.
clr.AddReference('CoSimulationPlugin')
from System import Object
from System.IO import File
from System.Threading import Monitor as _Lock

_OUT = '/work/out/renode-events.jsonl'
_lock = Object()
_keep = []  # the handlers, so nothing collects them


def _json_str(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"' or ch == '\\':
            out.append('\\' + ch)
        elif o < 32 or o > 126:
            out.append('\\u%04x' % o)
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def _emit(fields):
    parts = []
    for k, v in fields:
        if isinstance(v, bool):
            val = 'true' if v else 'false'
        elif isinstance(v, float):
            val = '%.6f' % v
        elif isinstance(v, (int, long)):
            val = str(v)
        else:
            val = _json_str(v)
        parts.append('%s: %s' % (_json_str(k), val))
    line = '{' + ', '.join(parts) + '}\n'
    _Lock.Enter(_lock)
    try:
        File.AppendAllText(_OUT, line)
    finally:
        _Lock.Exit(_lock)


def _vt(machine):
    return float(machine.LocalTimeSource.ElapsedVirtualTime.TotalSeconds)


def mc_forge_time():
    print('%.6f' % _vt(self.Machine))


def mc_forge_watch_uart(path, name):
    machine = self.Machine
    uart = machine[path]
    buf = []

    def on_char(c):
        ch = chr(c)
        if ch == '\n' or ch == '\r':
            if buf:
                _emit([('src', 'uart'), ('name', name), ('vt', _vt(machine)), ('line', ''.join(buf))])
                del buf[:]
        elif len(buf) < 400:
            buf.append(ch)

    uart.CharReceived += on_char
    _keep.append(on_char)


def mc_forge_watch_led(path, name):
    machine = self.Machine
    led = machine[path]

    def on_change(source, state):
        _emit([('src', 'led'), ('name', name), ('vt', _vt(machine)), ('on', bool(state))])

    led.StateChanged += on_change
    _keep.append(on_change)
    _emit([('src', 'led'), ('name', name), ('vt', _vt(machine)), ('on', bool(led.State))])


def mc_forge_uart_send(path, text):
    uart = self.Machine[path]
    for ch in text:
        uart.WriteChar(ord(ch))
    uart.WriteChar(13)
