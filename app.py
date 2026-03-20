import os
import json
import time
import sqlite3
import threading
import subprocess
import urllib.request
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, jsonify, request, Response, redirect, url_for

app = Flask(__name__)

DB_PATH = os.environ.get('DB_PATH', '/data/monitor.db')
PASSWORD = os.environ.get('MONITOR_PASSWORD', '')
PORT = int(os.environ.get('MONITOR_PORT', '8899'))


# ── Database ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        remote_ip TEXT NOT NULL,
        local_port INTEGER NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        hit_count INTEGER DEFAULT 1,
        UNIQUE(remote_ip, local_port)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS geo_cache (
        ip TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_conn_last ON connections(last_seen)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_conn_ip ON connections(remote_ip)')
    conn.commit()
    conn.close()


# ── Auth ──────────────────────────────────────────────────

def check_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not PASSWORD:
            return f(*args, **kwargs)
        token = request.cookies.get('monitor_token')
        if token == PASSWORD:
            return f(*args, **kwargs)
        if request.path.startswith('/api/'):
            return Response('Unauthorized', 401)
        return render_template('login.html')
    return wrapper


# ── Geolocation ───────────────────────────────────────────

_geo_lock = threading.Lock()


def get_geo(ip):
    conn = get_db()
    row = conn.execute('SELECT data FROM geo_cache WHERE ip = ?', (ip,)).fetchone()
    conn.close()
    if row:
        return json.loads(row['data'])

    with _geo_lock:
        # Double-check after acquiring lock
        conn = get_db()
        row = conn.execute('SELECT data FROM geo_cache WHERE ip = ?', (ip,)).fetchone()
        conn.close()
        if row:
            return json.loads(row['data'])

        try:
            url = f'http://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp&lang=zh-CN'
            req = urllib.request.Request(url, headers={'User-Agent': 'vps-monitor/1.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
            if data.get('status') == 'success':
                geo = {
                    'country': data.get('country', '未知'),
                    'region': data.get('regionName', ''),
                    'city': data.get('city', ''),
                    'isp': data.get('isp', ''),
                }
                conn = get_db()
                conn.execute(
                    'INSERT OR REPLACE INTO geo_cache (ip, data, updated_at) VALUES (?, ?, ?)',
                    (ip, json.dumps(geo, ensure_ascii=False), datetime.now().isoformat()))
                conn.commit()
                conn.close()
                return geo
        except Exception as e:
            print(f"Geo lookup error for {ip}: {e}")

        return {'country': '未知', 'region': '', 'city': '', 'isp': ''}


# ── Connection Parser ────────────────────────────────────

def parse_connections():
    try:
        result = subprocess.run(
            ['ss', '-tn', 'state', 'established'],
            capture_output=True, text=True, timeout=5)
        conns = []
        for line in result.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            local, remote = parts[2], parts[3]

            # Parse addresses (handle IPv6 bracket notation)
            def parse_addr(addr):
                if ']:' in addr:
                    ip = addr.rsplit(':', 1)[0].strip('[]')
                    port = int(addr.rsplit(':', 1)[1])
                else:
                    ip = addr.rsplit(':', 1)[0]
                    port = int(addr.rsplit(':', 1)[1])
                return ip, port

            remote_ip, remote_port = parse_addr(remote)
            _, local_port = parse_addr(local)

            if remote_ip in ('127.0.0.1', '::1', '0.0.0.0', '::'):
                continue

            conns.append({
                'remote_ip': remote_ip,
                'remote_port': remote_port,
                'local_port': local_port,
            })
        return conns
    except Exception as e:
        print(f"Parse error: {e}")
        return []


# ── Background Collector ─────────────────────────────────

def collector_loop():
    while True:
        try:
            conns = parse_connections()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn = get_db()
            c = conn.cursor()
            for cn in conns:
                c.execute('''INSERT INTO connections
                             (remote_ip, local_port, first_seen, last_seen, hit_count)
                             VALUES (?, ?, ?, ?, 1)
                             ON CONFLICT(remote_ip, local_port)
                             DO UPDATE SET last_seen = ?, hit_count = hit_count + 1''',
                          (cn['remote_ip'], cn['local_port'], now, now, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Collector error: {e}")
        time.sleep(15)


# ── API Routes ────────────────────────────────────────────

@app.route('/')
@check_auth
def index():
    return render_template('index.html')


@app.route('/api/system')
@check_auth
def api_system():
    # CPU load
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        load1, load5, load15 = float(parts[0]), float(parts[1]), float(parts[2])
        with open('/proc/stat') as f:
            cpu_count = sum(1 for l in f if l.startswith('cpu') and l[3:4].isdigit())
        cpu_pct = min(100, round(load1 / max(cpu_count, 1) * 100, 1))
    except:
        load1 = load5 = load15 = 0
        cpu_count = 1
        cpu_pct = 0

    # Memory
    try:
        mi = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')
                mi[k.strip()] = int(v.strip().split()[0]) * 1024
        mem_total = mi.get('MemTotal', 0)
        mem_used = mem_total - mi.get('MemAvailable', 0)
        mem_pct = round(mem_used / mem_total * 100, 1) if mem_total else 0
    except:
        mem_total = mem_used = 0
        mem_pct = 0

    # Disk
    try:
        s = os.statvfs('/')
        dk_total = s.f_blocks * s.f_frsize
        dk_used = dk_total - s.f_bavail * s.f_frsize
        dk_pct = round(dk_used / dk_total * 100, 1) if dk_total else 0
    except:
        dk_total = dk_used = 0
        dk_pct = 0

    # Network I/O
    try:
        rx = tx = 0
        with open('/proc/net/dev') as f:
            for line in f.readlines()[2:]:
                p = line.split()
                if p[0].rstrip(':') == 'lo':
                    continue
                rx += int(p[1])
                tx += int(p[9])
    except:
        rx = tx = 0

    # Uptime
    try:
        with open('/proc/uptime') as f:
            uptime = int(float(f.read().split()[0]))
    except:
        uptime = 0

    return jsonify({
        'cpu': {'percent': cpu_pct, 'cores': cpu_count,
                'load': [load1, load5, load15]},
        'memory': {'total': mem_total, 'used': mem_used, 'percent': mem_pct},
        'disk': {'total': dk_total, 'used': dk_used, 'percent': dk_pct},
        'network': {'rx': rx, 'tx': tx},
        'uptime': uptime,
    })


@app.route('/api/processes')
@check_auth
def api_processes():
    sort = request.args.get('sort', 'mem')  # 'mem' or 'cpu'
    try:
        # ps aux: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
        sort_key = '-pcpu' if sort == 'cpu' else '-pmem'
        result = subprocess.run(
            ['ps', 'aux', '--sort', sort_key],
            capture_output=True, text=True, timeout=5)
        procs = []
        for line in result.stdout.strip().split('\n')[1:16]:  # Top 15
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            cpu = float(parts[2])
            mem = float(parts[3])
            if cpu < 0.1 and mem < 0.1:
                continue
            procs.append({
                'user': parts[0],
                'pid': int(parts[1]),
                'cpu': cpu,
                'mem': mem,
                'rss': int(parts[5]) * 1024,  # KB to bytes
                'command': parts[10][:120],
            })
        return jsonify(procs)
    except Exception as e:
        print(f"Process list error: {e}")
        return jsonify([])


@app.route('/api/connections')
@check_auth
def api_connections():
    active = parse_connections()
    ips = set(c['remote_ip'] for c in active)
    geo_map = {ip: get_geo(ip) for ip in ips}
    for c in active:
        c['geo'] = geo_map.get(c['remote_ip'], {})
    return jsonify(active)


@app.route('/api/history')
@check_auth
def api_history():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '').strip()
    offset = (page - 1) * per_page

    conn = get_db()
    if search:
        where = 'WHERE remote_ip LIKE ?'
        params = (f'%{search}%',)
    else:
        where = ''
        params = ()

    rows = conn.execute(
        f'''SELECT remote_ip, local_port, first_seen, last_seen, hit_count
            FROM connections {where}
            ORDER BY last_seen DESC LIMIT ? OFFSET ?''',
        (*params, per_page, offset)).fetchall()
    total = conn.execute(
        f'SELECT COUNT(*) FROM connections {where}', params).fetchone()[0]
    conn.close()

    data = []
    for r in rows:
        data.append({
            'remote_ip': r['remote_ip'],
            'local_port': r['local_port'],
            'first_seen': r['first_seen'],
            'last_seen': r['last_seen'],
            'hit_count': r['hit_count'],
            'geo': get_geo(r['remote_ip']),
        })
    return jsonify({'data': data, 'total': total, 'page': page, 'per_page': per_page})


@app.route('/api/stats')
@check_auth
def api_stats():
    conn = get_db()
    unique_ips = conn.execute(
        'SELECT COUNT(DISTINCT remote_ip) FROM connections').fetchone()[0]
    total_records = conn.execute('SELECT COUNT(*) FROM connections').fetchone()[0]
    today = datetime.now().strftime('%Y-%m-%d')
    today_ips = conn.execute(
        "SELECT COUNT(DISTINCT remote_ip) FROM connections WHERE last_seen >= ?",
        (today,)).fetchone()[0]
    top_ips = conn.execute(
        '''SELECT remote_ip, SUM(hit_count) as hits, COUNT(DISTINCT local_port) as ports
           FROM connections GROUP BY remote_ip ORDER BY hits DESC LIMIT 10'''
    ).fetchall()
    conn.close()

    return jsonify({
        'unique_ips': unique_ips,
        'total_records': total_records,
        'today_ips': today_ips,
        'active_now': len(parse_connections()),
        'top_ips': [{'ip': r['remote_ip'], 'hits': r['hits'],
                     'ports': r['ports'], 'geo': get_geo(r['remote_ip'])} for r in top_ips],
    })


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return redirect('/')
    pwd = request.form.get('password', '')
    if pwd == PASSWORD:
        resp = redirect('/')
        resp.set_cookie('monitor_token', PASSWORD, max_age=86400 * 30, httponly=True)
        return resp
    return render_template('login.html', error=True)


@app.route('/logout')
def logout():
    resp = redirect('/')
    resp.delete_cookie('monitor_token')
    return resp


# ── Main ──────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    init_db()
    t = threading.Thread(target=collector_loop, daemon=True)
    t.start()
    print(f"VPS Monitor starting on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
