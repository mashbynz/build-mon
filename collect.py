"""Runs on agents-home: piped over SSH as `python3 - <mode> [arg]`, prints JSON."""
import functools, json, os, pwd, re, subprocess, sys, time

SESSION = 'build-mon'
APT = ('sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -y'
       ' -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold')
JOBS = {
    'refresh': 'sudo -n apt-get update',
    'upgrade': f'sudo -n apt-get update && {APT} upgrade --with-new-pkgs',
    'release': 'sudo -n do-release-upgrade -f DistUpgradeViewNonInteractive',
}
APT_LINE = re.compile(r'(\S+)/(\S+) (\S+) \S+ \[upgradable from: (\S+)\]')


def out(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def read(path):
    with open(path) as f:
        return f.read()


@functools.lru_cache(None)
def user(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def cpu_jiffies():
    v = [int(x) for x in read('/proc/stat').split('\n', 1)[0].split()[1:9]]
    return sum(v), v[3] + v[4]  # total, idle + iowait


def proc_stats():
    stats = {}
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            s = read(f'/proc/{pid}/stat')
        except OSError:
            continue  # exited mid-scan
        f = s[s.rfind(')') + 2:].split()  # fields from #3 (state) on
        stats[pid] = (int(f[11]) + int(f[12]), s[s.find('(') + 1:s.rfind(')')], int(f[19]), int(f[21]))
    return stats  # pid -> (utime+stime, name, starttime, rss pages)


def net_bytes():
    for line in read('/proc/net/route').splitlines()[1:]:
        f = line.split()
        if f[1] == '00000000':  # default route = the "Ethernet" adapter
            s = f'/sys/class/net/{f[0]}/statistics/'
            return f[0], int(read(s + 'rx_bytes')), int(read(s + 'tx_bytes'))
    return None, 0, 0


def overview():
    (t0, i0), p0, n0, w0 = cpu_jiffies(), proc_stats(), net_bytes(), time.time()
    time.sleep(1)
    (t1, i1), p1, n1, w1 = cpu_jiffies(), proc_stats(), net_bytes(), time.time()
    dt, el = (t1 - t0) or 1, w1 - w0
    hz, page = os.sysconf('SC_CLK_TCK'), os.sysconf('SC_PAGE_SIZE')
    uptime = float(read('/proc/uptime').split()[0])
    mem = {k: int(v.split()[0]) * 1024 for k, v in (l.split(':') for l in read('/proc/meminfo').splitlines())}
    osr = dict(l.split('=', 1) for l in read('/etc/os-release').splitlines() if '=' in l)

    procs = []
    for pid, (ticks, name, start, rss) in p1.items():
        try:
            uid = os.stat(f'/proc/{pid}').st_uid
            cmd = read(f'/proc/{pid}/cmdline').replace('\0', ' ').strip()
        except OSError:
            continue
        procs.append({'pid': int(pid), 'name': name, 'user': user(uid),
                      'cpu': round((ticks - p0.get(pid, (0,))[0]) / dt * 100, 1),
                      'rss': rss * page, 'age': round(uptime - start / hz), 'cmd': cmd or f'[{name}]'})

    disks = []
    for line in out('df', '-B1', '--output=target,size,used', '-x', 'tmpfs', '-x', 'devtmpfs',
                    '-x', 'squashfs', '-x', 'overlay', '-x', 'efivarfs').splitlines()[1:]:
        mount, size, used = line.rsplit(None, 2)
        disks.append({'mount': mount, 'size': int(size), 'used': int(used)})

    return {
        'host': os.uname().nodename, 'os': osr.get('PRETTY_NAME', '').strip('"'),
        'kernel': os.uname().release, 'uptime': uptime, 'cpus': os.cpu_count(), 'load': os.getloadavg(),
        'cpu': round(100 * (1 - (i1 - i0) / dt), 1),
        'mem': {'total': mem['MemTotal'], 'used': mem['MemTotal'] - mem['MemAvailable'],
                'swapTotal': mem['SwapTotal'], 'swapUsed': mem['SwapTotal'] - mem['SwapFree']},
        'net': {'iface': n1[0], 'rx': (n1[1] - n0[1]) / el, 'tx': (n1[2] - n0[2]) / el},
        'disks': disks, 'procs': procs,
    }


def updates():
    pkgs = [dict(zip(('name', 'source', 'new', 'old'), m.groups()))
            for m in map(APT_LINE.match, out('apt', 'list', '--upgradable').splitlines()) if m]
    rel = re.search(r"New release '([^']+)' available", out('do-release-upgrade', '-c'))
    stamp = '/var/lib/apt/periodic/update-success-stamp'  # touched by every successful apt update
    return {'packages': pkgs, 'release': rel and rel.group(1),
            'reboot': os.path.exists('/var/run/reboot-required'),
            'checked': os.path.exists(stamp) and os.path.getmtime(stamp)}


def screen(name=SESSION):
    sessions = []
    for line in out('tmux', 'list-sessions', '-F', '#{session_name}\t#{pane_dead}').splitlines():
        n, dead = line.split('\t')
        sessions.append({'name': n, 'dead': dead == '1'})
    names = [s['name'] for s in sessions]
    if name not in names:
        name = names[0] if names else None
    text = name and out('tmux', 'capture-pane', '-p', '-J', '-S', '-500', '-t', f'={name}:').rstrip()
    return {'sessions': sessions, 'session': name, 'text': text}


def run(job):
    if job not in JOBS:
        sys.exit(f'Unknown job: {job}')
    if out('tmux', 'display-message', '-p', '-t', f'={SESSION}:', '#{pane_dead}').strip() == '0':
        sys.exit('An update job is already running. Watch it in the Screen tool.')
    subprocess.run(['tmux', 'kill-session', '-t', f'={SESSION}'], capture_output=True)
    cmd = f'{JOBS[job]}; rc=$?; echo; echo "[build-mon] {job} finished with exit code $rc"'
    # start a shell, keep the pane after exit, then swap in the job, so fast exits still leave output
    subprocess.run(['tmux', 'new-session', '-d', '-s', SESSION, '-x', '160', '-y', '48',
                    ';', 'set-option', '-w', '-t', f'={SESSION}:', 'remain-on-exit', 'on',
                    ';', 'respawn-pane', '-k', '-t', f'={SESSION}:', cmd], check=True)
    return {'started': job, 'session': SESSION}


def selftest():
    assert APT_LINE.match('vim/resolute-updates 2:9.1 amd64 [upgradable from: 2:9.0]').group(4) == '2:9.0'
    o = overview()
    assert 0 <= o['cpu'] <= 100 and 0 < o['mem']['used'] < o['mem']['total'] and o['disks']
    assert any(p['pid'] == os.getpid() for p in o['procs'])
    assert isinstance(updates()['packages'], list)
    screen()
    return {'ok': True}


if __name__ == '__main__':
    mode, *args = sys.argv[1:]
    modes = {'overview': overview, 'updates': updates, 'screen': screen, 'run': run,
             'selftest': selftest, 'ping': lambda: {'ok': True}}
    print(json.dumps(modes[mode](*args)))
