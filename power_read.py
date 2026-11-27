#!/usr/bin/env python3
"""PMIC rail power reading for the PiStats project.

Parses the raw output of ``vcgencmd pmic_read_adc``.  A rail only contributes
power when it has at least one amps reading *and* at least one volts reading:

    watts(rail) = max(voltage readings) * sum(current readings)

Rails that are voltage-only (including the explicitly excluded EXT5V_V and
BATT_V rails) are omitted, because they do not represent on-board consumption.
"""

import os
import subprocess
import sys

# These rails are measured for monitoring only; they must never be counted as
# board power, even when a (spurious) current reading is present.
EXCLUDE_VOLTAGE_ONLY_RAILS = {"EXT5V_V", "BATT_V"}


def parse_pmic_adc(text):
    """Return ``{rail_name: watts}`` parsed from raw pmic_read_adc text.

    Malformed lines are skipped, whitespace is arbitrary, exactly duplicated
    ``(rail, unit, value)`` triples are collapsed and multiple distinct current
    readings for one rail are summed (dual-phase rails).
    """
    rails = {}
    if not text:
        return rails

    seen = set()
    currents = {}
    voltages = {}

    for line in str(text).splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        rail, unit, raw_value = parts
        unit = unit.upper()
        if unit not in ("A", "V"):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue

        triple = (rail, unit, value)
        if triple in seen:
            continue
        seen.add(triple)

        if unit == "A":
            currents.setdefault(rail, []).append(value)
        else:
            voltages.setdefault(rail, []).append(value)

    for rail, amps in currents.items():
        if rail in EXCLUDE_VOLTAGE_ONLY_RAILS:
            continue
        if rail not in voltages:
            continue
        rails[rail] = max(voltages[rail]) * sum(amps)
    return rails


def sum_pmic_watts(source):
    """Sum rail watts from raw ADC text or from a ``parse_pmic_adc`` dict."""
    if isinstance(source, dict):
        return float(sum(float(v) for v in source.values()))
    return float(sum(parse_pmic_adc(source).values()))


def _adc_text(env=None):
    environ = os.environ if env is None else env
    fake = environ.get("FAKE_PMIC_ADC")
    if fake is not None:
        return fake
    try:
        proc = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def read_power(env=None):
    """Return a live power snapshot dict.

    ``{"rails": {...}, "pmic_w": float, "wall_w": float,
       "wall_factor": float}``

    The optional ``env`` mapping is mainly a test hook; by default the process
    environment is used (``FAKE_PMIC_ADC`` and the config's ``wall_factor``).
    """
    environ = os.environ if env is None else env
    rails = parse_pmic_adc(_adc_text(environ))
    pmic_w = float(sum(rails.values()))

    wall_factor = 1.0
    try:
        import pistats_config
        wall_factor = float(pistats_config.load_config().get("wall_factor", 1.0))
    except Exception:
        wall_factor = 1.0

    # Honour an explicit WALL_FACTOR override (used by the collector hooks).
    override = environ.get("WALL_FACTOR")
    if override not in (None, ""):
        try:
            wall_factor = float(override)
        except (TypeError, ValueError):
            pass
    if wall_factor <= 0:
        wall_factor = 1.0

    return {
        "rails": rails,
        "pmic_w": pmic_w,
        "wall_w": pmic_w * wall_factor,
        "wall_factor": wall_factor,
    }


if __name__ == "__main__":
    # Small helper used by collect.sh: read raw ADC text on stdin and print the
    # summed PMIC watts.  FAKE_PMIC_ADC still takes precedence when set.
    raw = os.environ.get("FAKE_PMIC_ADC")
    if raw is None:
        raw = sys.stdin.read()
    print(f"{sum_pmic_watts(raw):.6f}")
