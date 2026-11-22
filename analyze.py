#!/usr/bin/env python3
"""
RPi 5 Temperature Log Analyzer & Cooler Decision Engine
"""

import csv
import sys
import os
import math
import datetime
from collections import Counter

DECISION_NO_COOLER = "No Cooler Needed (Your current bare setup is fine)"
DECISION_PASSIVE_COOLER = "Passive Heatsink Sufficient (Great for silent, light-to-moderate thermal relief)"
DECISION_ACTIVE_COOLER = "Active Cooler Strongly Recommended (Prevent thermal throttling and performance degradation)"


def parse_csv_string(csv_text):
    records = []
    lines = csv_text.strip().splitlines()
    if not lines:
        return records
    reader = csv.DictReader(lines)
    for row in reader:
        try:
            records.append({
                'timestamp': row['timestamp'],
                'soc_temp': float(row['soc_temp_c']),
                'pmic_temp': float(row['pmic_temp_c']) if row['pmic_temp_c'] != 'N/A' else None,
                'load_1m': float(row['load_1m']),
                'arm_freq': int(row['arm_freq_mhz']),
                'throttled_hex': row['throttled_hex'],
                'top_proc': row['top_proc'],
                'top_cpu': float(row['top_cpu_pct'])
            })
        except (ValueError, KeyError):
            continue
    return records


