"""
api.py - Flask Web API 服务
重心：流量统计查询，支持日期范围筛选
"""

import logging
import os
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory

logger = logging.getLogger('sentinel.api')


def fmt_bytes(b: int) -> str:
    if b is None: b = 0
    b = int(b)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def create_app(db, capture):
    app = Flask(__name__, static_folder='static')
    app.config['JSON_SORT_KEYS'] = False

    @app.route('/')
    def index():
        return send_from_directory('static', 'index.html')

    # ── 首屏汇总（今日/本月/本年 + 内存增量）────────────────────────────────────
    @app.route('/api/summary')
    def api_summary():
        today_db = db.get_today_stats()
        month_db = db.get_month_stats()
        year_db  = db.get_year_stats()

        # 取内存快照（线程安全），避免裸读时 flush_and_get() 并发 clear() 造成空读
        mem       = capture.stats.get_hourly_snapshot()
        # 严格基于容器本地时间（已由 app.py 调用 time.tzset() 激活 TZ 变量）
        now       = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        month_str = now.strftime('%Y-%m')
        year_str  = now.strftime('%Y')

        t_up = t_dn = m_up = m_dn = y_up = y_dn = 0
        for k, v in mem.items():
            if k.startswith(today_str): t_up += v['up']; t_dn += v['down']
            if k.startswith(month_str): m_up += v['up']; m_dn += v['down']
            if k.startswith(year_str):  y_up += v['up']; y_dn += v['down']

        def stat(db_row, du, dd):
            u = (db_row.get('up_bytes')   or 0) + du
            d = (db_row.get('down_bytes') or 0) + dd
            return {'up_bytes': u, 'down_bytes': d, 'total_bytes': u+d,
                    'up_fmt': fmt_bytes(u), 'down_fmt': fmt_bytes(d), 'total_fmt': fmt_bytes(u+d)}

        return jsonify({
            'today':  stat(today_db, t_up, t_dn),
            'month':  stat(month_db, m_up, m_dn),
            'year':   stat(year_db,  y_up, y_dn),
        })

    # ── 日期范围查询（核心接口）──────────────────────────────────────────────────
    @app.route('/api/query')
    def api_query():
        """
        参数:
          start       YYYY-MM-DD（必填）
          end         YYYY-MM-DD（必填）
          granularity hour|day|month（默认 day）
        """
        start = request.args.get('start', '')
        end   = request.args.get('end',   '')
        gran  = request.args.get('granularity', 'day')

        if not start or not end:
            return jsonify({'error': 'start and end are required'}), 400
        try:
            datetime.strptime(start, '%Y-%m-%d')
            datetime.strptime(end,   '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400
        if gran not in ('hour', 'day', 'month'):
            gran = 'day'

        result = db.query_range(start, end, gran)

        # 若查询范围包含今天，叠加内存增量到今天那条
        # 使用 datetime.now() 而非 date.today()，两者在 tzset() 后等价，但保持一致性
        today_str = datetime.now().strftime('%Y-%m-%d')
        if start <= today_str <= end and gran == 'day':
            mem   = capture.stats.get_hourly_snapshot()  # 线程安全快照
            mem_u = mem_d = 0
            for k, v in mem.items():
                if k.startswith(today_str):
                    mem_u += v['up']; mem_d += v['down']
            if mem_u or mem_d:
                for row in result['series']:
                    if row.get('day') == today_str:
                        row['up_bytes']    = (row.get('up_bytes')    or 0) + mem_u
                        row['down_bytes']  = (row.get('down_bytes')  or 0) + mem_d
                        row['total_bytes'] = row['up_bytes'] + row['down_bytes']
                        break
                result['summary']['up_bytes']    += mem_u
                result['summary']['down_bytes']  += mem_d
                result['summary']['total_bytes'] += mem_u + mem_d

        # 格式化 summary
        s = result['summary']
        s['up_fmt']    = fmt_bytes(s['up_bytes'])
        s['down_fmt']  = fmt_bytes(s['down_bytes'])
        s['total_fmt'] = fmt_bytes(s['total_bytes'])
        return jsonify(result)

    # ── 最近30天 ─────────────────────────────────────────────────────────────
    @app.route('/api/history/30days')
    def api_history_30days():
        days = db.get_last_30days()
        return jsonify({'days': days})

    # ── 最近12个月 ───────────────────────────────────────────────────────────
    @app.route('/api/history/12months')
    def api_history_12months():
        months = db.get_last_12months()
        return jsonify({'months': months})

    # ── 今日24小时分布 ────────────────────────────────────────────────────────
    @app.route('/api/history/today_hours')
    def api_today_hours():
        hours = db.get_hourly_today()
        return jsonify({'hours': hours})

    # ── 数据库可用日期范围 ─────────────────────────────────────────────────────
    @app.route('/api/date_range')
    def api_date_range():
        return jsonify(db.get_available_date_range())

    # ── 实时速率（降为辅助接口，仅保留当前速率，不再是主角）──────────────────────
    @app.route('/api/realtime')
    def api_realtime():
        samples = capture.get_realtime(seconds=60)
        cur_up = cur_down = 0
        if samples:
            last = samples[-1]
            cur_up, cur_down = last['up'], last['down']
        return jsonify({
            'samples': samples[-30:],   # 只返回最近30个点，够显示迷你图即可
            'current_up_bps': cur_up * 8,
            'current_down_bps': cur_down * 8,
            'current_up_Bps': cur_up,
            'current_down_Bps': cur_down,
        })

    # ── TOP IP ────────────────────────────────────────────────────────────────
    @app.route('/api/top_ips')
    def api_top_ips():
        top = capture.get_top_ips(10)
        for item in top:
            item['bytes_fmt'] = fmt_bytes(item['bytes'])
        return jsonify({'top_ips': top})

    @app.route('/api/health')
    def api_health():
        return jsonify({'status': 'ok', 'ts': datetime.now().isoformat()})


    @app.route('/api/debug/local_ips')
    def api_debug_local_ips():
        """
        调试接口：查看程序当前检测到的本机 IP 地址列表及 LAN /56 前缀过滤器。
        用于验证公网 IPv6 是否被正确识别，确认上下行方向判断是否准确。
        访问：http://<NAS_IP>:<PORT>/api/debug/local_ips
        """
        ips = sorted(capture.local_ips)
        v4 = [ip for ip in ips if ':' not in ip]
        v6 = [ip for ip in ips if ':' in ip]

        # 读取当前生效的 LAN 过滤前缀（手动或自动检测的 /56）
        with capture._local_ips_lock:
            lan_prefixes = [str(n) for n in capture._lan_prefixes]
        manual_mode = capture._manual_mode

        return jsonify({
            'iface': os.environ.get('MONITOR_IFACE', 'eth0'),
            'ipv4': v4,
            'ipv6': v6,
            'total': len(ips),
            'ipv6_lan_filter': {
                'mode': 'manual' if manual_mode else 'auto-gua-/56',
                'prefixes': lan_prefixes,
                'note': (
                    '手动模式：由 EXCLUDE_IPV6_PREFIX 环境变量指定，优先级最高。'
                    if manual_mode else
                    '自动模式：从网卡 GUA 提取 /56 前缀，每小时刷新一次。'
                    '双端地址同属此前缀的 IPv6 包将被视为 LAN 内部流量并排除。'
                ),
            },
            'note': (
                '上行/下行方向判断依赖 ipv6 列表中的本机地址。'
                '若 NAS 的公网 IPv6 未出现，请检查 MONITOR_IFACE 是否正确。'
            ),
        })

    return app
