#!/usr/bin/env python3
"""
RPi 5 Temperature Log Analyzer & Cooler Decision Engine
"""

import csv
import sys
import os
import math
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


def main():
    log_file = sys.argv[1] if len(sys.argv) > 1 else '/home/berry5/projects/temp/temp_log.csv'
    records = parse_csv_file(log_file)

    if not records:
        print(f"No log records found or file empty: {log_file}")
        print("Run collect.sh first to gather temperature data.")
        sys.exit(1)

    soc_temps = [r['soc_temp'] for r in records]
    pmic_temps = [r['pmic_temp'] for r in records if r['pmic_temp'] is not None]
    loads = [r['load_1m'] for r in records]

    stats = calculate_stats(soc_temps)
    pmic_stats = calculate_stats(pmic_temps) if pmic_temps else None
    load_stats = calculate_stats(loads)
    buckets = bucket_temperatures(soc_temps)
    throttle_summary = summarize_throttling(records)
    verdict = determine_verdict(stats, throttle_summary, buckets)

    # Top processes frequency
    proc_counter = Counter(r['top_proc'] for r in records)
    top_procs = proc_counter.most_common(5)

    print("=" * 65)
    print(" RPi 5 TEMPERATURE & COOLER DECISION REPORT")
    print("=" * 65)
    print(f" Log File          : {log_file}")
    print(f" Total Samples     : {stats['count']}")
    print(f" Time Span         : {records[0]['timestamp']} to {records[-1]['timestamp']}")
    print("-" * 65)
    print(" SoC TEMPERATURE STATS (°C):")
    print(f"   Min             : {stats['min']:.1f}°C")
    print(f"   Average         : {stats['avg']:.1f}°C")
    print(f"   Median          : {stats['median']:.1f}°C")
    print(f"   95th Percentile : {stats['p95']:.1f}°C")
    print(f"   Max             : {stats['max']:.1f}°C")
    if pmic_stats:
        print(f" PMIC TEMP (Avg)   : {pmic_stats['avg']:.1f}°C (Max: {pmic_stats['max']:.1f}°C)")
    print(f" CPU LOAD (1m Avg) : {load_stats['avg']:.2f} (Max: {load_stats['max']:.2f})")
    print("-" * 65)
    print(" TEMPERATURE DISTRIBUTION:")
    for b_name, b_info in [
        ("Under 60°C (Cool)", buckets['under_60']),
        ("60°C - 70°C (Normal)", buckets['60_to_70']),
        ("70°C - 80°C (Warm)", buckets['70_to_80']),
        ("80°C - 85°C (Soft Throttle Zone)", buckets['80_to_85']),
        ("Over 85°C (Hard Throttle / Critical)", buckets['over_85'])
    ]:
        bar_len = int(b_info['pct'] / 2)
        bar = '#' * bar_len
        print(f"   {b_name:<35} : {b_info['count']:5d} ({b_info['pct']:5.2f}%) {bar}")
    print("-" * 65)
    print(" TOP PROCESSES (Most frequently observed top CPU consumer):")
    for proc, count in top_procs:
        pct_of_time = (count / stats['count']) * 100
        print(f"   {proc:<20} : {count:5d} samples ({pct_of_time:5.1f}%)")
    print("-" * 65)
    print(" FINAL VERDICT & RECOMMENDATION:")
    print(f"   => {verdict['decision']}")
    print("   Reasons:")
    for r in verdict['reasons']:
        print(f"      - {r}")
    print("=" * 65)


if __name__ == '__main__':
    main()