def parse_csv_file(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', newline='') as f:
        content = f.read()
    return parse_csv_string(content)


def calculate_stats(vals):
    if not vals:
        return None
    sorted_vals = sorted(vals)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    avg = total / n
    vmin = sorted_vals[0]
    vmax = sorted_vals[-1]

    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0

    # Standard deviation
    variance = sum((x - avg) ** 2 for x in vals) / n
    std_dev = math.sqrt(variance)

    # 95th percentile
    p95_idx = int(math.ceil(0.95 * n)) - 1
    p95_idx = max(0, min(p95_idx, n - 1))
    p95 = sorted_vals[p95_idx]

    return {
        'count': n,
        'min': vmin,
        'max': vmax,
        'avg': avg,
        'median': median,
        'std_dev': std_dev,
        'p95': p95
    }


def bucket_temperatures(vals):
    n = len(vals)
    if n == 0:
        return {
            'under_60': {'count': 0, 'pct': 0.0},
            '60_to_70': {'count': 0, 'pct': 0.0},
            '70_to_80': {'count': 0, 'pct': 0.0},
            '80_to_85': {'count': 0, 'pct': 0.0},
            'over_85': {'count': 0, 'pct': 0.0},
        }

    c_under_60 = sum(1 for v in vals if v < 60.0)
    c_60_70 = sum(1 for v in vals if 60.0 <= v < 70.0)
    c_70_80 = sum(1 for v in vals if 70.0 <= v < 80.0)
    c_80_85 = sum(1 for v in vals if 80.0 <= v < 85.0)
    c_over_85 = sum(1 for v in vals if v >= 85.0)

    return {
        'under_60': {'count': c_under_60, 'pct': (c_under_60 / n) * 100},
        '60_to_70': {'count': c_60_70, 'pct': (c_60_70 / n) * 100},
        '70_to_80': {'count': c_70_80, 'pct': (c_70_80 / n) * 100},
        '80_to_85': {'count': c_80_85, 'pct': (c_80_85 / n) * 100},
        'over_85': {'count': c_over_85, 'pct': (c_over_85 / n) * 100},
    }


def bucket_frequencies(vals, max_freq=2400):
    n = len(vals)
    if n == 0:
        return {
            '2400_mhz': {'count': 0, 'pct': 0.0},
            '1500_mhz': {'count': 0, 'pct': 0.0},
            '1000_mhz': {'count': 0, 'pct': 0.0},
            '750_mhz': {'count': 0, 'pct': 0.0},
            '600_mhz': {'count': 0, 'pct': 0.0},
            'other': {'count': 0, 'pct': 0.0},
        }

    c_2400 = sum(1 for v in vals if v >= 2300)
    c_1500 = sum(1 for v in vals if 1400 <= v < 2300)
    c_1000 = sum(1 for v in vals if 900 <= v < 1400)
    c_750 = sum(1 for v in vals if 700 <= v < 900)
    c_600 = sum(1 for v in vals if v < 700 and v > 0)
    c_other = sum(1 for v in vals if v == 0)

    return {
        '2400_mhz': {'count': c_2400, 'pct': (c_2400 / n) * 100},
        '1500_mhz': {'count': c_1500, 'pct': (c_1500 / n) * 100},
        '1000_mhz': {'count': c_1000, 'pct': (c_1000 / n) * 100},
        '750_mhz': {'count': c_750, 'pct': (c_750 / n) * 100},
        '600_mhz': {'count': c_600, 'pct': (c_600 / n) * 100},
        'other': {'count': c_other, 'pct': (c_other / n) * 100},
    }


def calculate_performance_loss(freq_vals, max_freq=2400):
    if not freq_vals:
        return None

    valid_freqs = [f for f in freq_vals if f > 0]
    if not valid_freqs:
        return None

    avg_freq = sum(valid_freqs) / len(valid_freqs)
    performance_loss_pct = (1 - avg_freq / max_freq) * 100

    return {
        'avg_freq_mhz': avg_freq,
        'max_freq_mhz': max_freq,
        'performance_loss_pct': performance_loss_pct,
        'effective_speed_factor': avg_freq / max_freq,
    }


def decode_throttled_bits(hex_str):
    try:
        val = int(hex_str, 16)
    except ValueError:
        val = 0

    flags = []
    active_throttled = bool(val & 0x4)
    past_throttled = bool(val & 0x40000)
    active_soft_temp = bool(val & 0x8)
    past_soft_temp = bool(val & 0x80000)
    under_voltage_now = bool(val & 0x1)
    under_voltage_past = bool(val & 0x10000)

    if active_throttled:
        flags.append("Active: Currently throttled")
    if past_throttled:
        flags.append("Past: Throttling occurred since boot")
    if active_soft_temp:
        flags.append("Active: Soft temperature limit (80°C) reached")
    if past_soft_temp:
        flags.append("Past: Soft temperature limit (80°C) occurred")
    if under_voltage_now:
        flags.append("Active: Under-voltage detected (Check power supply!)")
    if under_voltage_past:
        flags.append("Past: Under-voltage occurred since boot")

    return {
        'val': val,
        'active_throttled': active_throttled,
        'past_throttled': past_throttled,
        'active_soft_temp_limit': active_soft_temp,
        'past_soft_temp_limit': past_soft_temp,
        'flags': flags
    }


def summarize_throttling(records):
    active_throttle_count = 0
    past_throttle_count = 0
    past_soft_temp_count = 0
    under_voltage_count = 0

    for r in records:
        decoded = decode_throttled_bits(r['throttled_hex'])
        if decoded['active_throttled']:
            active_throttle_count += 1
        if decoded['past_throttled']:
            past_throttle_count += 1
        if decoded['past_soft_temp_limit'] or decoded['active_soft_temp_limit']:
            past_soft_temp_count += 1
        if decoded['active_throttled'] or decoded['past_throttled']:
            pass

    return {
        'active_throttle_count': active_throttle_count,
        'past_throttle_count': past_throttle_count,
        'past_soft_temp_count': past_soft_temp_count
    }


def detect_culprit_app(detail):
    """Map a process detail string to a known app name.

    Detail may be 'comm|cpu|mem|args' (test hook) or the compact
    'comm(cpu%|mem:x%|args);...' snapshot from collect.sh.
    """
    if not detail:
        return 'unknown'
    low = str(detail).lower()
    for app in ('ollama', 'hermes', 'opencode', 'tailscaled', 'docker', 'apt'):
        if app in low:
            return app
    # Fall back to first comm token before '|' or '('
    token = str(detail).split('|')[0].split('(')[0].strip()
    token = token.split(';')[0].strip()
    return token if token else 'unknown'


def parse_spikes_string(csv_text):
    spikes = []
    if not csv_text or not csv_text.strip():
        return spikes
    reader = csv.DictReader(csv_text.strip().splitlines())
    for row in reader:
        try:
            spikes.append({
                'timestamp': row.get('timestamp', ''),
                'soc_temp': float(row.get('soc_temp_c', 0)),
                'baseline': float(row.get('baseline_temp_c', 0)),
                'delta': float(row.get('delta_c', 0)),
                'load': float(row.get('load_1m', 0)),
                'culprit_app': row.get('culprit_app', 'unknown') or 'unknown',
                'detail': row.get('top_processes_detail', ''),
            })
        except (ValueError, KeyError, AttributeError):
            continue
    return spikes


def parse_spikes_file(filepath):
    if not filepath or not os.path.exists(filepath):
        return []
    with open(filepath, 'r', newline='') as f:
        return parse_spikes_string(f.read())


def group_spikes_by_app(spikes):
    grouped = {}
    for s in spikes:
        app = s.get('culprit_app', 'unknown') or 'unknown'
        entry = grouped.setdefault(app, {'count': 0, 'max_temp': 0.0, 'max_delta': 0.0})
        entry['count'] += 1
        entry['max_temp'] = max(entry['max_temp'], float(s.get('soc_temp', 0)))
        entry['max_delta'] = max(entry['max_delta'], float(s.get('delta', 0)))
    return grouped


def frequency_weighted_note(spikes, total_samples):
    if not spikes:
        return ('No temperature spikes detected (no 5°C+ jumps over baseline). '
                'Verdict below reflects steady-state temps only.')
    total = max(int(total_samples or 0), 1)
    pct = len(spikes) / total * 100
    grouped = group_spikes_by_app(spikes)
    top = sorted(grouped.items(), key=lambda kv: kv[1]['count'], reverse=True)
    top_str = ', '.join(f'{app} ({info["count"]}x, peak {info["max_temp"]:.1f}°C)'
                        for app, info in top[:3])
    if pct < 1.0:
        return (f'Spikes are rare: {len(spikes)} spikes in {total} samples ({pct:.2f}% of time). '
                f'Culprits: {top_str}. Daily use is likely fine; heat is bursty/experimental.')
    return (f'Spikes are frequent: {len(spikes)} spikes in {total} samples ({pct:.2f}% of time). '
            f'Culprits: {top_str}. Heat recurs during normal use — cooling is warranted.')


def determine_verdict(stats, throttle_summary, buckets):
    if not stats:
        return {
            'decision': DECISION_NO_COOLER,
            'reasons': ["No data collected yet."]
        }

    reasons = []
    max_temp = stats['max']
    p95_temp = stats['p95']
    active_throttles = throttle_summary['active_throttle_count']
    soft_temp_pct = buckets['80_to_85']['pct'] + buckets['over_85']['pct']

    # Decision logic
    over_85_count = buckets.get('over_85', {}).get('count', 0)
    if max_temp >= 85.0 or active_throttles > 0 or over_85_count > 0:
        decision = DECISION_ACTIVE_COOLER
        reasons.append(f"Maximum temperature reached {max_temp:.1f}°C (>= 85°C thermal throttle threshold).")
        if active_throttles > 0:
            reasons.append(f"Recorded active throttling events ({active_throttles} samples).")
        if over_85_count > 0:
            reasons.append(f"Spent time above 85°C.")
    elif p95_temp >= 80.0 or soft_temp_pct > 1.0 or throttle_summary['past_soft_temp_count'] > 0:
        decision = DECISION_PASSIVE_COOLER
        reasons.append(f"95th percentile temperature is {p95_temp:.1f}°C (approaching/entering 80°C throttle range).")
        reasons.append(f"Spent {soft_temp_pct:.2f}% of time in the warm zone (>=80°C).")
        reasons.append("Passive heatsink will effectively shave 5-10°C off spikes without adding fan noise.")
    elif max_temp < 70.0 and p95_temp < 65.0:
        decision = DECISION_NO_COOLER
        reasons.append(f"Max temperature stayed low at {max_temp:.1f}°C, with p95 at {p95_temp:.1f}°C.")
        reasons.append("Agents and occasional tasks do not thermally stress your Pi 5 in this environment.")
    else:
        decision = DECISION_PASSIVE_COOLER
        reasons.append(f"Moderate thermal profile (max {max_temp:.1f}°C, avg {stats['avg']:.1f}°C).")
        reasons.append("A simple passive heatsink provides safe headroom during experiment bursts.")

    return {
        'decision': decision,
        'reasons': reasons
    }


# ---------------------------------------------------------------------------
# PiScope power / energy / battery helpers (additive: legacy API untouched)
# ---------------------------------------------------------------------------

def pmic_to_wall(pmic_w, factor=1.0):
    """Estimate wall power from PMIC rail power using a linear factor."""
    return float(pmic_w) * float(factor)


def calibrated_wall_w(pmic_w, slope=1.0, intercept=0.0):
    """Estimate wall power using a full linear calibration."""
    return float(slope) * float(pmic_w) + float(intercept)


def _iso_epoch(timestamp):
    """Parse an ISO-8601 UTC (``...Z``) timestamp to epoch seconds."""
    if not timestamp:
        return 0.0
    text = str(timestamp).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return float(dt.timestamp())
    except (ValueError, TypeError, OverflowError):
        return 0.0


def parse_power_log_string(csv_text):
    """Parse ``timestamp,pmic_w,wall_w`` CSV text into record dicts."""
    records = []
    if not csv_text or not csv_text.strip():
        return records
    reader = csv.DictReader(csv_text.strip().splitlines())
    for row in reader:
        try:
            timestamp = (row.get('timestamp') or '').strip()
            pmic_w = float(row['pmic_w'])
            wall_w = float(row['wall_w'])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        records.append({
            'timestamp': timestamp,
            'epoch': _iso_epoch(timestamp),
            'pmic_w': pmic_w,
            'wall_w': wall_w,
        })
    return records


def parse_power_log(filepath):
    """Read a power log from disk; missing/unreadable file -> empty list."""
    if not filepath or not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', newline='') as f:
            return parse_power_log_string(f.read())
    except OSError:
        return []


def energy_wh(samples, window_minutes=None):
    """Left-Riemann energy in Wh from ``(epoch, watts)`` pairs or records.

    The final sample contributes no trailing interval.  ``window_minutes``
    keeps only samples at/after ``last_epoch - window_minutes * 60``.
    """
    usable = []
    for item in samples or []:
        try:
            if isinstance(item, dict):
                epoch = float(item['epoch'])
                watts = float(item['wall_w'])
            else:
                epoch, watts = float(item[0]), float(item[1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        usable.append((epoch, watts))

    if len(usable) < 2:
        return 0.0

    if window_minutes is not None:
        last_epoch = max(epoch for epoch, _ in usable)
        cutoff = last_epoch - float(window_minutes) * 60.0
        usable = [(epoch, watts) for epoch, watts in usable if epoch >= cutoff]
        if len(usable) < 2:
            return 0.0

    total_watt_seconds = sum(
        watts * (usable[i + 1][0] - epoch)
        for i, (epoch, watts) in enumerate(usable[:-1])
    )
    return total_watt_seconds / 3600.0


def cost_dollars(energy_wh_value, rate_per_kwh=0.15):
    """Cost in dollars for ``energy_wh_value`` Wh at ``rate_per_kwh`` $/kWh."""
    return float(energy_wh_value) / 1000.0 * float(rate_per_kwh)


def battery_runtime_hours(capacity_wh, power_w):
    """Runtime in hours for a battery at a constant power draw."""
    if float(power_w) <= 0:
        return 0.0
    return float(capacity_wh) / float(power_w)


def battery_estimate(capacity_wh, avg_power_w, p95_power_w):
    """Runtime estimates at average and at conservative (p95) power."""
    avg_h = battery_runtime_hours(capacity_wh, avg_power_w)
    p95_h = battery_runtime_hours(capacity_wh, p95_power_w)
    return {
        'capacity_wh': float(capacity_wh),
        'avg_power_w': float(avg_power_w),
        'p95_power_w': float(p95_power_w),
        'avg_hours': avg_h,
        'p95_hours': p95_h,
        'conservative_hours': p95_h,
    }


def _power_section_lines(power_records, config):
    """Build the appended POWER / ENERGY / BATTERY report section."""
    cfg = config or {}
    rate = float(cfg.get('rate_per_kwh', 0.15))
    battery = float(cfg.get('battery_wh', 45.0))
    windows = cfg.get('energy_windows_min') or [60, 120]

    watts = [r['wall_w'] for r in power_records]
    wall_stats = calculate_stats(watts)
    last = power_records[-1]
    full_wh = energy_wh(power_records)
    cost = cost_dollars(full_wh, rate)

    if wall_stats and wall_stats['count'] >= 2:
        avg_w = wall_stats['avg']
        p95_w = wall_stats['p95']
    else:
        avg_w = p95_w = watts[0] if watts else 0.0
    estimate = battery_estimate(battery, avg_w, p95_w)

    lines = [
        '-' * 65,
        ' POWER / ENERGY / BATTERY:',
        f"   Live PMIC sum          : {last['pmic_w']:6.2f} W"
        f"   wall est. {last['wall_w']:.2f} W",
    ]
    for minutes in windows:
        wh = energy_wh(power_records, window_minutes=float(minutes))
        lines.append(f'   Energy ({minutes:g} min window) : {wh:.2f} Wh')
    lines.extend([
        f'   Energy (full log)      : {full_wh:.2f} Wh',
        f'   Cost (full log)        : ${cost:.5f} @ ${rate:.2f}/kWh',
        f'   Battery ({battery:g} Wh)     : ~{estimate["avg_hours"]:.1f} h @ avg '
        f'{avg_w:.2f} W  ·  ~{estimate["conservative_hours"]:.1f} h @ p95 '
        f'{p95_w:.2f} W (conservative)',
        '   NOTE: PMIC excludes USB/fan/regulator losses; set a wall factor for accuracy.',
    ])
    return lines


def build_report(log_file, power_log_file=None, config=None, range_spec=None):
    """Build and return the complete text report (does not print).

    Preserves every legacy README report field and, when a readable power log
    is supplied, appends a POWER / ENERGY / BATTERY section.
    """
    records = parse_csv_file(log_file)
    lines = []
    lines.append('=' * 65)
    lines.append(' THERMALSCOPE - TEMPERATURE & COOLER DECISION REPORT')
    lines.append('=' * 65)

    if not records:
        lines.append(f' Log File          : {log_file}')
        lines.append(' No valid temperature data collected yet.')
        lines.append('=' * 65)
        return '\n'.join(lines) + '\n'

    soc_temps = [r['soc_temp'] for r in records]
    pmic_temps = [r['pmic_temp'] for r in records if r['pmic_temp'] is not None]
    loads = [r['load_1m'] for r in records]
    arm_freqs = [r['arm_freq'] for r in records]

    stats = calculate_stats(soc_temps)
    pmic_stats = calculate_stats(pmic_temps) if pmic_temps else None
    load_stats = calculate_stats(loads)
    freq_stats = calculate_stats(arm_freqs)
    buckets = bucket_temperatures(soc_temps)
    freq_buckets = bucket_frequencies(arm_freqs)
    perf_loss = calculate_performance_loss(arm_freqs)
    throttle_summary = summarize_throttling(records)
    verdict = determine_verdict(stats, throttle_summary, buckets)

    proc_counter = Counter(r['top_proc'] for r in records)
    top_procs = proc_counter.most_common(5)

    spike_file = os.path.join(
        os.path.dirname(os.path.abspath(log_file)), 'spikes.csv')
    spikes = parse_spikes_file(spike_file)
    grouped = group_spikes_by_app(spikes)
    verdict['reasons'].append(
        frequency_weighted_note(spikes, stats['count']))

    lines.append(f' Log File          : {log_file}')
    if range_spec:
        lines.append(f' Range             : {range_spec}')
    lines.append(f" Total Samples     : {stats['count']}")
    lines.append(
        f" Time Span         : {records[0]['timestamp']} to {records[-1]['timestamp']}")
    lines.append('-' * 65)
    lines.append(' SoC TEMPERATURE STATS (°C):')
    lines.append(f"   Min             : {stats['min']:.1f}°C")
    lines.append(
        f"   Average         : {stats['avg']:.1f}°C "
        f"(± {stats['std_dev']:.1f}°C std dev)")
    lines.append(f"   Median          : {stats['median']:.1f}°C")
    lines.append(f"   95th Percentile : {stats['p95']:.1f}°C")
    lines.append(f"   Max             : {stats['max']:.1f}°C")
    if pmic_stats:
        lines.append(
            f" PMIC TEMP (Avg)   : {pmic_stats['avg']:.1f}°C "
            f"(Max: {pmic_stats['max']:.1f}°C)")
    lines.append(
        f" CPU LOAD (1m Avg) : {load_stats['avg']:.2f} "
        f"(Max: {load_stats['max']:.2f})")
    lines.append(
        f" ARM FREQUENCY (Avg): {freq_stats['avg']:.0f} MHz "
        f"(Max: {freq_stats['max']:.0f} MHz)")
    lines.append('-' * 65)
    lines.append(' TEMPERATURE DISTRIBUTION:')
    for b_name, b_info in [
        ('Under 60°C (Cool)', buckets['under_60']),
        ('60°C - 70°C (Normal)', buckets['60_to_70']),
        ('70°C - 80°C (Warm)', buckets['70_to_80']),
        ('80°C - 85°C (Soft Throttle Zone)', buckets['80_to_85']),
        ('Over 85°C (Hard Throttle / Critical)', buckets['over_85']),
    ]:
        bar = '#' * int(b_info['pct'] / 2)
        lines.append(
            f"   {b_name:<35} : {b_info['count']:5d} "
            f"({b_info['pct']:5.2f}%) {bar}")
    lines.append('-' * 65)
    lines.append(' ARM FREQUENCY DISTRIBUTION:')
    for b_name, b_info in [
        ('2400 MHz (Max)', freq_buckets['2400_mhz']),
        ('1500 MHz (Throttled)', freq_buckets['1500_mhz']),
        ('1000 MHz (Throttled)', freq_buckets['1000_mhz']),
        ('750 MHz (Throttled)', freq_buckets['750_mhz']),
        ('600 MHz (Min)', freq_buckets['600_mhz']),
        ('Other/Unknown', freq_buckets['other']),
    ]:
        bar = '#' * int(b_info['pct'] / 2)
        lines.append(
            f"   {b_name:<30} : {b_info['count']:5d} "
            f"({b_info['pct']:5.2f}%) {bar}")
    lines.append('-' * 65)
    lines.append(' PERFORMANCE IMPACT ANALYSIS:')
    if perf_loss:
        lines.append(
            f"   Average Frequency    : {perf_loss['avg_freq_mhz']:.0f} MHz / "
            f"{perf_loss['max_freq_mhz']:.0f} MHz max")
        lines.append(
            f"   Performance Loss    : "
            f"{perf_loss['performance_loss_pct']:.1f}% slower than max capability")
        lines.append(
            f"   Effective Speed     : "
            f"{perf_loss['effective_speed_factor']:.1%} of maximum")
        time_impact = 60 * (1 / perf_loss['effective_speed_factor'] - 1)
        lines.append(
            f'   Time Impact         : For a 1-hour task, thermal throttling added '
            f'~{time_impact:.0f} minutes')
    else:
        lines.append('   Insufficient frequency data for analysis.')
    lines.append('-' * 65)
    lines.append(
        ' TOP PROCESSES (Most frequently observed top CPU consumer):')
    for proc, count in top_procs:
        pct_of_time = (count / stats['count']) * 100
        lines.append(f'   {proc:<20} : {count:5d} samples ({pct_of_time:5.1f}%)')
    lines.append('-' * 65)
    lines.append(f" SPIKE ATTRIBUTION (5°C+ jumps, {len(spikes)} events):")
    if spikes:
        for app, info in sorted(
                grouped.items(), key=lambda kv: kv[1]['count'], reverse=True)[:5]:
            lines.append(
                f"   {app:<20} : {info['count']:5d} spikes "
                f"(peak {info['max_temp']:.1f}°C, +{info['max_delta']:.1f}°C)")
    else:
        lines.append('   No spikes recorded yet.')
    lines.append('-' * 65)
    lines.append(' FINAL VERDICT & RECOMMENDATION:')
    lines.append(f"   => {verdict['decision']}")
    lines.append('   Reasons:')
    for reason in verdict['reasons']:
        lines.append(f'      - {reason}')

    power_records = parse_power_log(power_log_file) if power_log_file else []
    if power_records:
        lines.extend(_power_section_lines(power_records, config))

    lines.append('=' * 65)
    return '\n'.join(lines) + '\n'


def main(argv=None):
    """CLI entry point.  Prints the report built by :func:`build_report`."""
    args = list(sys.argv[1:] if argv is None else argv)
    power_log_file = None
    range_spec = None
    log_file = None
    positional = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--power-log' and i + 1 < len(args):
            power_log_file = args[i + 1]
            i += 2
            continue
        if arg.startswith('--power-log='):
            power_log_file = arg.split('=', 1)[1]
        elif arg == '--range' and i + 1 < len(args):
            range_spec = args[i + 1]
            i += 2
            continue
        elif arg.startswith('--range='):
            range_spec = arg.split('=', 1)[1]
        elif arg in ('--summary', '-s'):
            pass
        elif not arg.startswith('-'):
            positional.append(arg)
        i += 1
    if positional:
        log_file = positional[0]
    if log_file is None:
        log_file = '/home/berry5/projects/temp/temp_log.csv'

    if not os.path.exists(log_file):
        print('=' * 65)
        print(' ERROR: Log file not found')
        print('=' * 65)
        print(f' Expected file: {log_file}')
        print('')
        print('No temperature data has been collected yet.')
        print('')
        print('To start collecting data, choose one of the following:')
        print('')
        print('  Quick test (2 minutes):')
        print('    ./collect.sh --dry-run')
        print('    ./collect.sh temp_log.csv')
        print('    python3 analyze.py temp_log.csv')
        print('')
        print('  1-hour test with progress:')
        print('    ./run-test.sh 60')
        print('')
        print('  Custom duration test (e.g., 8 hours):')
        print('    ./run-test.sh 480')
        print('')
        print('For more information, see README.md')
        print('=' * 65)
        return 1

    if power_log_file is None:
        candidate = os.path.join(
            os.path.dirname(os.path.abspath(log_file)), 'power_log.csv')
        if os.path.exists(candidate):
            power_log_file = candidate

    config = None
    try:
        import piscope_config
        config = piscope_config.load_config()
    except Exception:
        config = None

    sys.stdout.write(build_report(
        log_file, power_log_file=power_log_file,
        config=config, range_spec=range_spec))
    return 0


if __name__ == '__main__':
    sys.exit(main())
